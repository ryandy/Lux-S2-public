import sys
import time

from .board import Board
from .factory import Factory
from .mode_forge import ModeForge
from .util import C, log, log_init


# TODO: Determine ice/ore scarcity and modify relevant score multipliers
#       Also check current number of ore factories for each?

# TODO: bonus for placing near isolated friendly factory that is ice disadvantaged to nearby opp

# TODO: include ore_weighted in spawn score? A/B test?

def agent_early_setup(agent, board_step, obs, remainingOverageTime):
    start_time = time.time()
    player_id = int(agent.player[-1])
    log_init(board_step, player_id)

    # First step: have to bid on first factory placement.
    if board_step == 0:
        agent.factories_per_team = obs["board"]["factories_per_team"]
        agent.factories_left = obs["board"]["factories_per_team"]
        log(f'{agent.factories_per_team}v{agent.factories_per_team}')

    # First step of actual factory placement: save initial pool of water/metal.
    if board_step == 1:
        agent.metal_left = obs["teams"][agent.player]["metal"]
        agent.water_left = obs["teams"][agent.player]["water"]
        agent.place_first = obs["teams"][agent.player]["place_first"]

    board = Board.from_obs(
        obs, agent.player, board_step, agent.env_cfg, agent.strategy, skip_routes=True)

    # Opponent's turn to place a factory
    if board_step != 0 and board_step % 2 != int(agent.place_first):
        # (re-)(pre-)calculating here
        # Cache ice vulnerabilities for all cells
        cids = [cid for cid in range(48*48) if cid in agent._0score]
        cids.sort(key=lambda x: agent._0score[x], reverse=True)
        for cid in cids:
            cell = board.cell(0,0,cid=cid)
            _ = cell.get_all_ice_vulnerable_relative_cells(agent.strategy)
            if time.time() - start_time > 2.9:
                break
        return {}

    # Cache ice vuln cells for each factory
    agent.ice_vuln_factories = set()
    for factory in board.factories.values():
        factory._ice_vuln_relative = []
        factory._ice_vuln_covered = False
        if (C.AFTER_DEADLINE
            and board.player
            and board.opp
            and factory.player_id == board.opp.id):
            factory._ice_vuln_relative = (
                factory.cell().get_all_ice_vulnerable_relative_cells(agent.strategy))
            #log(f'VULN REL: {factory} {factory._ice_vuln_relative}')
            for player_factory in factory.board.player.factories():
                if player_factory.cell() in factory._ice_vuln_relative:
                    agent.ice_vuln_factories.add(player_factory)
                    factory._ice_vuln_covered = True
                    factory._ice_vuln_relative = []

    shortlist = (board.step < 2 * agent.factories_per_team - 1)
    spawn_cells = get_spawn_cells(agent, board, obs, shortlist=shortlist)
    forge_mult = 1
    if board_step > 0 and agent.forge_spawn_cell_ids:
        # Count opp and player forge positions
        # Compare to remaining known good forge cells
        rem_forge_count, player_forge_count, opp_forge_count = 0, 0, 0
        for factory in board.factories.values():
            if ModeForge.from_factory(0, factory):
                if factory.player_id == board.player.id:
                    player_forge_count += 1
                else:
                    opp_forge_count += 1
        for forge_spawn_cell_id in agent.forge_spawn_cell_ids:
            if any(spawn_cell.id == forge_spawn_cell_id for spawn_cell in spawn_cells):
                rem_forge_count += 1
        #log(f'rem={rem_forge_count}, p={player_forge_count}, o={opp_forge_count}')
        if ((player_forge_count + (rem_forge_count) // 2)
            < (opp_forge_count + (rem_forge_count + 1) // 2)):
            forge_mult = 2

    # Calculate spawn score for each possible spawn position
    cell_scores = []
    for spawn_cell in spawn_cells:
        spawn_score1 = get_spawn_score1(agent, board, spawn_cell, forge_mult)
        cell_scores.append((spawn_cell, spawn_score1))
        if board_step == 0:
            agent._0score[spawn_cell.id] = spawn_score1
    cell_scores.sort(key=lambda x: x[1], reverse=True)
    #log(f'scores: {cell_scores[:3]}')

    # Calculate vulns until we hit 5 seconds or so
    cell_scores2 = []
    for j, (spawn_cell, spawn_score1) in enumerate(cell_scores):
        vulns = None
        # No need to inspect ice security for final placement
        if (board.step != 2 * agent.factories_per_team
            and (j < 10 or time.time() - start_time < 2.9)):
            vulns = spawn_cell.get_all_ice_vulnerable_relative_cells(agent.strategy)
            vulns = [c for c in vulns if c._spawnable]
        spawn_score2 = get_spawn_score2(agent, spawn_cell, spawn_score1, vulns)
        cell_scores2.append((spawn_cell, spawn_score2))
    cell_scores = cell_scores2
    cell_scores.sort(key=lambda x: x[1], reverse=True)
    #log(f'scores: {cell_scores[:3]}')

    # Step0: Bidding
    if board_step == 0:
        bid = 0
        should_bid_value = should_bid(agent, cell_scores)
        if should_bid_value > 10:
            bid = 0
        if should_bid_value > 25:
            bid = 10  # 5?
        if should_bid_value > 50:
            bid = 20  # 15?
        if should_bid_value > 200:
            bid = 30  # 25?

        # If only 1 forge, or top spot (and not 2nd) is a forge, bump up the bid
        if (len(agent.forge_spawn_cell_ids) == 1
            or (agent.top_spawn_position_is_forge[0]
                and not agent.top_spawn_position_is_forge[1])
            or (agent.top_spawn_position_is_superforge[0]
                and not agent.top_spawn_position_is_superforge[1])):
            log(f'bid up: forge')
            bid = min(30, bid + 10)

        # Avoid bidding more than 10/factory
        if agent.factories_per_team == 2:
            bid = min(20, bid)

        if cell_scores[0][0].get_all_ice_vulnerable_relative_cells(agent.strategy):
            log(f'bid down: vulnerable')
            bid -= 5

        bid = 0

        # Never bid negative
        bid = max(0, bid)
        log(f'bid_value={should_bid_value}, bid={bid}')
        log(f'time={round(time.time()-start_time, 2)}s')
        return dict(faction='AlphaStrike', bid=bid)

    # Step1...N: Selection

    # Look for a cell with a high score that also prevents other high-value positions
    # Only want/need to re-order if the opponent still has a placement left
    if board.step < 2 * agent.factories_per_team:
        cell_score_deltas = []
        # up to 85 cells are effectively blocked from being chosen when a cell is selected
        # Make sure we look at at least 85+1 when looking for "second best" choices
        for cell, score in cell_scores[:85+1]:
            for cell2, score2 in cell_scores[:85+1]:
                # f1.cell().man_dist(f2.cell()) >= 7 # Ok to build another factory
                # f1.cell().man_dist(f2.cell()) <= 6 # Factory cannot be built
                if cell.man_dist(cell2) <= 6:
                    continue
                # score2 is the next best score if cell is chosen. Record the score delta.
                cell_score_deltas.append((cell, score-score2))
                break
        cell_score_deltas.sort(key=lambda x: x[1], reverse=True)
        #log(f'deltas: {cell_score_deltas[:3]}')
        cell_scores = cell_score_deltas

    #for i in range(3):
    #    get_spawn_score1(agent, board, cell_scores[i][0], forge_mult, verbose=True)

    best_cell_score = cell_scores[0]
    best_cell = best_cell_score[0]
    best_spawn_pos = (best_cell.x, best_cell.y)

    metal = agent.metal_left // agent.factories_left
    if metal % 10 != 0:
        if agent.metal_left >= metal + 10 - (metal % 10):
            metal += 10 - (metal % 10)
    #water = agent.water_left // agent.factories_left
    #if water % 10 != 0:
    #    if agent.water_left >= water + 10 - (water % 10):
    #        water += 10 - (water % 10)
    water = agent.water_left if agent.factories_left == 1 else 140

    agent.metal_left -= metal
    agent.water_left -= water
    agent.factories_left -= 1
    ret = dict(spawn=best_spawn_pos, metal=metal, water=water)

    log(f'time={round(time.time()-start_time, 2)}s')
    return ret


# Need lots of possible cells for step0, but only need the ~85 best or so after that
def get_spawn_cells(agent, board, obs, shortlist=False):
    spawn_cells = []
    for y in range(board.size):
        for x in range(board.size):
            cell = board.cell(x, y)
            if not obs["board"]["valid_spawns_mask"][x][y]:
                cell._spawnable = False
                continue
            cell._spawnable = True
            spawn_cells.append(cell)

    if board.step > 0 and shortlist:
        spawn_cells.sort(key=lambda x: agent._0score[x.id], reverse=True)
        count = max(100, len(spawn_cells)//2)
        spawn_cells = spawn_cells[:count]

    return spawn_cells


def get_spawn_score2(agent, spawn_cell, spawn_score1, vulns):
    ice_security_bonus = 0
    if vulns is None:
        pass
    elif vulns == []:
        ice_security_bonus = 5  # TODO: eventually 25ish?
    else:
        vuln_cell_score = max(agent._0score[c.id] for c in vulns)
        score_diff = spawn_score1 - vuln_cell_score
        if score_diff > 350:
            ice_security_bonus = 2  # TODO eventually 5ish?
    return spawn_score1 + ice_security_bonus


def get_spawn_score1(agent, board, spawn_cell, forge_mult, verbose=False):
    # Bonus for settling near an isolated opponent factory (only applicable after a few placements)
    iso_counter_bonus = 0
    if board.step > max(4, 2 * agent.factories_per_team - 4):
        for opp_factory in board.opp.factories():
            nearest_player_factory = opp_factory.cell().nearest_factory(board,
                                                                        player_id=board.player.id)
            dist = opp_factory.cell().man_dist(nearest_player_factory.cell())
            if dist >= 25 and spawn_cell.man_dist(opp_factory.cell()) < 15:
                iso_counter_bonus = 1

    # Bonus for settling far from any existing opp factory
    iso_bonus = 0
    nearest_opp_factory = (spawn_cell.nearest_factory(board, player_id=board.opp.id)
                           if board.opp
                           else None)
    if board.step >= 2 * agent.factories_per_team - 1:
        if nearest_opp_factory and spawn_cell.man_dist(nearest_opp_factory.cell()) >= 25:
            iso_bonus = 1

    # Determine the number of ice/ore/flat cells at various distances
    RADIUS = 15
    ice = [0] * RADIUS
    ore = [0] * RADIUS
    flat = [0] * RADIUS
    low = [0] * RADIUS
    for cell, center_dist in spawn_cell.radius_cells(max_radius=RADIUS):
        # Check if this cell is on top of the proposed or an existing factory
        dist = cell.man_dist_factory(spawn_cell)
        if dist <= 0 or cell.factory():
            continue

        # TODO need to figure out if/when/howmuch each thing counts relative to nearby factories
        if cell.ore != 0:
            ore[dist] += 1

        # Check if this position encroaches a friendly/opp position (hurting both):
        # TODO: In addition to skipping when the cell is closer to other factory, should we account
        #       for cells that this new factory will be closer to than other factory was? This
        #       effectively hurts the other factory by some amount (the dist they thought they had)
        close_to_existing_factory = False
        for factory_id, factory_dist in cell.factory_dists.items():
            factory = board.factory(factory_id)
            if (factory.player_id == board.player.id
                and dist >= factory_dist):
                close_to_existing_factory = True
            if (factory.player_id != board.player.id
                and dist > factory_dist
                and not spawn_cell in factory._ice_vuln_relative):
                close_to_existing_factory = True
        if close_to_existing_factory:
            continue

        # TODO: ignore/reduce if "behind" a (any?) existing factory
        # TODO: if resources are on other side of opp factory, they don't count?

        if cell.ice != 0:
            ice[dist] += 1
        #if cell.ore != 0:
        #    ore[dist] += 1
        if cell.rubble[0] == 0:
            flat[dist] += 1
        if cell.rubble[0] <= 19:
            low[dist] += 1

    ice1_dist = get_dist_to_nearest(ice, radius=4)
    ice2_dist = get_dist_to_nearest(ice, 2, radius=6)
    ore1_dist = get_dist_to_nearest(ore, radius=8)
    ore1_dist_long = get_dist_to_nearest(ore, radius=15)

    total_ice, total_ore, total_flat, total_low = sum(ice), sum(ore), sum(flat), sum(low)
    weight_ice, weight_ore, weight_flat, weight_low = 0, 0, 0, 0
    for i in range(1, RADIUS):
        cells_at_dist = get_cells_at_dist(i)
        decay = 1.05**(i-1)
        weight_ice += 12 * ice[i] / cells_at_dist / decay
        weight_ore += 12 * ore[i] / cells_at_dist / decay
        weight_flat += 12 * flat[i] / cells_at_dist / decay
        weight_low += 12 * low[i] / cells_at_dist / decay
        #log(f'{i} {low[i]} {weight_low} {cells_at_dist} {decay} {12 * low[i] /cells_at_dist / decay}')

    secure_other_factory_bonus = 0
    if board.step >= 3:
        for player_factory in board.player.factories():
            vuln_cells = player_factory.cell().get_all_ice_vulnerable_relative_cells(agent.strategy)
            if vuln_cells:
                # If all vuln cells are close-ish to spawn_cell
                all_close = True
                for vuln_cell in vuln_cells:
                    if vuln_cell.man_dist(spawn_cell) >= 7:
                        all_close = False
                        break
                if all_close:
                    #log(f'secure {player_factory}{player_factory.cell()} by placing at {spawn_cell}')
                    secure_other_factory_bonus = 1

    # Note: ice vuln count does not include ice vulns _against_ me
    ice_vuln_count = len(agent.ice_vuln_factories)
    max_ice_vuln_count = 2 if agent.factories_per_team == 5 else 1
    if agent.factories_per_team == 5 and agent.place_first == False:
        max_ice_vuln_count = 3
    if agent.factories_per_team == 4 and agent.place_first == False:
        max_ice_vuln_count = 2
    if agent.factories_per_team == 3 and agent.place_first == False:
        max_ice_vuln_count = 2
    remaining_factories = agent.factories_per_team - ((board.step - 1) // 2)

    # Check for ice conflicts if one of last factory placements and not too many conflicts yet
    # Always check for possible conflict placement for last factory - reduce bonus if over limit
    ice_conflict_bonus = 0
    desperate_ice_conflict_bonus = 0
    if ((ice_vuln_count + remaining_factories <= max_ice_vuln_count)
        or (board.step >= 2 * agent.factories_per_team - 1)):
        for opp_factory in board.opp.factories():
            spawn_dist = spawn_cell.man_dist(opp_factory.cell())
            if (spawn_dist <= 12
                and nearest_opp_factory is opp_factory
                and spawn_cell in opp_factory._ice_vuln_relative):
                # The problem with having a nearby opp factory is that that factory will soon have
                # 2 heavies and your factory will only have 1 non-contiguous ice cell.

                opp_factory_dist = C.UNREACHABLE  # Min dist from opp_factory to other_opp_factory
                player_factory_dist = C.UNREACHABLE  # Min dist from opp_factory to other_own_factory
                min_existing_dist = C.UNREACHABLE  # Same as above?

                for other_factory_id, man_dist in opp_factory.cell().factory_dists.items():
                    other_factory = board.factories[other_factory_id]
                    if other_factory.player_id == board.opp.id and 0 < man_dist < opp_factory_dist:
                        opp_factory_dist = man_dist
                    if other_factory.player_id == board.player.id:
                        existing_dist = other_factory.cell().man_dist(opp_factory.cell())
                        if existing_dist < min_existing_dist:
                            min_existing_dist = existing_dist
                        if man_dist < player_factory_dist:
                            player_factory_dist = man_dist

                ore1, ore3, ice1 = 0, 0, 0
                for neighbor, man_dist in opp_factory.radius_cells(3):
                    if man_dist == 1 and neighbor.ore:
                        ore1 = 1
                    if man_dist == 1 and neighbor.ice:
                        ice1 = 1
                    if neighbor.ore:
                        ore3 = 1
                ore1_bonus = int(ore1 and ice1)
                ore3_bonus = 0 if ore1_bonus else int(ore3 and ice1)

                small_map_bonus = 0
                if agent.factories_per_team <= 3 and opp_factory_dist >= 15:
                    small_map_bonus = 1

                # TODO probably want to re-assess these dist qualifiers
                opp_factory_dist = min(35, opp_factory_dist)
                player_factory_dist = min(35, player_factory_dist)
                desperate_ice_conflict_bonus = (1
                                                + 0.0025 * opp_factory_dist
                                                - 0.0015 * player_factory_dist
                                                + (0.08 - 0.01 * spawn_dist)
                                                + 0.1 * ore1_bonus
                                                + 0.05 * ore3_bonus
                                                + 0.1 * small_map_bonus)

                if opp_factory_dist >= 15 and player_factory_dist < 35 and min_existing_dist > 8:
                    ice_conflict_bonus = (1
                                          + 0.0025 * opp_factory_dist
                                          - 0.0015 * player_factory_dist
                                          + (0.08 - 0.01 * spawn_dist)
                                          + 0.1 * ore1_bonus
                                          + 0.05 * ore3_bonus
                                          + 0.1 * small_map_bonus)

                # Allow over-limit ice conflicts for last placement, but reduce bonus given
                if ice_vuln_count + remaining_factories > max_ice_vuln_count:
                    desperate_ice_conflict_bonus /= 2
                    ice_conflict_bonus /= 2
                elif opp_factory_dist < 10:
                    desperate_ice_conflict_bonus /= 2

    # Don't make a crazy play at an ice conflict picking 3rd out of 4.
    if agent.factories_per_team == 2 and board.step == 3:
        desperate_ice_conflict_bonus = min(0.1, desperate_ice_conflict_bonus)

    ice1_bonus = 1 if ice1_dist == 1 else 0
    ore1_bonus = 1 if ore1_dist == 1 else 0
    forge_bonus = 1 if (ice1_dist == 1 and ore1_dist <= 3) else 0
    if ice1_bonus or ice_conflict_bonus:
        desperate_ice_conflict_bonus = 0

    desperate_ice_dist = 0
    if (board.step >= 3
        and not ice1_bonus
        and not ice_conflict_bonus
        and not desperate_ice_conflict_bonus):
        # dist to furthest ice1 at nearest opp factory
        # Should exist except for buggy opponents
        if nearest_opp_factory:
            min_opp_ice_dist = None
            max_own_ice_dist = 20
            for ice_cell, opp_ice_dist in nearest_opp_factory.radius_cells(20):
                if not ice_cell.ice:
                    continue
                if min_opp_ice_dist is None:
                    min_opp_ice_dist = opp_ice_dist
                    max_own_ice_dist = 0
                elif opp_ice_dist > min_opp_ice_dist:
                    break
                own_ice_dist = spawn_cell.man_dist(ice_cell)

                # Discourage picking on an opp factory that is hovering near my own low-ice factory
                nearest_own_factory = (ice_cell.nearest_factory(board, player_id=board.player.id)
                                       if board.player
                                       else None)
                if (nearest_own_factory
                    and ice_cell.man_dist_factory(nearest_own_factory) == 1):
                    own_dist1_ice = [c for c in nearest_own_factory.neighbors() if c.ice]
                    if len(own_dist1_ice) <= 1:
                        own_ice_dist += 15

                if own_ice_dist > max_own_ice_dist:
                    max_own_ice_dist = own_ice_dist
            desperate_ice_dist = max_own_ice_dist
            if nearest_opp_factory._ice_vuln_covered:
                desperate_ice_dist += 10

    features = [
        ('ice1_bonus', ice1_bonus, 300),
        ('ore1_bonus', ore1_bonus, 25),
        ('ice1_dist', ice1_dist, -6),
        ('ice2_dist', ice2_dist, -3.5),
        ('ore1_dist', ore1_dist, -5.5),
        ('ore1_dist_long', ore1_dist_long, -1.5),
        #('flat1_count', flat[1], 0.2),
        ('low_weighted', min(50, weight_low), 2.5),
        ('low_weighted_full', weight_low, 0.01),  # Tie breaker for above
        #('ice_weighted', weight_ice, 0.5),
        ('ore_weighted', weight_ore, 0.5),
        ('forge_bonus', forge_bonus, 10*forge_mult),
        ('iso_bonus', iso_bonus, 10),
        ('iso_counter_bonus', iso_counter_bonus, 10),
        ('secure_other_factory_bonus', secure_other_factory_bonus, 3),  # TODO: eventually 20ish?
        ('ice_conflict_bonus', ice_conflict_bonus, 500),
        ('desperate_ice_conflict_bonus', desperate_ice_conflict_bonus, 300),
        ('desperate_ice_dist', desperate_ice_dist, -100),
    ]
    score = 1000
    for (_, v, w) in features:
        score += v * w

    if verbose:
        log(f'{spawn_cell.x},{spawn_cell.y}:')
        for (name, v, w) in features:
            log(f'{name} {v:.2f} * {w:.1f}')
        log(f'total {score:.2f}')

    return score


def get_cells_at_dist(dist):
    return 4 * (3 + (dist-1))


# Effectively accomplished by resource_routes
def get_dist_to_nearest(resource, nearest=1, radius=4):
    '''
    resource is dist list of a resource
    '''
    count = 0
    for dist in range(1, radius):
        count += resource[dist]
        if count >= nearest:
            return dist
    return radius + 4


def should_bid(agent, cell_scores):
    # calculate score_deltas for each remaining cell_score
    # select cell associated with maximum score_delta
    # record score_delta
    # filter out affected cells from cell_scores based on proximity to selected cell
    # recalculate scores? I think no for now
    agent.forge_spawn_cell_ids = []
    agent.top_spawn_position_is_forge = []
    agent.top_spawn_position_is_superforge = []

    all_score_deltas = []
    for _ in range(2 * (agent.factories_per_team + 1)):  # Do 1 extra per team (looking for forges)
        cell_score_deltas = []
        for cell, score in cell_scores[:85+1]:
            for cell2, score2 in cell_scores[:85+1]:
                if cell.man_dist(cell2) <= 6:
                    continue
                # score2 is the next best score if cell is chosen. Record the score delta.
                cell_score_deltas.append((cell, score-score2))
                break
        cell_score_deltas.sort(key=lambda x: x[1], reverse=True)
        best_cell, best_score_delta = cell_score_deltas[0]
        all_score_deltas.append(best_score_delta)

        #log(f'bid score details ->')
        #get_spawn_score1(agent, best_cell.board, best_cell, 1, verbose=True)

        # Determine if best_cell is a forge. Need to gauge forge scarcity.
        ice1, ore1, ore3, flat1 = False, False, False, False
        for cell, dist in best_cell.radius_cells_factory(3):
            if cell.factory():
                continue
            if dist == 1 and cell.ice:
                ice1 = True
            if dist == 1 and cell.ore:
                ore1 = True
            if cell.ore:
                ore3 = True
            if dist == 1 and cell.rubble[0] == 0 and cell.lowland_size >= 10:
                flat1 = True

        # "forge"
        if ice1 and ore3:
            agent.forge_spawn_cell_ids.append(best_cell.id)
            agent.top_spawn_position_is_forge.append(True)
        else:
            agent.top_spawn_position_is_forge.append(False)

        # "superforge"
        if ice1 and ore1 and flat1:
            agent.top_spawn_position_is_superforge.append(True)
        else:
            agent.top_spawn_position_is_superforge.append(False)

        cell_scores = [cell_score for cell_score in cell_scores
                       if cell_score[0].man_dist(best_cell) >= 7]

    max_diff = 0
    for i, score_delta in enumerate(all_score_deltas[:(2*agent.factories_per_team)]):
        #log(f'should_bid? score_delta{i} = {score_delta:.2f}')
        if i % 2 == 0:
            # score_delta represents the bump player 0 gets for picking here instead of next
            max_diff = max(max_diff, score_delta)
    #log(f'max_diff  : {max_diff:.2f}')
    return max_diff
