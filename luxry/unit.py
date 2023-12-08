from collections import defaultdict
import math
import sys

from .cell import Cell
from .entity import Entity
from .util import C, Action, Direction, Resource, log, prandom, prandom_shuffle


def log_cond(unit):
    if C.PROD:
        return unit.role and unit.role.NAME == 'blockade'
        return False
    return False


class Unit(Entity):
    def __init__(self, board, unit_id, player_id, x, y, unit_type,
                 ice, ore, water, metal, power, action_queue):
        super().__init__()
        self.board = board
        self.id = unit_id
        self.id_str = f'unit_{unit_id}'
        self.player_id = player_id
        self.x = [x] + [None] * C.FUTURE_LEN
        self.y = [y] + [None] * C.FUTURE_LEN
        self.type = unit_type
        self.ice = [ice] + [0] * C.FUTURE_LEN
        self.ore = [ore] + [0] * C.FUTURE_LEN
        self.water = [water] + [0] * C.FUTURE_LEN
        self.metal = [metal] + [0] * C.FUTURE_LEN
        self.power = [power] + [0] * C.FUTURE_LEN

        self.cfg = board.env_cfg.ROBOTS[unit_type]
        self.role = None
        self.route = None
        self.assigned_factory = None

        self.low_power = False  # Relevant for step_idx=0 only for opp units
        self.low_power_threshold = None
        self.low_power_route = None

        self.protectors = [[]] + [[] for _ in range(C.FUTURE_LEN)]
        self.transporters = [[]] + [[] for _ in range(C.FUTURE_LEN)]

        # Used by Board::get_new_actions
        # This wouldn't be necessary if we had .power[] and .power_delta[]
        self.init_power = power
        self._mines = None  # cache of get_mines()
        self._is_antagonized = None  # cache of is_antagonized()
        self._is_chain = None  # cache of is_chain()

        self._raw_action_queue = list(action_queue)
        self.action_queue = Action.expand_queue(self, action_queue)
        self.new_action_queue = [None] * C.FUTURE_LEN

    def __repr__(self):
        return f'{self.type}{self.id}'

    def serialize(self):
        return ('u', self.id)

    def get_player(self):
        return self.board.get_player(self.player_id)

    def cell(self, step):
        i = step - self.board.step
        if i < 0:
            stats = self.stats()
            if not stats or (step < stats.init_step):
                log(f'Bad step {step} for unit {self}: {stats and stats.init_step}')
                assert False
            j = step - stats.init_step
            cid = stats.cell_ids[j]
            return self.board.cell(0,0,cid=cid)
        return self.board.cell(self.x[i], self.y[i])

    def stats(self):
        return (self.board.strategy.unit_stats[self.id]
                if self.id in self.board.strategy.unit_stats else None)

    def set_role(self, step, role=None):
        i = step - self.board.step

        lc = False
        if role:
            if self.role:
                lc = lc or log_cond(self)
                if i == 0 and lc:
                    log(f'X! {self} {self.role}')
                self.unset_role(step, lc=lc)
            self.role = role
            lc = lc or log_cond(self)
            if i == 0 and lc:
                log(f'   {self} {self.role}')
        self.role.set_role(step)

    def unset_role(self, step, lc=False):
        i = step - self.board.step

        lc = lc or log_cond(self)
        if i == 0 and lc:
            log(f'X  {self} {self.role}')
        self.role.unset_role(step)
        self.role = None

    def update_goal(self, step):
        i = step - self.board.step
        prev_goal = self.role.goal
        self.role.update_goal(step)
        if i == 0 and prev_goal is not self.role.goal and log_cond(self):
            log(f'.  {self} {self.role}')

    def set_protector(self, step, unit):
        i = step - self.board.step
        self.protectors[i].append(unit.id)

    def unset_protector(self, step, unit):
        i = step - self.board.step
        if unit.id in self.protectors[i]:
            self.protectors[i].remove(unit.id)

    def unset_role_for_protectors(self, step):
        i = step - self.board.step
        for protector_uid in self.protectors[i]:
            self.board.units[protector_uid].unset_role(step)

    def set_transporter(self, step, unit):
        i = step - self.board.step
        self.transporters[i].append(unit.id)

    def unset_transporter(self, step, unit):
        i = step - self.board.step
        if unit.id in self.transporters[i]:
            self.transporters[i].remove(unit.id)

    def unset_role_for_transporters(self, step):
        i = step - self.board.step
        for transporter_uid in self.transporters[i]:
            self.board.units[transporter_uid].unset_role(step)

    def steps_until_power(self, goal_power):
        goal_power = min(goal_power, self.cfg.BATTERY_CAPACITY)
        power_diff = goal_power - self.power[0]
        if power_diff <= 0:
            return 0

        total_day_steps = math.ceil(power_diff / self.cfg.CHARGE)
        full_days = total_day_steps // 30
        remainder_power = (total_day_steps % 30) * self.cfg.CHARGE

        power = 0
        additional_day_count = 0
        while power < remainder_power:
            power += self.power_gain(self.board.step + additional_day_count)
            additional_day_count += 1

        return 50 * full_days + additional_day_count

    def power_gain(self, step, end_step=None):
        # end_step is an exclusive end
        if end_step:
            return self._power_gain_multi(step, end_step)
        return (self.cfg.CHARGE  # day
                if step % self.board.env_cfg.CYCLE_LENGTH < self.board.env_cfg.DAY_LENGTH
                else 0)  # night

    def _power_gain_multi(self, step, end_step):
        # round step up to whole cycle
        # round end_step down to whole cycle
        # multiply 30 * charge for each whole day
        # iterate for beginning and end
        start_cycle = step + 50 - (step % 50)
        end_cycle = end_step - (end_step % 50)
        power = 0
        if start_cycle >= end_cycle:
            # Iterate from start to end
            for s in range(step, end_step):
                power += self.power_gain(s)
        else:
            # Iterate from start to start, end to end, add 30*x
            for s in range(step, start_cycle):
                power += self.power_gain(s)
            for s in range(end_cycle, end_step):
                power += self.power_gain(s)
            power += ((end_cycle - start_cycle) // 50) * 30 * self.cfg.CHARGE
        return power

    def action_queue_cost(self, step):
        return self.cfg.ACTION_QUEUE_POWER_COST

    def _need_action_queue_cost(self, step, action, direction, resource, amount):
        '''action_queue and new_action_queue match exactly up until step, at which point they differ
         AQ = [1, 1, 3]     len=3
        nAQ = [1, 1, 3, 2]  i=4
        nAQ = [1, 1, 3, None, None] i=5

        '''
        i = step - self.board.step

        # If the previous new action is a None-placeholder, then that means this turn we'll need AQ
        if (i > 0
            and Action.equal(self.new_action_queue[i-1], [Action.MOVE, Direction.CENTER, 1, 0, 0, 1])
            and step - self.last_action_step > 1):
            return True

        # New/old actions at position i are the same: no need to pay AQ cost at i.
        old_action = self.action_queue[i] if i < len(self.action_queue) else None
        if Action.equal(old_action, (action, direction, resource, amount, '_', '_')):
            return False

        # Actions at position i differ. If all preceding actions match, return True.
        queue_same = True
        for j in range(i-1, -1, -1):
            old_action = self.action_queue[j] if j < len(self.action_queue) else None
            if not Action.equal(old_action, self.new_action_queue[j]):
                queue_same = False
                break

        return queue_same

    def _register_no_move(self, step):
        i = step - self.board.step
        self.x[i+1], self.y[i+1] = self.x[i], self.y[i]
        self.cell(step+1).register_unit(step+1, self)

    def _neighbor_to_direction(self, step, neighbor_cell):
        i = step - self.board.step
        if neighbor_cell.y < self.y[i]:
            return Direction.NORTH
        if neighbor_cell.x > self.x[i]:
            return Direction.EAST
        if neighbor_cell.y > self.y[i]:
            return Direction.SOUTH
        if neighbor_cell.x < self.x[i]:
            return Direction.WEST
        return Direction.CENTER

    def _goal_to_direction(self, step, goal_cell):
        move_cell, threatening_units = self.goal_to_move(step, goal_cell)
        direction = self._neighbor_to_direction(step, move_cell)
        return direction, threatening_units

    def move_cost(self, step, direction):
        threatening_units = None
        if isinstance(direction, Cell):
            direction, threatening_units = self._goal_to_direction(step, direction)

        i = step - self.board.step
        move_delta = C.MOVE_DELTAS[direction]
        target_pos = (self.x[i] + move_delta[0], self.y[i] + move_delta[1])
        if (target_pos[0] < 0
            or target_pos[1] < 0
            or target_pos[1] >= self.board.size
            or target_pos[0] >= self.board.size):
            # TODO: Crash seen here: https://s2vis.lux-ai.org/#/?input=47065636
            #       With some logs:  https://s2vis.lux-ai.org/#/?input=47098359
            log(f'CrashA: {step} {self} {direction} {self.x[i]} {self.y[i]} {self.cell(step)}'
                f'{self.board.size} {move_delta} {target_pos} {target_pos[0] < 0} {target_pos[1] < 0}'
                f'{target_pos[1] >= self.board.size} {target_pos[0] >= self.board.size}')
            log(f'{self.cell(step).neighbor(*C.MOVE_DELTAS[direction])}')
            return 'rma', 0  # TODO this is just for debugging
            assert False

        target_cell = self.board.cell(target_pos[0], target_pos[1])

        cost = (0  # No movement
                if direction == 0
                else math.floor(self.cfg.MOVE_COST
                                + self.cfg.RUBBLE_MOVEMENT_COST * target_cell.rubble[i]))
        if self._need_action_queue_cost(step, Action.MOVE, direction, 0, 0):
            cost += self.action_queue_cost(step)
        return cost, direction, threatening_units

    def do_move(self, step, direction, force_no_move=False, move_cost=None, threatening_units=None):
        if force_no_move:
            assert direction == Direction.CENTER
        elif isinstance(direction, Cell):
            direction, threatening_units = self._goal_to_direction(step, direction)
        else:
            # If direction is a non-Cell, assert that move cost has been provided
            assert move_cost is not None

        # Update next position/power, and update affected cell
        i = step - self.board.step
        move_delta = C.MOVE_DELTAS[direction]
        self.x[i+1], self.y[i+1] = (self.x[i] + move_delta[0], self.y[i] + move_delta[1])
        self.cell(step+1).register_unit(step+1, self)

        if force_no_move:
            # Used when there is not enough power to update AQ for a no-move
            # NOTE: this is kinda gross, but we need to tell the entity that this None action is
            #       our actual move choice so that other moves are not considered later. This can
            #       cause issues with move logic because we already called cell.register_unit
            #       for this unit this step.
            self.last_action_step = step
            return None
        else:
            if move_cost is None:
                move_cost, _, threatening_units = self.move_cost(step, direction)
            if i == 0 and threatening_units:
                self.board.strategy.save_unit_threat(self, threatening_units)
            self.power[i] -= move_cost
            return [Action.MOVE, direction, 0, 0, 0, 1]

    def transfer_cost(self, step, direction, resource, amount):
        if isinstance(direction, Cell):
            direction = self._neighbor_to_direction(step, direction)

        cost = 0
        if self._need_action_queue_cost(step, Action.TRANSFER, direction, resource, amount):
            cost += self.action_queue_cost(step)
        return cost

    def do_transfer(self, step, direction, resource, amount, repeat=0, n=1):
        if isinstance(direction, Cell):
            direction = self._neighbor_to_direction(step, direction)
        assert Resource.MIN <= resource <= Resource.MAX
        assert Direction.MIN <= direction <= Direction.MAX
        assert isinstance(amount, int)

        # Update next resource/power, and update affected recipient factory/unit
        # TODO: enforce limits (and probably warn if overflowing)
        i = step - self.board.step
        cell = self.cell(step).neighbor(*C.MOVE_DELTAS[direction])
        recipient = cell.factory() if cell.factory() else cell.unit(step+1)
        assert recipient
        cargo_lim = recipient.cfg.CARGO_SPACE if isinstance(recipient, Unit) else C.UNREACHABLE
        power_lim = recipient.cfg.BATTERY_CAPACITY if isinstance(recipient, Unit) else C.UNREACHABLE
        # Transfer ice/ore to `step` so they can be processed into water/metal at end of turn
        # Transfer power to `step+1` so it cannot be used this turn (actions are validated at start)
        ice_ore_i = i if cell.factory() else i+1 # TODO: separate ice_delta etc arrays for unit/factory
        if resource == Resource.ICE:
            actual_amount = min(amount, self.ice[i])
            self.ice[i] -= actual_amount
            recipient.ice[ice_ore_i] += actual_amount
        elif resource == Resource.ORE:
            actual_amount = min(amount, self.ore[i])
            self.ore[i] -= actual_amount
            recipient.ore[ice_ore_i] += actual_amount
        elif resource == Resource.WATER:
            actual_amount = min(amount, self.water[i])
            self.water[i] -= actual_amount
            recipient.water[ice_ore_i] += actual_amount
        elif resource == Resource.METAL:
            actual_amount = min(amount, self.metal[i])
            self.metal[i] -= actual_amount
            recipient.metal[ice_ore_i] += actual_amount
        elif resource == Resource.POWER:
            actual_amount = min(amount, self.power[i])
            self.power[i] -= actual_amount
            recipient.power[i+1] += actual_amount
        self.power[i] -= self.transfer_cost(step, direction, resource, amount)
        self._register_no_move(step)

        return [Action.TRANSFER, direction, resource, amount, repeat, n]

    def pickup_cost(self, step, resource, amount):
        cost = 0
        if self._need_action_queue_cost(step, Action.PICKUP, 0, resource, amount):
            cost += self.action_queue_cost(step)
        return cost

    def do_pickup(self, step, resource, amount, repeat=0, n=1):
        assert Resource.MIN <= resource <= Resource.MAX
        assert isinstance(amount, int)

        # Update resource/power, and update affected factory
        i = step - self.board.step
        factory = self.cell(step).factory()
        if resource == Resource.ICE:
            actual_amount = min(amount, factory.ice[i])
            self.ice[i] += actual_amount
            factory.ice[i] -= actual_amount
        elif resource == Resource.ORE:
            actual_amount = min(amount, factory.ore[i])
            self.ore[i] += actual_amount
            factory.ore[i] -= actual_amount
        elif resource == Resource.WATER:
            actual_amount = min(amount, factory.water[i])
            self.water[i] += actual_amount
            factory.water[i] -= actual_amount
        elif resource == Resource.METAL:
            actual_amount = min(amount, factory.metal[i])
            self.metal[i] += actual_amount
            factory.metal[i] -= actual_amount
        elif resource == Resource.POWER:
            actual_amount = min(amount, factory.power[i])
            self.power[i] += actual_amount
            factory.power[i] -= actual_amount
        self.power[i] -= self.pickup_cost(step, resource, amount)
        self._register_no_move(step)

        return [Action.PICKUP, 0, resource, amount, repeat, n]

    def dig_cost(self, step):
        cost = self.cfg.DIG_COST
        if self._need_action_queue_cost(step, Action.DIG, 0, 0, 0):
            cost += self.action_queue_cost(step)
        return cost

    def do_dig(self, step, repeat=0, n=1):
        # Update next resource/power, and update affected cell
        i = step - self.board.step
        cell = self.cell(step)
        if cell.rubble[i] > 0:
            cell.rubble[i] -= min(self.cfg.DIG_RUBBLE_REMOVED, cell.rubble[i])
        elif cell.lichen[i]: #  TODO
            cell.lichen[i] -= min(self.cfg.DIG_LICHEN_REMOVED, cell.lichen[i])
            if cell.lichen[i] <= 0:
                cell.rubble[i] += self.cfg.DIG_RUBBLE_REMOVED
        elif cell.ice:
            self.ice[i] += self.cfg.DIG_RESOURCE_GAIN
        elif cell.ore:
            self.ore[i] += self.cfg.DIG_RESOURCE_GAIN

        self.power[i] -= self.dig_cost(step)
        self._register_no_move(step)

        return [Action.DIG, 0, 0, 0, repeat, n]

    def selfdestruct_cost(self, step):
        cost = self.cfg.SELF_DESTRUCT_COST
        if self._need_action_queue_cost(step, Action.SELF_DESTRUCT, 0, 0, 0):
            cost += self.action_queue_cost(step)
        return cost

    def do_selfdestruct(self, step, repeat=0, n=1):
        i = step - self.board.step
        # TODO: Also affects rubble/lichen/alive-ness
        self.power[i] -= self.selfdestruct_cost(step)
        self._register_no_move(step)
        return [Action.SELF_DESTRUCT, 0, 0, 0, repeat, n]

    def get_mines(self, past_steps=15, future_steps=10, ice=None):
        if self._mines is not None and past_steps == 15 and future_steps == 10:
            return [c for c in self._mines
                    if ice is None or (ice and c.ice) or (not ice and c.ore)]

        board = self.board
        mines = []
        stats = self.stats()
        if stats:
            #log(f'get_mines {self} {stats.mine_cell_id_steps}')
            for cell_id, past_step in reversed(stats.mine_cell_id_steps):
                if past_step <= board.step - past_steps:  # Note: not every step is in mine_cell list
                    break
                cell = board.cell(0,0,cid=cell_id)
                if ice is None or (ice and cell.ice) or (not ice and cell.ore):
                    mines.append(cell)

        cell = self.cell(board.step)
        for action_spec in self.action_queue[:future_steps]:
            if not cell:
                break
            action, direction, _, _, _, _ = list(action_spec)
            if action == Action.MOVE:
                move_delta = C.MOVE_DELTAS[direction]
                cell = cell.neighbor(*move_delta)
            elif action == Action.DIG:
                if cell.ice or cell.ore:
                    if ice is None or (ice and cell.ice) or (not ice and cell.ore):
                        mines.append(cell)

        # De-duplicate while maintaining order
        seen = set()
        mines = [c for c in mines if not (c in seen or seen.add(c))]

        if self._mines is None and past_steps == 15 and future_steps == 10 and ice is None:
            self._mines = list(mines)

        return mines

    # TODO: precalculate around init time?
    # TODO: check for invalid actions e.g. factory digs
    def future_route(self, max_len=20, dest_cell=None, dest_factory=None, ignore_repeat1=False):
        board = self.board
        cell = self.cell(board.step)
        route = [cell]
        prev_adr = (None, None, None)
        for action_spec in self.action_queue:
            action, direction, _, _, repeat, n = list(action_spec)
            if action == Action.MOVE and direction != Direction.CENTER:
                if (ignore_repeat1
                    and repeat == 1
                    and n == 1
                    and prev_adr == (action, direction, repeat, n)):
                    break
                cell = cell.neighbor(*C.MOVE_DELTAS[direction])
                prev_adr = (action, direction, repeat, n)
            else:
                prev_adr = (action, direction, repeat, n)
                continue

            # invalid move, cut the route off here
            if not cell or (cell.factory() and cell.factory().player_id != self.player_id):
                break

            route.append(cell)
            if (len(route) > max_len
                or (dest_cell and (cell is dest_cell))
                or (dest_factory and (cell.factory() is dest_factory))):
                break
        return route

    def future_cell(self, future_steps):
        board = self.board
        cell = self.cell(board.step)
        future_steps = min(future_steps, len(self.action_queue))
        for j, action_spec in enumerate(self.action_queue[:future_steps]):
            prev_cell = cell
            action, direction, _, _, _, _ = list(action_spec)
            if action == Action.MOVE:
                cell = cell.neighbor(*C.MOVE_DELTAS[direction])
            if not cell:
                cell, future_steps = prev_cell, j
                break

        return cell, future_steps

    def past_cell(self, past_steps):
        stats = self.stats()
        if not stats or len(stats.cell_ids) < past_steps + 1:  # len=1 -> 0 past; len=2 -> 1 past
            return None

        cell_id = stats.cell_ids[-(past_steps+1)]
        return self.board.cell(0,0,cid=cell_id)

    def is_retreating(self, step):
        board = self.board
        if step != board.step:
            return False

        future_cell, future_steps = self.future_cell(3)
        past_steps = 8 - future_steps
        past_cell = self.past_cell(past_steps)
        if not past_cell:
            return False

        # Must move some distance
        if past_cell.man_dist(future_cell) < 4:
            return False

        # Must be coming FROM the other player's factory region
        past_nearest_factory = past_cell.nearest_factory(board, player_id='all')
        if past_nearest_factory.player_id == self.player_id:
            return False

        # Must be going TO own player's factory region
        future_nearest_factory = future_cell.nearest_factory(board, player_id='all')
        if future_nearest_factory.player_id != self.player_id:
            return False

        # Must be getting meaningfully closer to own factory
        dist_delta = (future_cell.factory_dists[future_nearest_factory.id]
                      - past_cell.factory_dists[future_nearest_factory.id])
        if dist_delta > -4:
            return False

        return True

    def is_stationary(self, step, step_count):
        cur_cell = self.cell(step)
        for s in range(step-1, max(0, step-1-step_count), -1):
            if cur_cell.unit_history[s] != self.id:
                return False
        return True

    # TODO: remove step arg
    def is_antagonized(self, step):
        if self._is_antagonized is not None:
            return self._is_antagonized

        board = self.board
        self._is_antagonized = False

        stats = self.stats()
        if not stats or len(stats.cell_ids) < 6:
            return self._is_antagonized

        # If threatened by the same unit for >= 3/6 steps, return True
        # Possible but not guaranteed that list will be updated with self.board.step data
        threat_count = defaultdict(int)
        max_threat_count, max_threat_unit = 0, None
        for threat_unit_id, threat_step in reversed(stats.threat_unit_id_steps):
            if threat_step < self.board.step - 6:
                break
            if threat_unit_id in self.board.units:
                threat_count[threat_unit_id] += 1
                if threat_count[threat_unit_id] > max_threat_count:
                    max_threat_count = threat_count[threat_unit_id]
                    max_threat_unit = self.board.units[threat_unit_id]
        if max_threat_count >= 3:
            self._is_antagonized = max_threat_unit

        return self._is_antagonized

    def is_chain(self):
        board = self.board
        assert self.player_id == board.opp.id

        if self._is_chain is not None:
            return self._is_chain
        self._is_chain = False

        cell = self.cell(board.step)
        if (not self.type == 'LIGHT'
            or cell.factory()
            or len(self.action_queue) < 2):
            return False

        resource_count, power_count = 0, 0
        resource_dir, power_dir = None, None
        for action, direction, resource, amount, repeat, n in self.action_queue[:10]:
            if not ((action == Action.MOVE and direction == Direction.CENTER)
                    or action == Action.RECHARGE
                    or action == Action.TRANSFER):
                return False
            if action == Action.MOVE or action == Action.RECHARGE:
                continue
            if resource == Resource.POWER:
                power_count += 1
                if power_dir is None:
                    power_dir = direction
                elif power_dir != direction:
                    return False
            else:
                resource_count += 1
                if resource_dir is None:
                    resource_dir = direction
                elif resource_dir != direction:
                    return False

        self._is_chain = resource_count >= 2 and power_count >= 2 and resource_dir != power_dir
        return self._is_chain

    def goal_to_move(self, step, goal_cell):
        board = self.board
        i = step - board.step
        cur_cell = self.cell(step)

        # Check if previously determined route exists and is still valid.
        if self.route:
            route_dest_cell = self.route[-1]
            route_is_relevant = ((route_dest_cell is goal_cell)
                                 or (goal_cell.factory_center
                                     and route_dest_cell.factory() is goal_cell.factory()))
            if route_is_relevant and cur_cell in self.route:
                cur_i = self.route.index(cur_cell)
                next_i = min(cur_i + 1, len(self.route) - 1)
                move_cell = self.route[next_i]
                safe_from_friendly = move_cell.safe_to_move(step, self)
                safe_from_opp = not ((i == 0 or cur_cell.man_dist(goal_cell) <= 1)
                                     and self.threatened_by_opp(step, move_cell)[0])
                if safe_from_friendly and safe_from_opp:
                    #if i <= 1 and step < 30 and self.id == 9:
                    #    log(f'{step} {self} (.) {cur_cell} -> {move_cell} -> {goal_cell}')
                    return move_cell, None
                else:
                    #if (self.type == 'LIGHT' and self.role and self.id == 73):
                    #    log(f'{step} {self} (x) {cur_cell} -> ?? -> {goal_cell}; '
                    #        f'safe={safe_from_friendly}/{safe_from_opp}')
                    pass
            else:
                # TODO: I don't _think_ there's any benefit to deleting an outdated route
                #self.route = None
                #if (self.type == 'LIGHT' and self.role and self.id == 73):
                #    log(f'{step} {self} (x) {cur_cell} -> ?? -> {goal_cell}; '
                #        f'relevant={route_is_relevant}; route={self.route}')
                pass
        else:
            #if (self.type == 'LIGHT' and self.role and self.id == 73):
            #    log(f'{step} {self} (x) {cur_cell} -> ?? -> {goal_cell}; no route')
            pass

        best_score = (C.UNREACHABLE,)*6
        best_ideal_score = (C.UNREACHABLE,)*6
        best_move, best_route, best_threats = cur_cell, None, None  # Default: no move
        move_cell_options = cur_cell.neighbors()
        prandom_shuffle(step + self.id, move_cell_options)
        for move_cell in [cur_cell] + move_cell_options:
            # If move_cell is an opp factory cell, skip it.
            if move_cell.factory() and move_cell.factory().player_id != self.player_id:
                continue

            # If choosing move_cell could cause a collision, skip it.
            if not move_cell.safe_to_move(step, self):
                continue

            risk_value, threatening_units = self.threatened_by_opp(step, move_cell)

            # If player unit is taking cur_cell, increase risk_value if cannot afford this move_cell
            if (cur_cell.unit(step + 1)
                and (self.power[i]
                     < self.move_cost(step, self._neighbor_to_direction(step, move_cell))[0])):
                #if i == 0:
                #    log(f'{self} avoid {move_cell} due to low power and friendly move')
                risk_value += 1000

            # TODO: If we threaten an opp unit by moving to move_cell, that should contribute to score
            #       Particularly if currently at dist-2 relative to opp unit
            #       Below is the first step in that direction, but specifically for protected miners
            if (i == 0
                and move_cell.unit(step)
                and move_cell.unit(step).player_id != self.player_id
                and self.role
                and self.role.NAME == 'miner'
                and self.role.goal is self.role.resource_cell
                and self.protectors[i]):
                protector_id = self.protectors[i][0]
                protector = board.units[protector_id]
                if protector.role._should_strike(step):
                    log(f'{self} protected miner strike {move_cell}')
                    risk_value -= 1

            # If we can move directly to goal, do it.
            # Only applies to non-risky moves. Do not early exit for factory center destinations,
            # that's rarely where we actually want to go.
            if move_cell is goal_cell and risk_value <= 0 and not goal_cell.factory_center:
                #if (i == 0 and self.type == 'LIGHT' and self.role
                #    and self.role.get_factory() and self.role.get_factory().id == 7):
                #    log(f'{self} (B) {cur_cell} -> {move_cell} -> {goal_cell}')
                return move_cell, None

            # Use an even safer route sometimes e.g. for water transporters
            #   Stay at least 3-4 cells away from opp factories
            very_safe_avoid_cond = lambda s,c: (
                c.man_dist_factory(c.nearest_factory(board, player_id=board.opp.id)) <= 3
                or (c.assigned_unit(s)
                    and c.assigned_unit(s) is not self)
                or (c.unit(step) and c.unit(step).role and c.unit(step).role.NAME == 'blockade'))

            # Normal level of avoidance:
            #   Lights avoid factory centers, all assigned cells, and heavy opp miners
            #   Heavies avoid cells assigned to other heavies
            careful_avoid_cond = lambda s,c: (
                (self.type == 'LIGHT'
                 and (c.factory_center
                      # TODO this feels like an improvement, avoiding standoffs
                      #or any(((x.ice or x.ore or x.factory())
                      #        and x.unit(board.step)
                      #        and (x.unit(board.step).type == 'HEAVY'
                      #             or x.unit(board.step).power[0] > self.power[0])
                      #        and x.unit(board.step).player_id == board.opp.id)
                      #       for x in [c] + c.neighbors())))
                      or any((x.unit(board.step)
                              and (x.unit(board.step).type == 'HEAVY'
                                   or x.unit(board.step).power[0] > self.power[0])
                              and x.unit(board.step).player_id == board.opp.id
                              and x.unit(board.step).is_stationary(board.step, 5))
                             for x in [c] + c.neighbors())
                      or ((c.ice or c.ore)
                          and c.unit(board.step)
                          and c.unit(board.step).type == 'HEAVY'
                          and c.unit(board.step).player_id == board.opp.id)))
                or (c.assigned_unit(s)
                    and c.assigned_unit(s) is not self
                    and ((self.type == 'LIGHT'
                          and c.assigned_unit(s).role
                          and c.assigned_unit(s).role.NAME != 'pillager')
                         or c.assigned_unit(s).type == 'HEAVY'
                         or (c.assigned_unit(s).role
                             and c.assigned_unit(s).role.NAME == 'transporter'))))

            # Only avoid heavy miners when navigating less carefully.
            reckless_avoid_cond = lambda s,c: (
                c.assigned_unit(s)
                and c.assigned_unit(s) is not self
                and c.assigned_unit(s).role
                and ((c.assigned_unit(s).type == 'HEAVY'
                      and c.assigned_unit(s).role.NAME == 'miner')
                     or ((c.assigned_unit(s).role.NAME == 'transporter'
                          and c.assigned_unit(s).role.factory_cell.man_dist(
                              c.assigned_unit(s).role.destination.role.resource_cell) == 1))))

            # Endgame pillagers rush toward a tight cluster of cells around opp factories. Avoiding
            # assigned cells is almost impossible - also collisions in that area are ok.
            # Also skip right to reckless route if all destination neighbors are assigned/impassable.
            # Remember: reckless routes can end up "stuck" behind higher priority units
            skip_careful_route = False
            # TODO: pillagers should probably try careful route, but with dist lim
            #       otherwise they can easily get stuck behind other (low power) units
            #       Also: maybe avoid super-low-power units (<1 AQcost at night)
            if step >= C.END_PHASE and self.role and self.role.NAME == 'pillager':
                skip_careful_route = True
            elif (self.role
                  and self.role.NAME == 'blockade'
                  and self.role.goal is not self.role.factory):
                skip_careful_route = True
            elif all((c.assigned_unit(step)
                      or (c.factory() and c.factory().player_id != self.player_id))
                     for c in goal_cell.neighbors()):
                skip_careful_route = True

            dest_cell = None
            cost1, cost2, cost3, cost4 = (C.UNREACHABLE,) * 4
            if (self.role
                and self.role.NAME == 'water_transporter'
                and ((self.water[i] >= 5 or self.ice[i] >= 50)
                     or self.role.goal is self.role.target_factory)
                and not very_safe_avoid_cond(step, move_cell)):
                cost1, wt_dist, dest_cell = board.dist(
                    step, move_cell, self,
                    dest_cell=goal_cell,
                    avoid_cond=very_safe_avoid_cond)

                # If this route is overly roundabout and we may miss our deadline, go for faster route
                if ((self.water[i] >= 5 or self.ice[i] >= 50)
                    and wt_dist >= self.role.factory.water[i] - 2):
                    cost1 = C.UNREACHABLE

            if (cost1 == C.UNREACHABLE
                and not skip_careful_route
                and not careful_avoid_cond(step, move_cell)):
                cost2, _, dest_cell = board.dist(
                    step, move_cell, self,
                    dest_cell=goal_cell,
                    avoid_cond=careful_avoid_cond)

            # If goal_cost is unreachable, call again without avoiding assigned cells and use that
            # to break ties between various unreachable cost moves.
            unit_rubble_movement_cost = (0
                                         if (self.role
                                             and self.role.NAME == 'blockade'
                                             and self.role._straightline)
                                         else None)
            if (cost1 == cost2 == C.UNREACHABLE
                and not reckless_avoid_cond(step, move_cell)):
                cost3, _, dest_cell = board.dist(
                    step, move_cell, self,
                    dest_cell=goal_cell,
                    avoid_cond=reckless_avoid_cond,
                    unit_rubble_movement_cost=unit_rubble_movement_cost)

            # If goal_cost is unreachable, call again without avoiding assigned cells and use that
            # to break ties between various unreachable cost moves.
            if cost1 == cost2 == cost3 == C.UNREACHABLE:
                cost4, _, dest_cell = board.dist(
                    step, move_cell, self,
                    dest_cell=goal_cell)

            # Grab the route used by the last call to dist().
            route = board._route(dest_cell) if dest_cell else []

            # Calculate cost of moving 0-1 cells to get from self to move_cell.
            move_cost = 0 if cur_cell is move_cell else (
                self.cfg.MOVE_COST
                + math.floor(self.cfg.RUBBLE_MOVEMENT_COST * move_cell.rubble[i]))

            # When oscillating with an opp unit, give slight advantage to moving toward safety
            if ((self.role
                 and self.role.NAME == 'antagonizer'
                 and cur_cell is self.role.target_cell)
                or self.is_antagonized(self.board.step)):
                factory = self.assigned_factory or cur_cell.nearest_factory(self.board)
                move_cost += self.cfg.MOVE_COST * move_cell.man_dist_factory(factory)

            # Determine the best move_cell (small score/cost is better).
            ideal_score = (cost1, cost2, cost3, cost4, move_cost)
            score = (risk_value, cost1, cost2, cost3, cost4, move_cost)
            #if i == 0 and log_cond(self) and self.id == 17:
            #    log(f'{step} {self} {move_cell} {score} !{threatening_units}')
            if ideal_score < best_ideal_score:
                best_ideal_score, best_threats = ideal_score, threatening_units
            if score < best_score:
                best_score, best_move, best_route = score, move_cell, route

        self.route = best_route or []
        if i == 0 and best_score[0] > 0:
            log(f'{self} risky move to {best_move}: {best_score[0]}')

        #if i == 0 and log_cond(self) and self.id == 17:
        #   log(f'{self} {cur_cell} -> {best_move} -> {goal_cell}; score={best_score} !{best_threats}')
        return best_move, (best_threats if i == 0 else None)

    def _opp_collision_risk_value(self, step, opp_unit, move_cell):
        board = self.board
        i = step - board.step

        # TODO: is opp threatened by anything when moving to move_cell?
        #       this would discourage their move and decrease the risk to me
        #       beware an infinite loop of threatened_by_opp()

        # TODO: evaluate risks differently at i=0?
        #       risk is more well known at this point
        #       also more importantly we don't want to broadcast which risky choice we're leaning
        #       toward ahead of time. We basically always want to change the AQ when making a risky
        #       move choice.

        cur_cell = self.cell(step)
        opp_cell = opp_unit.cell(board.step)
        is_my_move = (cur_cell is move_cell)

        opp_threatened_at_cell = False
        if i == 0:
            opp_threatened_at_cell, _ = opp_unit.threatened_by_opp(step, move_cell)

        opp_just_at_cell = (move_cell.unit_history[board.step - 1] == opp_unit.id)

        opp_recently_at_cell = False
        for j in range(board.step - 2, board.step - 4, -1):
            if move_cell.unit_history[j] == opp_unit.id:
                opp_recently_at_cell = True

        opp_planning_to_move = False
        if len(opp_unit.action_queue) >= 1:
            if opp_unit.action_queue[0][0] == Action.MOVE:
                direction = opp_unit.action_queue[0][1]
                opp_plan_cell = opp_cell.neighbor(*C.MOVE_DELTAS[direction])
            else:
                opp_plan_cell = opp_cell
            if opp_plan_cell is move_cell:
                opp_planning_to_move = True

        risk_value = ((50 if (not is_my_move and self.type == opp_unit.type) else 0)
                      + (100 if opp_just_at_cell else 0)
                      + ( 20 if opp_recently_at_cell else 0)
                      + (100 if opp_planning_to_move else 0)
                      + (-90 if opp_threatened_at_cell else 0))
        return max(5, risk_value)

    def threatened_by_opp(self, step, move_cell, all_collisions=False):
        '''Returns a threat value: 0 for perfectly safe, higher for more danger'''
        i = step - self.board.step
        if move_cell.factory() and move_cell.factory().player_id == self.player_id:
            return 0, []

        risk_value = 0
        threatening_units = []
        cur_cell = self.cell(step)
        is_my_move = cur_cell is not move_cell

        for neighbor in [move_cell] + move_cell.neighbors():
            unit = neighbor.unit(self.board.step)
            if unit and unit.player_id != self.player_id:
                # No threat from lighter unit
                if (self.type == 'HEAVY' and unit.type == 'LIGHT') and not all_collisions:
                    continue

                # No threat if they can't make the move
                is_opp_move = neighbor is not move_cell
                move_cost = (0 if not is_opp_move
                             else math.floor(unit.cfg.MOVE_COST
                                             + move_cell.rubble[i] * unit.cfg.RUBBLE_MOVEMENT_COST))
                if unit.power[0] < move_cost:
                    continue

                # If antagonizing a chain, ignore threats from the targeted chain and miner
                if (self.role
                    and self.role.NAME == 'antagonizer'
                    and self.role.chain
                    and self.role.factory
                    and self.role.factory.mode
                    and self.role.factory.mode.NAME != 'ice_conflict'
                    and (unit.is_chain()
                         or (unit.type == 'HEAVY'
                             and (neighbor.ice or neighbor.ore)
                             and len(unit.action_queue) >= 1
                             and unit.action_queue[0][0] in (Action.DIG, Action.TRANSFER)))):
                    continue

                # Threat from heavier unit
                if self.type == 'LIGHT' and unit.type == 'HEAVY':
                    threatening_units.append(unit)
                    if (i == 0
                        and self.player_id == self.board.player.id
                        and is_my_move
                        and is_opp_move
                        and not all_collisions):
                        standoff_turns = self._is_standoff(step, unit)
                        # steps:  0  1  2    3   4    5    6    7    8+
                        chance = [0, 0, 0, 0.1, 0.3, 0.5, 0.7, 0.9, 0.9][min(8, standoff_turns)]
                        if prandom(step + self.id, chance):
                            log(f'{self} OK to break standoff with {unit}; {standoff_turns}')
                            continue  # Continue to next possible threat
                    risk_value += self._opp_collision_risk_value(step, unit, move_cell)
                    continue

                # An opp used to be on this neighbor of move_cell
                # If I am currently at that neighbor:
                # It's likely safe to move (continue to next possible threat) and unsafe to stand.
                if neighbor is cur_cell:
                    if move_cell is cur_cell:  # Standing here likely dangerous (opp nearby somewhere)
                        risk_value += 1000
                        threatening_units.append(unit)
                    continue  # Continue to next possible threat

                # Threat from same weight, I don't move
                if is_opp_move and not is_my_move:
                    risk_value += self._opp_collision_risk_value(step, unit, move_cell)
                    threatening_units.append(unit)
                    continue

                # Potential threat from same weight, we both move
                if is_opp_move and is_my_move:
                    # Can't predict their power so use time-equivalent power for own unit
                    # When idx=0, consider AQ cost
                    my_power, opp_power = self.power[0], unit.power[0]
                    if i == 0:
                        my_direction = self._neighbor_to_direction(step, move_cell)
                        opp_direction = unit._neighbor_to_direction(step, move_cell)
                        if not (self.action_queue
                                and self.action_queue[0]
                                and self.action_queue[0][0] == Action.MOVE
                                and self.action_queue[0][1] == my_direction):
                            my_power -= self.cfg.ACTION_QUEUE_POWER_COST
                        if not (unit.action_queue
                                and unit.action_queue[0]
                                and unit.action_queue[0][0] == Action.MOVE
                                and unit.action_queue[0][1] == opp_direction):
                            opp_power -= unit.cfg.ACTION_QUEUE_POWER_COST
                    if (opp_power > my_power) or all_collisions:
                        # Check recent history for each unit:
                        # If this situation has occurred multiple times recently, determine the
                        # likely opp move, then determine if this risk is likely to occur
                        # Only do this extra work when i==0, this would always require an AQ update
                        threatening_units.append(unit)
                        if (i == 0
                            and self.player_id == self.board.player.id
                            and not all_collisions):
                            standoff_turns = self._is_standoff(step, unit)
                            # steps:  0  1  2  3   4    5     6    7    8+
                            chance = [0, 0, 0, 0, 0.1, 0.25, 0.5, 0.5, 0.7][min(8, standoff_turns)]
                            if prandom(step + self.id, chance):
                                log(f'{self} OK to break standoff with {unit}; {standoff_turns}')
                                continue  # Continue to next possible threat
                        risk_value += self._opp_collision_risk_value(step, unit, move_cell)
                        continue
        return risk_value, threatening_units

    def _is_standoff(self, step, opp_unit):
        assert step == self.board.step

        # Exit early if we've already determined is_antagonized to be False
        if self._is_antagonized == False:
            return 0

        # Opp needs to plan to be stationary (or no plan)
        # TODO: This can be easily tricked by AQ lies
        if (opp_unit.action_queue
            and opp_unit.action_queue[0]
            and opp_unit.action_queue[0][0] == Action.MOVE
            and opp_unit.action_queue[0][1] != Direction.CENTER):
            return 0

        stats = self.stats()
        if not stats or len(stats.cell_ids) <= 3:
            return 0

        max_standoff = 0
        opp_cell = opp_unit.cell(step)
        for jump in [1, 2]:
            # Count number of consecutive steps that opp_unit has threatened self
            prev_step = step - jump
            standoff_count = 0
            for threat_unit_id, threat_step in reversed(stats.threat_unit_id_steps):
                # Allow unit to oscillate, but opp_unit must be stationary
                # TODO: allow both to oscillate
                if threat_step == prev_step and threat_unit_id == opp_unit.id:
                    # Only a standoff if they're standing
                    # Otherwise we're being chased? (TODO?)
                    if opp_unit.cell(prev_step) is not opp_cell:
                        break
                    if jump == 2:
                        if opp_unit.cell(prev_step+1) is not opp_cell:
                            break
                    standoff_count += 1
                    prev_step -= jump
                elif threat_step < prev_step:
                    break
            if standoff_count > max_standoff:
                max_standoff = standoff_count

        return max_standoff

    def update_low_power_flag(self, step):
        self.low_power = False
        self.low_power_route = None

        board = self.board
        i = step - board.step
        unit_power = self.power[i]

        if (unit_power >= self.cfg.BATTERY_CAPACITY // 2
            or (self.role and self.role.NAME == 'recharge')
            or (self.role and self.role._goal_is_factory())):
            return

        cur_cell = self.cell(step)
        if not self.role:
            goal_cell = None
        elif self.role.NAME == 'blockade':
            # Avoid calling blockade's goal_cell function early, it's complicated and order can matter
            goal_cell = (self.role.target_unit.cell(board.step)
                         if self.role.target_unit
                         else cur_cell)
        else:
            goal_cell = self.role.goal_cell(step)

        if goal_cell and goal_cell.factory():
            return

        # Possible to need to update goal to factory when standing on factory
        factory = self.assigned_factory or cur_cell.nearest_factory(board, player_id=self.player_id)
        is_player = (self.player_id == board.player.id)
        if not is_player and cur_cell.factory() is factory:
            return

        baseline_power, do_something_cost = 0, 0
        if is_player:
            baseline_power = 3 * self.cfg.MOVE_COST + self.cfg.ACTION_QUEUE_POWER_COST

            # Analyze the cost of DOING SOMETHING this turn and returning to factory NEXT TURN.
            do_something_cost = self.cfg.DIG_COST
            if self.role:
                # TODO: check if threatened at goal cell
                if goal_cell is not cur_cell:
                    next_cell = cur_cell.neighbor_toward(goal_cell)
                    do_something_cost = math.floor(self.cfg.MOVE_COST
                                                   + (self.cfg.RUBBLE_MOVEMENT_COST
                                                      * next_cell.rubble[i]))
                    if not next_cell.factory():
                        cur_cell = next_cell
        else:
            assert i == 0

        # Without checking specifically where the unit is going, assume that if they have no plans
        # to move, they're going to have to pay to update the queue at least once.
        naive_aq_cost = 0
        if (not self.action_queue
            or not self.action_queue[0]
            or not self.action_queue[0][0] == Action.MOVE
            or self.action_queue[0][1] == Direction.CENTER):
            naive_aq_cost = self.cfg.ACTION_QUEUE_POWER_COST

        man_dist = cur_cell.man_dist_factory(factory)
        naive_power_threshold = board.naive_cost(step, self, cur_cell, factory.cell(), is_factory=True)
        # Include power gain for this turn + step+man_dist-1 movement turns (therefore step+man_dist)
        end_step = step + man_dist if is_player else step + man_dist - 1
        naive_power_gain = self.power_gain(step, end_step=end_step)
        if (unit_power - do_something_cost + naive_power_gain
            < baseline_power + naive_power_threshold + naive_aq_cost):
            power_threshold, dist, factory_dest_cell = board.dist(
                step, cur_cell, self,
                dest_cell=factory.cell())
            end_step = step + dist if is_player else step + dist - 1
            power_gain = self.power_gain(step, end_step=end_step)
            if (unit_power - do_something_cost + power_gain
                < baseline_power + power_threshold + naive_aq_cost):
                self.low_power = True
                self.low_power_threshold = power_threshold
                self.low_power_route = self.board._route(factory_dest_cell)
                assert self.low_power_route[-1].factory()
                if cur_cell.factory() is not factory:
                    # Can't go from not-factory to factory if we're already at factory
                    assert not self.low_power_route[-2].factory()
                #if i == 0 and self.player_id == self.board.player.id and self.type == 'LIGHT':
                #    log(f'{self} low power {dist} {factory_dest_cell}')# {self.low_power_route}')
