import math
import sys

from .cell import Cell
from .role import Role
from .util import Action, Direction, Resource, C, log, profileit

'''
1ic: 0,4,6,7,8,9
2ic: 1,3
'''
class RoleBlockade(Role):
    '''Prevent opp units with water from reaching ice conflict target factory'''
    NAME = 'blockade'

    def __init__(self, step, unit, factory, target_unit, target_factory, partner,
                 last_transporter_factory_id=None,
                 last_transporter_step=None,
                 goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.target_unit = target_unit
        self.target_factory = target_factory
        self.partner = partner

        if target_unit is None and goal is None and last_transporter_factory_id is not None:
            self.goal = None
        else:
            self.goal = goal or factory or 'temp invalid'

        self.last_transporter_factory_id = (
            last_transporter_factory_id
            if last_transporter_factory_id is not None
            else target_unit.stats().last_factory_id)
        self.last_transporter_step = last_transporter_step or step

        self._target_route = None
        self._goal_cell = None
        self._is_primary = None
        self._push = None
        self._avoid = None
        self._next_goals = []
        self._force_direction = None
        self._straightline = None

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        tgoal = '*' if self.goal is self.target_unit else ''
        tfrom = f'({self.last_transporter_factory_id})'
        return f'Blockade[{self.factory}{fgoal} -> {self.target_unit}{tfrom}{tgoal} w/ {self.partner}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.target_unit),
                self.serialize_obj(self.last_transporter_factory_id),
                self.serialize_obj(self.last_transporter_step),
                self.serialize_obj(self.target_factory),
                self.serialize_obj(self.partner),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        target_unit = cls.deserialize_obj(unit.board, role_data[2])
        last_transporter_factory_id = cls.deserialize_obj(unit.board, role_data[3])
        last_transporter_step = cls.deserialize_obj(unit.board, role_data[4])
        target_factory = cls.deserialize_obj(unit.board, role_data[5])
        partner = cls.deserialize_obj(unit.board, role_data[6])
        goal = cls.deserialize_obj(unit.board, role_data[7])
        return RoleBlockade(step, unit, factory, target_unit, target_factory, partner,
                            last_transporter_factory_id=last_transporter_factory_id,
                            last_transporter_step=last_transporter_step,
                            goal=goal)

    def opp_cell(self):
        return (self.target_unit.cell(self.unit.board.step)
                if self.target_unit
                else self.unit.board.factories[self.last_transporter_factory_id].cell())

    def is_primary(self):
        if self._is_primary is not None:
            return self._is_primary

        self._is_primary = True
        if self.partner:
            step = self.unit.board.step
            cur_cell = self.unit.cell(step)
            par_cell = self.partner.cell(step)
            opp_cell = self.opp_cell()
            if (cur_cell.man_dist(par_cell) == 1
                and (cur_cell.man_dist(opp_cell) < 10
                     or par_cell.man_dist(opp_cell) < 10)):
                self._is_primary = cur_cell.man_dist(opp_cell) < par_cell.man_dist(opp_cell)
            else:
                self._is_primary = self.unit.id < self.partner.id
        return self._is_primary

    @classmethod
    @profileit
    def from_transition_block_water_transporter(cls, step, unit):
        board = unit.board
        i = step - board.step
        if i != 0 or unit.type != 'LIGHT':
            return

        if (unit.role
            and (unit.role.NAME == 'blockade'
                 or unit.role.NAME == 'water_transporter')):
            return

        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)
        if (not factory.mode
            or factory.mode.NAME != 'ice_conflict'
            or cur_cell.man_dist_factory(factory) >= 5):
            return

        blockades = [u for u in factory.units(step)
                     if (u.type == 'LIGHT'
                         and u.role
                         and u.role.NAME == 'blockade')]

        max_count = 2 if C.AFTER_DEADLINE else 1
        if len(blockades) >= max_count:
            return

        for opp_unit in board.opp.units():
            if (opp_unit.type != 'LIGHT'
                or (opp_unit.water[i] < 5
                    and not any((spec[0] == Action.PICKUP
                                 and spec[2] == Resource.WATER
                                 and spec[3] >= 5)
                                 for spec in opp_unit.action_queue[:5]))
                or opp_unit.cell(step).factory() is factory.mode.opp_factory):
                continue

            partner, already_blockaded = None, False
            for blockade in blockades:
                if blockade.role.target_unit is opp_unit:
                    if blockade.role.partner:
                        already_blockaded = True
                    else:
                        partner = blockade

            if not already_blockaded:
                if partner:
                    partner.role.partner = unit
                return RoleBlockade(step, unit, factory, opp_unit, factory.mode.opp_factory, partner)

    @classmethod
    @profileit
    def from_transition_block_different_water_transporter(cls, step, unit):
        board = unit.board
        i = step - board.step
        if (i != 0
            or unit.type != 'LIGHT'
            or not unit.role
            or not unit.role.NAME == 'blockade'):
            return

        # If in good position for current target, no transition
        cur_cell = unit.cell(step)
        opp_cell = unit.role.opp_cell()
        opp_dist = cur_cell.man_dist(opp_cell)
        if (unit.role.target_unit
            and unit.role.partner
            and unit.role.partner.role
            and unit.role.partner.role.NAME == 'blockade'):
            par_cell = unit.role.partner.cell(step)
            if (cur_cell.man_dist(par_cell) == 1
                and any(c.is_between(opp_cell, unit.role.target_factory.cell())
                        for c in cur_cell.neighbors() + par_cell.neighbors())
                and opp_dist <= 5):
                return

        factory = unit.assigned_factory or cur_cell.nearest_factory(board)
        blockades = [u for u in factory.units(step)
                     if (u.type == 'LIGHT'
                         and u.role
                         and u.role.NAME == 'blockade')]

        # TODO: do more to prefer real pickup vs AQ pickup?
        cur_target_route = None
        for opp_unit in board.opp.units():
            if (opp_unit.type != 'LIGHT'
                or opp_unit is unit.role.target_unit
                or (opp_unit.water[i] < 5
                    and not any((spec[0] == Action.PICKUP
                                 and spec[2] == Resource.WATER
                                 and spec[3] >= 5)
                                 for spec in opp_unit.action_queue[:5]))
                or opp_unit.cell(step).factory() is factory.mode.opp_factory):
                continue

            if unit.role.target_unit or True:
                # Need to compare opp_unit's projected route with unit.role.target_unit's
                if cur_target_route is None:
                    #opp_cell = unit.role.target_unit.cell(step)
                    #max_len = min(10, opp_cell.man_dist_factory(unit.role.target_factory))
                    #cur_target_route = unit.role.target_unit.future_route(
                    #    dest_factory=unit.role.target_factory, max_len=max_len, ignore_repeat1=True)
                    opp_cell = unit.role.opp_cell()
                    max_len = min(10, opp_cell.man_dist_factory(unit.role.target_factory))
                    cur_target_route = (
                        unit.role.target_unit.future_route(
                            dest_factory=unit.role.target_factory,
                            max_len=max_len,
                            ignore_repeat1=True)
                        if unit.role.target_unit
                        else [opp_cell])

                opp_cell = opp_unit.cell(step)
                max_len = min(10, opp_cell.man_dist_factory(unit.role.target_factory))
                opp_target_route = opp_unit.future_route(
                    dest_factory=unit.role.target_factory, max_len=max_len, ignore_repeat1=True)

                cur_start_dist = cur_target_route[0].man_dist_factory(unit.role.target_factory)
                cur_end_dist = cur_target_route[-1].man_dist_factory(unit.role.target_factory)
                cur_len = len(cur_target_route)
                cur_progress = cur_start_dist - cur_end_dist
                cur_progress_rate = 0 if (cur_len == 1) else int(100 * cur_progress / (cur_len - 1))

                opp_start_dist = opp_target_route[0].man_dist_factory(unit.role.target_factory)
                opp_end_dist = opp_target_route[-1].man_dist_factory(unit.role.target_factory)
                opp_len = len(opp_target_route)
                opp_progress = opp_start_dist - opp_end_dist
                opp_progress_rate = 0 if (opp_len == 1) else int(100 * opp_progress / (opp_len - 1))

            # Compare target routes
            # Which ends up closer to unit.role.target_factory?
            # Which makes more progress?
            #log(f'A {cur_target_route}')
            #log(f'B {cur_start_dist} {cur_end_dist} {cur_progress_rate}')
            #log(f'C {opp_target_route}')
            #log(f'D {opp_start_dist} {opp_end_dist} {opp_progress_rate}')
            #if (not unit.role.target_unit
            #    or (opp_end_dist <= cur_end_dist - 2
            if ((opp_end_dist <= cur_end_dist - 2
                 and opp_progress_rate >= cur_progress_rate)
                or (opp_progress_rate == 100
                    and cur_progress_rate <= 0
                    and opp_end_dist <= cur_end_dist
                    and opp_dist <= 20)):
                partner, already_blockaded = None, False
                for blockade in blockades:
                    if blockade.role.target_unit is opp_unit:
                        if blockade.role.partner:
                            already_blockaded = True
                        else:
                            partner = blockade

                if not already_blockaded:
                    if partner:
                        partner.role.partner = unit
                    log(f'{unit} transition blockade {unit.role.target_unit} -> {opp_unit}')
                    return RoleBlockade(
                        step, unit, factory, opp_unit, factory.mode.opp_factory, partner)

    # TODO: maybe invalidate if power is sufficiently less than target's?
    #       if opp is already within dist2 or so
    def is_valid(self, step):
        board = self.unit.board
        i = step - board.step

        if i > 0:
            return True

        # Drop partner if they get re-assigned
        if self.partner:
            if (not self.partner.role
                or self.partner.role.NAME != 'blockade'
                or self.partner.role.target_factory is not self.target_factory):
                self.partner = None

        target_is_valid = (self.factory
                           and self.target_factory
                           and self.target_unit
                           and (self.target_unit.water[i] >= 5
                                or any((spec[0] == Action.PICKUP
                                        and spec[2] == Resource.WATER
                                        and spec[3] >= 5)
                                       for spec in self.target_unit.action_queue[:5]))
                           and self.target_unit.cell(step).factory() is not self.target_factory)

        if target_is_valid:
            self.last_transporter_step = step
            if (self.target_unit.water[i] > self.target_unit.stats().prev_prev_water
                and self.target_unit.cell(step).factory()):
                self.last_transporter_factory_id = self.target_unit.cell(step).factory().id
        elif self.last_transporter_step == step - 1:
            if (self.factory
                and self.target_factory):
                #and self.target_unit
                #and ((self.target_unit.cell(step).man_dist_factory(self.target_factory) <= 1
                #      and self.last_transporter_factory_id != self.target_factory.id)
                # # There could be other units out there with water or picking it up
                #     or any(
                #         u for u in board.opp.units()
                #     ))):
                self.target_unit = None
                self.goal = self.factory
                log(f'{self.unit} blockade transition to anticipate')

        anticipation_is_valid = (
            self.factory
            and self.target_factory
            and self.last_transporter_factory_id in board.factories
            and step < self.last_transporter_step + 150
            and self.target_factory.water[i] < 150)

        return target_is_valid or anticipation_is_valid

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)
        #self.target_unit.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            #self.target_unit.unset_assignment(step, self.unit)

    def target_route(self, step):
        # Lazy load
        if self._target_route is not None:
            return self._target_route

        board = self.unit.board
        cur_cell = self.unit.cell(step)
        opp_cell = self.opp_cell()
        max_len = min(10, opp_cell.man_dist_factory(self.target_factory))
        target_route = ([opp_cell]
                        if not self.target_unit
                        else self.target_unit.future_route(
                                dest_factory=self.target_factory,
                                max_len=max_len,
                                ignore_repeat1=True))
        #log(f'{self.unit} blockade route A: {target_route}')

        # When the opp reaches the standoff, assume that they may reroute slightly
        # TODO: take into account threatened cell when calculating end_route?
        avoid_self = False
        if self.target_unit and cur_cell in target_route and cur_cell.man_dist(opp_cell) == 2:
            # TODO: unless alternative routes/moves make no sense..
            #       if all other moves (other than target_route[1]) result in cost same or longer
            target_route = [opp_cell]
            next_cell, _ = self.target_unit.goal_to_move(step, self.target_factory.cell())
            if next_cell is not opp_cell:
                target_route.append(next_cell)
            #avoid_self = True
            #log(f'{self.unit} clear target route')

        arrive_idx = None
        for j, c in enumerate(target_route):
            if c.man_dist_factory(self.target_factory) <= 1:
                arrive_idx = j
                break
        if arrive_idx is not None:
            target_route = target_route[:arrive_idx+1]

        end_cell = target_route[-1]
        if end_cell.factory() is not self.target_factory:
            # Slight preference toward a straight line route
            end_route = board.route(
                step, end_cell, self.target_unit,
                dest_cell=self.target_factory.cell(),
                dest_cond=lambda s,c: (c.factory() is self.target_factory),
                avoid_cond=lambda s,c: ((c.factory() and c.factory().player_id == self.unit.player_id)
                                        or (avoid_self and c.man_dist(cur_cell) == 1)),
                unit_move_cost=1, unit_rubble_movement_cost=0.0375)
            if end_route:
                target_route = target_route[:-1] + end_route

        #log(f'{self.unit} blockade route B: {target_route}')
        if target_route[-1].factory() is self.target_factory:
            self._target_route = target_route
        else:
            self._target_route = []

        return self._target_route

    def _goal_cell_score(self, step, cell):
        # TODO: if opp is not approaching factory straight on, make sure we position ourselves
        #       offset from factory in both directions of opp
        #       kinda picture a square that we "pivot" around the factory to box them out
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        opp_cell = self.opp_cell()

        #       Want low distance to self.unit
        self_dist = cell.man_dist(cur_cell)
        opp_dist = cell.man_dist(opp_cell)
        #       Want low distance to self.factory
        own_factory_dist = cell.man_dist_factory(self.factory)
        #       Want low-ish (~5-10) distance to target_factory
        opp_factory_dist = cell.man_dist_factory(self.target_factory)
        #       Want low rubble and low neighbor rubble
        rubble = cell.rubble[i]//20*20
        adj_rubble = sum(c.rubble[i]//20*20 for c in cell.neighbors())
        #       Want low historic opp unit traffic
        traffic = 0  # max=250
        for c in [cell] + cell.neighbors():
            traffic += 50 * sum(c.traffic())
        #       Want high distance from all other opp factories
        other_opp_factory_dist = 100
        for opp_factory in board.opp.factories():
            if opp_factory is self.target_factory:
                continue
            other_opp_factory_dist = min(other_opp_factory_dist, cell.man_dist_factory(opp_factory))
        #       Want no resources, and no resources adjacent
        resource = 0 # int(cell.ice or cell.ore)
        adj_resource = 0 # sum((c.ice or c.ore) for c in cell.neighbors())

        factory_dx = max(0, abs(cell.x - self.target_factory.x) - 1)
        factory_dy = max(0, abs(cell.y - self.target_factory.y) - 1)
        target_route = self.target_route(step)
        in_route = int(bool((factory_dx and factory_dy) or (cell in target_route)))

        par_dist = 10
        if (self.partner
            and self.partner.role
            and self.partner.role.NAME == 'blockade'):
            par_dist = cell.man_dist(self.partner.cell(step))

        # TODO: factory_dx/factory_dy
        features = [
            ('self_dist', self_dist, -0.25),
            ('self_dist0', int(self_dist == 0), 3),
            ('par_dist01', int(par_dist <= 1), 2),
            ('own_factory_dist_near', min(2, own_factory_dist), 0.5),
            ('own_factory_dist_far', max(3, own_factory_dist), -0.5),
            ('opp_factory_dist_near', min(8, opp_factory_dist), 5),
            ('opp_factory_dist_far', max(12, opp_factory_dist), -0.5),
            ('rubble', rubble/100, -10),
            ('rubble_adj', adj_rubble/400, -10),
            ('traffic', traffic/250, -30),
            ('other_opp_factory_dist_near', min(6, other_opp_factory_dist), 2),
            ('other_opp_factory_dist', min(25, other_opp_factory_dist), 3),
            ('resource', resource, -5),
            ('resource_adj', adj_resource/4, -8),
            ('in_route', in_route, 5),
            ('reachable', int(self_dist <= opp_dist - 2), 100),
            ]

        score = 0
        for (_, v, w) in features:
            score += v * w

        #if score > 100 or (cell.x == 29 and cell.y == 34): #cell.x == 36 and cell.y == 14:
        #    log(f'{cell.x},{cell.y}:')
        #    for (name, v, w) in features:
        #        log(f'{name} {v:.2f} * {w:.1f}')
        #    log(f'total {score:.2f}')
        #    log(f'route: {target_route}')

        return score

    def target_route_candidates(self, step):
        target_route = self.target_route(step)
        prev_cell = target_route[0] if target_route else None
        candidate_cells = set()
        for cell in target_route:
            if cell.factory() and cell.factory().player_id != self.unit.player_id:
                continue
            prev_factory_dx = max(0, abs(prev_cell.x - self.target_factory.x) - 1)
            prev_factory_dy = max(0, abs(prev_cell.y - self.target_factory.y) - 1)
            for neighbor in [cell] + cell.neighbors():
                if (not neighbor.assigned_unit(step)
                    and (not neighbor.factory() or neighbor.factory().player_id == self.unit.player_id)
                    and neighbor.man_dist(prev_cell) == 2):
                    # And neighbor is closer in both dimensions
                    factory_dx = max(0, abs(neighbor.x - self.target_factory.x) - 1)
                    factory_dy = max(0, abs(neighbor.y - self.target_factory.y) - 1)
                    if ((factory_dx < prev_factory_dx and factory_dy < prev_factory_dy)
                        or (factory_dx < prev_factory_dx and factory_dy == prev_factory_dy == 0)
                        or (factory_dy < prev_factory_dy and factory_dx == prev_factory_dx == 0)):
                        candidate_cells.add(neighbor)
            prev_cell = cell

        # If close to partner, expand candidates to include all route cells
        if (self.partner
            and self.partner.role
            and self.partner.role.NAME == 'blockade'):
            opp_cell = self.opp_cell()
            oc = self.unit.cell(step).man_dist(opp_cell)
            cp = self.unit.cell(step).man_dist(self.partner.cell(step))
            if oc/2 - 1 + cp < oc/2 + 1:
                candidate_cells.update(c for c in target_route if not c.factory())

        return sorted(candidate_cells, key=lambda c: c.id)

    def _best_goal_cell(self, step):
        best_cell, best_score = None, -C.UNREACHABLE
        candidate_cells = self.target_route_candidates(step)
        for cell in candidate_cells:
            score = self._goal_cell_score(step, cell)
            if score > best_score:
                best_cell, best_score = cell, score
        if step == self.unit.board.step:
            log(f'{self.unit} {self.factory} {self.target_unit} BEST {best_cell} {best_score}')
            #log(f'CANDS: {candidate_cells}')
        return best_cell or self.opp_cell()

    def is_engaged(self, step):
        board = self.unit.board
        cur_cell = self.unit.cell(step)
        opp_cell = self.opp_cell()
        opp_dist = cur_cell.man_dist(opp_cell)

        if self.partner and self.partner.role and self.partner.role.NAME == 'blockade':
            par_cell = self.partner.cell(step)
            if (cur_cell.man_dist(par_cell) == 1
                and any(c.is_between(opp_cell, self.target_factory.cell())
                        for c in cur_cell.neighbors() + par_cell.neighbors())
                and opp_dist <= 5):
                return True
        return False

    def _goal_cell_avoid_threat(self, step):
        # assume engaged and primary
        board = self.unit.board
        i = step - board.step
        if i != 0:
            return

        cur_cell = self.unit.cell(step)
        par_cell = self.partner.cell(step)
        opp_cell = self.opp_cell()
        opp_dist = cur_cell.man_dist(opp_cell)

        threats = []
        # time-equivalent power
        min_power = min(self.unit.power[0], self.partner.power[0])
        for neighbor in cur_cell.neighbors() + par_cell.neighbors():
            threat_unit = neighbor.unit(board.step)
            if (threat_unit
                and threat_unit.player_id == board.opp.id
                and (threat_unit.type == 'HEAVY'
                     or threat_unit.power[0] >= min_power)):
                threats.append(threat_unit)
        if not threats:
            return

        possible_cells, probable_cells = set(), set()
        for threat in threats:
            threat_cell = threat.cell(step)
            for neighbor in threat_cell.neighbors():
                if not neighbor.factory() or neighbor.factory().player_id == board.opp.id:
                    possible_cells.add(neighbor)

                neighbor_unit = neighbor.unit(step)
                if (neighbor_unit
                    and neighbor_unit.player_id == board.player.id
                    and (threat.power[0] > neighbor_unit.power[0]
                         or threat.type == 'HEAVY' and neighbor_unit.type == 'LIGHT')):
                    probable_cells.add(neighbor)

            if (len(threat.action_queue) >= 1
                and threat.action_queue[0][0] == Action.MOVE
                and threat.action_queue[0][1] != Direction.CENTER):
                direction = threat.action_queue[0][1]
                probable_cells.add(threat_cell.neighbor(*C.MOVE_DELTAS[direction]))
            elif threat.type == 'HEAVY':
                probable_cells.add(threat_cell)

        nearest_cell, min_dist = None, C.UNREACHABLE
        for cell in self.target_factory.cells():
            if cell.man_dist(opp_cell) < min_dist:
                nearest_cell, min_dist = cell, cell.man_dist(opp_cell)

        cutoff_cells = []
        for neighbor in nearest_cell.neighbors():
            if not neighbor.factory():
                cutoff_cells.append(neighbor)

        #cutoff_cell = self.target_factory.neighbor_toward(opp_cell)
        #opp_cutoff_dist = opp_cell.man_dist(cutoff_cell)

        # Check possible move directions
        # Both units can move that direction
        # Neither unit ends up adjacent to any threat unit
        # No possibility opp unit sneaks through blockade
        # any neighbor is between target unit and target factory is maintained
        best_direction, best_next_cell, best_score = None, None, 0
        for direction in range(Direction.MIN+1, Direction.MAX+1):
            next_cell1 = cur_cell.neighbor(*C.MOVE_DELTAS[direction])
            next_cell2 = par_cell.neighbor(*C.MOVE_DELTAS[direction])
            if (next_cell1
                and next_cell2
                and not next_cell1.unit(step+1)
                and not next_cell2.unit(step+1)
                and (not next_cell1.factory()
                     or next_cell1.factory().player_id == board.player.id)
                and (not next_cell2.factory()
                     or next_cell2.factory().player_id == board.player.id)
                #and not any(next_cell1.man_dist(u.cell(board.step)) == 1
                #            for u in threats)
                #and not any(next_cell2.man_dist(u.cell(board.step)) == 1
                #            for u in threats)
                and any(c.is_between(opp_cell, self.target_factory.cell())
                        for c in next_cell1.neighbors() + next_cell2.neighbors())):

                worst_cutoff_dist_diff = C.UNREACHABLE
                for cutoff_cell in cutoff_cells:
                    own_cdist = cutoff_cell.man_dist(next_cell1)
                    par_cdist = cutoff_cell.man_dist(next_cell2)
                    opp_cdist = cutoff_cell.man_dist(opp_cell)
                    dist_diff = opp_cdist - min(own_cdist, par_cdist)
                    if dist_diff < worst_cutoff_dist_diff:
                        worst_cutoff_dist_diff = dist_diff

                new_opp_dist = min(next_cell1.man_dist(opp_cell),
                                   next_cell2.man_dist(opp_cell))
                #new_cutoff_dist = min(next_cell1.man_dist(cutoff_cell),
                #                      next_cell2.man_dist(cutoff_cell))
                # Want to be closer to factory than opp_cell, but not too close?
                fdist = min(next_cell1.man_dist_factory(self.target_factory),
                            next_cell2.man_dist_factory(self.target_factory))

                score = 100

                if next_cell1 in possible_cells:
                    score -= 10
                if next_cell2 in possible_cells:
                    score -= 10
                if next_cell1 in probable_cells:
                    score -= 200
                if next_cell2 in probable_cells:
                    score -= 200

                if new_opp_dist == 1:
                    score += 1
                elif new_opp_dist == 2:
                    score += 1.5
                elif new_opp_dist == 3:
                    score += 1

                for neighbor in [opp_cell] + opp_cell.neighbors():
                    if next_cell1.is_between(neighbor, self.target_factory.cell()):
                        score += 0.5 if neighbor is opp_cell else 0.25
                    if next_cell2.is_between(neighbor, self.target_factory.cell()):
                        score += 0.5 if neighbor is opp_cell else 0.25

                #if new_cutoff_dist >= opp_cutoff_dist:
                #    score -= 200
                if worst_cutoff_dist_diff <= -1:
                    score -= 200
                if worst_cutoff_dist_diff == 0:
                    score -= 50
                elif worst_cutoff_dist_diff == 1:
                    score -= 20

                if fdist == 1:
                    score -= 3
                elif fdist == 2:
                    score -= 0.5

                for neighbor in next_cell1.neighbors() + next_cell2.neighbors():
                    # If we will still be adjacent to threat after this move, discourage
                    if neighbor in probable_cells:
                        score -= 5
                    # If collision with yet another threat is possible, do not consider
                    neighbor_unit = neighbor.unit(step)
                    if (neighbor_unit
                        and neighbor_unit.player_id == board.opp.id
                        and (neighbor_unit.power[0] > min_power
                             or neighbor_unit.type == 'HEAVY')
                        and neighbor_unit not in threats):
                        score -= 200

                log(f'{self.unit} AVOID dir {direction} {next_cell1} {score}'
                    f' -- {cutoff_cells} {worst_cutoff_dist_diff}')
                if score > best_score:
                    best_direction, best_next_cell, best_score = direction, next_cell1, score

        if best_next_cell:
            log(f'primary {self.unit} avoid {best_direction} {threats}')
            self._avoid = best_direction
            self._force_direction = step, best_direction
            self._next_goals = step+1, ['swap', 'chill']
            self.partner.role._next_goals = step+1, ['swap', 'chill']
            self._goal_cell = best_next_cell
            return self._goal_cell

    def goal_cell(self, step):
        # Possible for factory and target_unit to be invalidated same step
        # Handle this function being called before the role is officially invalidated.
        if self.goal == 'temp invalid':
            return self.unit.cell(step)

        # Temporarily override goal cell if on factory center
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        opp_cell = self.opp_cell()
        opp_nonfactory_cell = (opp_cell.factory().neighbor_toward(cur_cell)
                               if opp_cell.factory()
                               else opp_cell)
        opp_dist = cur_cell.man_dist(opp_cell)

        if cur_cell is self.get_factory().cell():
            return opp_nonfactory_cell

        if self.goal and self.goal.type == 'FACTORY':
            return self.goal.cell()

        if self._next_goals and step >= self._next_goals[0] and self.partner:
            ng_step, next_goals = self._next_goals
            if next_goals[0] == 'swap':
                self._goal_cell = self.partner.cell(step)
                self._force_direction = step, cur_cell.neighbor_to_direction(self._goal_cell)
            elif next_goals[0] == 'chill':
                self._goal_cell = cur_cell
                self._force_direction = step, Direction.CENTER
            else:
                self._goal_cell = cur_cell.neighbor(*C.MOVE_DELTAS[next_goals[0]]) or cur_cell
                self._force_direction = step, cur_cell.neighbor_to_direction(self._goal_cell)
            if len(next_goals) > 1:
                next_goals = next_goals[1:]
            self._next_goals = ng_step, next_goals
            return self._goal_cell

        if self._goal_cell is not None:
            #if not self.goal or self.goal.type != 'FACTORY':
            #    if (i == 1
            #        and self._goal_cell.factory()
            #        and self._goal_cell.factory().player_id == board.opp.id):
            #        log(f'WARNING aiming at opp factory {self.unit} {self._goal_cell}')
            return self._goal_cell

        primary_on_the_move = False
        if self.partner and self.partner.role and self.partner.role.NAME == 'blockade':
            # In position
            # self and partner are adjacent, but where
            # Check to see that at least one is between factory and target_unit
            # If so, then we do special goal cell determination
            par_cell = self.partner.cell(step)
            min_power = min(self.unit.power[0], self.partner.power[0])

            if (self.target_unit
                and cur_cell.man_dist(par_cell) == 1
                and any(c.is_between(opp_cell, self.target_factory.cell())
                        for c in cur_cell.neighbors() + par_cell.neighbors())
                and opp_dist <= 5):
                self._goal_cell = cur_cell

                if self.is_primary():
                    # If this returns non-None, everything has been handled
                    if self._goal_cell_avoid_threat(step):
                        return self._goal_cell

                    # If opp is dist-2 diagonal from primary
                    # unless we are +3/+3 from target_factory: chase
                    opp_dx, opp_dy = opp_cell.x - cur_cell.x, opp_cell.y - cur_cell.y
                    opp_factory_dx = max(0, abs(opp_cell.x - self.target_factory.x) - 1)
                    opp_factory_dy = max(0, abs(opp_cell.y - self.target_factory.y) - 1)
                    factory_dx = max(0, abs(cur_cell.x - self.target_factory.x) - 1)
                    factory_dy = max(0, abs(cur_cell.y - self.target_factory.y) - 1)
                    if 1 <= abs(opp_dx) <= 2 and 1 <= abs(opp_dy) <= 2:
                        # If going toward target factory, always slide
                        # If going away from factory, stop once we hit the +4/+4 mark
                        if (False
                            #or opp_factory_dx == 0
                            #or opp_factory_dy == 0
                            or opp_factory_dx <= factory_dx
                            or opp_factory_dy <= factory_dy
                            or abs(factory_dx) <= 4
                            or abs(factory_dy) <= 4):

                            enough_power = True
                            if ((opp_dx == 2 or opp_dy == 2)
                                and min_power <= self.target_unit.power[0] + 3):
                                enough_power = False

                            #if abs(factory_dx) <= 3 or abs(factory_dy) <= 3:
                            best_cell, best_score = None, -C.UNREACHABLE
                            for neighbor in cur_cell.neighbors():
                                if (neighbor.man_dist(opp_cell) < opp_dist
                                    and not neighbor.unit(step+1)
                                    and neighbor.is_between(opp_cell, self.target_factory.cell())
                                    and (not neighbor.factory()
                                         or neighbor.factory().player_id == board.player.id)):
                                    nfactory_dx = max(0, abs(neighbor.x - self.target_factory.x) - 1)
                                    nfactory_dy = max(0, abs(neighbor.y - self.target_factory.y) - 1)
                                    ncell_dx = abs(neighbor.x - self.target_factory.x)
                                    ncell_dy = abs(neighbor.y - self.target_factory.y)
                                    score = min(nfactory_dx, nfactory_dy) + 0.1*min(ncell_dx, ncell_dy)
                                    if score > best_score:
                                        best_cell, best_score = neighbor, score
                            if enough_power and best_cell:
                                log(f'primary {self.unit} slide A')
                                self._next_goals = step+1, [cur_cell.neighbor_to_direction(best_cell)]
                                self.partner.role._next_goals = step+1, [cur_cell.neighbor_to_direction(best_cell)]
                                self._goal_cell = best_cell
                                return self._goal_cell

                    # If adjacent to opp and secondary is "behind" primary, slide toward factory
                    sec_opp_dx, sec_opp_dy = opp_cell.x - par_cell.x, opp_cell.y - par_cell.y
                    if sec_opp_dx == 2 * opp_dx and sec_opp_dy == 2 * opp_dy:
                        for neighbor in cur_cell.neighbors():
                            if (neighbor is not par_cell
                                and not neighbor.unit(step + 1)
                                and (neighbor.man_dist_factory(self.target_factory)
                                     <= cur_cell.man_dist_factory(self.target_factory))
                                and (not neighbor.factory()
                                     or neighbor.factory().player_id == board.player.id)):
                                log(f'primary {self.unit} slide B')
                                self._next_goals = step+1, ['chill']
                                self.partner.role._next_goals = step+1, ['chill']
                                self._goal_cell = neighbor
                                return self._goal_cell

                    # If adjacent to opp and opp is in a better position
                    if (opp_dist == 1
                        and opp_factory_dx <= factory_dx
                        and opp_factory_dy <= factory_dy):
                        if not opp_nonfactory_cell.unit(step + 1):
                            log(f'primary {self.unit} push A')
                            self._next_goals = step+1, [cur_cell.neighbor_to_direction(opp_cell)]
                            self.partner.role._next_goals = step+1, [cur_cell.neighbor_to_direction(opp_cell)]
                            self._goal_cell = opp_nonfactory_cell
                            self._push = True
                            return self._goal_cell

                    # If small gap to opp and we are close to opp factory, push toward opp
                    if (opp_dist == 2
                        and factory_dx <= 2
                        and factory_dy <= 2
                        and (opp_cell.x == cur_cell.x or opp_cell.y == cur_cell.y)
                        and self.unit.power[i] >= self.target_unit.power[i]):
                        target_cell = cur_cell.neighbor_toward(opp_cell)
                        if not target_cell.unit(step + 1):
                            log(f'primary {self.unit} push B')
                            self._next_goals = step+1, ['swap', 'chill']
                            self.partner.role._next_goals = step+1, ['swap', 'chill']
                            self._goal_cell = target_cell
                            self._push = True
                            return self._goal_cell

                    # Confirm current position still works with target_route
                    position_candidates = self.target_route_candidates(step)
                    if (opp_dist == 1
                        or cur_cell in position_candidates
                        or par_cell in position_candidates
                        or (opp_dist == 2
                            and any(c.is_between(opp_cell, self.target_factory.cell())
                                    for c in [cur_cell, par_cell]))):
                        threat_exists = False
                        for neighbor in cur_cell.neighbors() + par_cell.neighbors():
                            threat_unit = neighbor.unit(step)
                            if (threat_unit
                                and threat_unit.type == 'LIGHT'
                                and threat_unit.player_id != self.unit.player_id):
                                # Check if threat has power to make the move
                                if ((neighbor.man_dist(cur_cell) == 1
                                     and (threat_unit.power[i]
                                          >= (threat_unit.cfg.MOVE_COST
                                              + math.floor(threat_unit.cfg.RUBBLE_MOVEMENT_COST
                                                           * cur_cell.rubble[i]))))
                                    or (neighbor.man_dist(par_cell) == 1
                                        and (threat_unit.power[i]
                                             >= (threat_unit.cfg.MOVE_COST
                                                 + math.floor(threat_unit.cfg.RUBBLE_MOVEMENT_COST
                                                              * par_cell.rubble[i]))))):
                                    threat_exists = True
                                    break
                        if threat_exists:
                            log(f'primary {self.unit} swap')
                            self._next_goals = step+1, ['chill']
                            self.partner.role._next_goals = step+1, ['chill']
                            self._goal_cell = par_cell
                        else:
                            self._next_goals = step+1, ['chill']
                            self.partner.role._next_goals = step+1, ['chill']
                            log(f'primary {self.unit} chill A')
                        return self._goal_cell
                    else:
                        # Adjacent to partner, but need to move to keep up with target_unit
                        log(f'primary {self.unit} on the move')
                        primary_on_the_move = True
                        pass # Drop down to below goal cell logic
                else:
                    if self.partner.x[i+1] is not None:
                        #if False and self.partner.role._straightline: # TODO
                        #    # If primary is moving straight toward a goal, do the same
                        #    sgoal_cell = self.partner.role._straightline
                        #    naive_route = board.naive_cost(
                        #        step, self.unit, cur_cell, sgoal_cell, ret_route=True)
                        #    if len(naive_route) >= 2:
                        #        #self._goal_cell = naive_route[1]
                        #        self._goal_cell = None
                        #        log(f'secondary {self.unit} straightline toward {sgoal_cell}')
                        #        #return self._goal_cell
                        #        return naive_route[1]
                        if self.partner.role._avoid:
                            # If primary is avoiding a threat, move the same direction
                            direction = self.partner.role._avoid
                            self._force_direction = (step, direction)
                            self._goal_cell = cur_cell.neighbor(*C.MOVE_DELTAS[direction])
                            log(f'secondary {self.unit} avoid {direction}')
                            return self._goal_cell
                        elif self.partner.role._push:
                            # If primary is pushing, push with it
                            opp_dx = opp_cell.x - par_cell.x
                            opp_dy = opp_cell.y - par_cell.y
                            opp_dx = (opp_dx // abs(opp_dx)) if opp_dx else opp_dx
                            opp_dy = (opp_dy // abs(opp_dy)) if opp_dy else opp_dy
                            self._goal_cell = cur_cell.neighbor(opp_dx, opp_dy)
                            if (self._goal_cell.factory()
                                and self._goal_cell.factory().player_id == board.opp.id):
                                self._goal_cell = par_cell
                            log(f'secondary {self.unit} push')
                            return self._goal_cell
                        else:
                            # If primary has moved, take its cell
                            log(f'secondary {self.unit} follow')
                            self._goal_cell = par_cell
                            return self._goal_cell
                    else:
                        # Otherwise stand still
                        log(f'secondary {self.unit} no move')
                        self._goal_cell = cur_cell
                        return self._goal_cell

            elif (self.target_unit
                  and cur_cell.man_dist(par_cell) <= 2
                  and any(c.is_between(opp_cell, self.target_factory.cell())
                          for c in cur_cell.neighbors() + par_cell.neighbors())
                  and opp_dist > 5):
                if min_power <= self.target_unit.power[0]:
                    threats = (self._get_threat_units(
                        cur_cell, history_len=1, max_radius=1, heavy=True, light=True)
                               + self._get_threat_units(
                                   par_cell, history_len=1, max_radius=1, heavy=True, light=True))
                    if not threats:
                        log(f'primary {self.unit} chill low power')
                        self._next_goals = step+1, ['chill']
                        self.partner.role._next_goals = step+1, ['chill']
                        self._goal_cell = cur_cell
                        return self._goal_cell

            # Pick a cell adjacent to primary's goal
            if not self.is_primary() and (self.partner.role.goal is self.partner.role.target_unit):
                par_goal_cell = self.partner.role.goal_cell(step)
                self._goal_cell = par_goal_cell

                dx = par_goal_cell.x - self.target_factory.x
                dy = par_goal_cell.y - self.target_factory.y

                best_cell, best_score = None, -C.UNREACHABLE
                for neighbor in par_goal_cell.neighbors():
                    if (neighbor
                        and not neighbor.assigned_unit(step)
                        and not (neighbor.factory()
                                 and neighbor.factory().player_id != self.unit.player_id)):
                        # Want near cur_cell
                        # If large difference between dx and dy, want the opposite
                        # Want low rubble
                        score = -board.naive_cost(step, self.unit, cur_cell, neighbor)
                        if abs(dx) >= abs(dy) + 2:
                            score += (4 if (neighbor.x == par_goal_cell.x) else 0)
                        elif abs(dy) >= abs(dx) + 2:
                            score += (4 if (neighbor.y == par_goal_cell.y) else 0)
                        if score > best_score:
                            best_cell, best_score = neighbor, score
                if best_cell:
                    self._goal_cell = best_cell
                    log(f'{self.unit} is secondary and heading to {self._goal_cell}')

                    if (self.target_unit
                        and self.target_unit.water[0] >= 5
                        and cur_cell.man_dist(self._goal_cell) > 2
                        and opp_cell.man_dist(self._goal_cell) - 2 <= cur_cell.man_dist(self._goal_cell)
                        and self.unit.power[i] - self.target_unit.power[i] >= 10):
                        self._straightline = True
                        log(f'secondary {self.unit} straightline toward {self._goal_cell}')
                        #if i == 0:  # TODO remove
                        #    print(f'{step} {len(board.player.factories())} secondary {self.unit} straightline toward {self._goal_cell}',
                        #          file=sys.stderr)
                        #    print('\n'*7, file=sys.stderr)
                    return self._goal_cell

        # Need target_unit's projected route to get to target_factory
        target_route = self.target_route(step)
        opp_factory_dist = opp_cell.man_dist_factory(self.target_factory)
        own_factory_dist = cur_cell.man_dist_factory(self.target_factory)
        unit_dist = cur_cell.man_dist(opp_cell)
        if not target_route:
            self._goal_cell = opp_nonfactory_cell
            log(f'{self.unit} blockade other A {self._goal_cell}')
        elif opp_factory_dist <= own_factory_dist:
            if (self.target_unit
                and self.unit.power[0] > self.target_unit.power[0] + 5
                and not cur_cell.neighbor_toward(self.target_factory.cell()).factory()):
                self._goal_cell = cur_cell.neighbor_toward(self.target_factory.cell())
                # Shorter goals give partner a better chance of linking up
                log(f'{self.unit} blockade other B {self._goal_cell}')
            else:
                self._goal_cell = target_route[-2] if len(target_route) >= 2 else opp_nonfactory_cell
                log(f'{self.unit} blockade other C {self._goal_cell}')
        elif unit_dist > 3:
            self._goal_cell = (self._best_goal_cell(step)
                               if len(target_route) >= 4
                               else opp_nonfactory_cell)
        elif 2 <= unit_dist <= 3:
            self._goal_cell = (self._best_goal_cell(step)
                               if (i == 0 and len(target_route) >= 4)
                               else opp_nonfactory_cell)
        else:
            self._goal_cell = target_route[-2] if len(target_route) >= 2 else opp_nonfactory_cell
            log(f'{self.unit} blockade other D {self._goal_cell}')

        # If opp unit exists and has water and is almost as close to goal as we are
        # Need to move in a straighter line
        if (self.target_unit
            and self.target_unit.water[0] >= 5
            and cur_cell.man_dist(self._goal_cell) > 2
            and opp_cell.man_dist(self._goal_cell) - 2 <= cur_cell.man_dist(self._goal_cell)
            and self.unit.power[i] - self.target_unit.power[i] >= 10):
            self._straightline = True
            log(f'primary {self.unit} straightline toward {self._goal_cell}')
            #if i == 0:  # TODO remove
            #    print(f'{step} {len(board.player.factories())} primary {self.unit} straightline toward {self._goal_cell}',
            #          file=sys.stderr)
            #    print('\n'*7, file=sys.stderr)
            return self._goal_cell

        # If the primary is "moving" in order to swap with the secondary, just stand still
        if primary_on_the_move and cur_cell.neighbor_toward(self._goal_cell) is par_cell:
            log(f'primary {self.unit} chill B')
            self._goal_cell = cur_cell
            return self._goal_cell

        if (self.is_primary()
            and self.partner and self.partner.role and self.partner.role.NAME == 'blockade'
            and cur_cell.man_dist(par_cell) == 1
            and self._goal_cell is par_cell):
            log(f'primary {self.unit} chill C')
            self._goal_cell = cur_cell
            return self._goal_cell

        #log(f'blockade {self.unit} goal = {self._goal_cell}')
        return self._goal_cell

    def update_goal(self, step):
        board = self.unit.board
        i = step - board.step
        cur_power = self.unit.power[i]

        if self.goal is self.target_unit:
            # Handled by RoleRecharge
            pass
        elif self.goal is self.factory:
            # If power is good, head out to block target unit
            power_threshold = self.unit.cfg.BATTERY_CAPACITY - 3
            if cur_power >= power_threshold:
                self.goal = self.target_unit
                return

            # determine where we want to be (roughly)
            # determine how much power we would be able to maintain to that position (roughly)
            # look at current power and dist to dest vs dist to factory
            # lower power threshold?
            # esp. helpful when in position from anticipation (maybe with ~140 power) and then we
            #      transition to a real unit
            cur_cell = self.unit.cell(step)
            opp_cell = self.opp_cell()
            goal_cell = self._best_goal_cell(step)

            dist_c2f = cur_cell.man_dist(self.factory.cell())
            dist_f2g = self.factory.cell().man_dist(goal_cell)
            dist_c2g = cur_cell.man_dist(goal_cell)

            # Continue getting power now while we have a break between transporters
            if self.target_unit is None:
                if cur_power >= 120 and 2 * dist_c2f >= opp_cell.man_dist_factory(self.target_factory):
                    pass
                else:
                    return

            cost_c2g = board.naive_cost(step, self.unit, cur_cell, goal_cell)
            cost_f2g = board.naive_cost(step, self.unit, self.factory.cell(), goal_cell)

            dist_diff = dist_c2f + dist_f2g - dist_c2g
            power_gain = 0
            if dist_diff > 0:
                start_step = step + dist_c2g
                power_gain = self.unit.power_gain(start_step, end_step=start_step+dist_diff)

            if 150 - cost_f2g <= cur_power - cost_c2g + power_gain + 3:
                if i == 0:
                    log(f'{self.unit} PRE blockade-goal-unit A {goal_cell} {cost_c2g} {cost_f2g}'
                        f' -- {150 - cost_f2g} vs {cur_power - cost_c2g + power_gain + 3}')
                cost_c2g, dist_c2g, _ = board.dist(
                    step, cur_cell, self.unit, dest_cell=goal_cell)
                cost_f2g, dist_f2g, _ = board.dist(
                    step, self.factory.cell(), self.unit, dest_cell=goal_cell)

                dist_diff = dist_c2f + dist_f2g - dist_c2g
                power_gain = 0
                if dist_diff > 0:
                    start_step = step + dist_c2g
                    power_gain = self.unit.power_gain(start_step, end_step=start_step+dist_diff)

                if 150 - cost_f2g <= cur_power - cost_c2g + power_gain + 3:
                    # Just go to goal now
                    self.goal = self.target_unit
                    if i == 0:
                        log(f'{self.unit} blockade-goal-unit A {goal_cell} {cost_c2g} {cost_f2g}'
                            f' -- {150 - cost_f2g} vs {cur_power - cost_c2g + power_gain + 3}')
                    return

            # If unit exists, has water, on its way
            # and we have enough power
            # and we don't have the time to go get more
            if self.target_unit and self.target_unit.water[0] >= 5:
                opp_power = self.target_unit.power[0]
                for spec in self.target_unit.action_queue[:5]:
                    if spec[0] == Action.PICKUP and spec[2] == Resource.POWER:
                        opp_power = min(150, opp_power + spec[3])

                pickup_dist = (cur_cell.man_dist_factory(self.factory)
                               + 1
                               + self.factory.cell().man_dist(goal_cell))

                # Not enough time to get more power
                if (pickup_dist >= opp_cell.man_dist_factory(self.target_factory)
                    and cur_power >= opp_power):
                    self.goal = self.target_unit
                    if i == 0:
                        log(f'{self.unit} blockade-goal-unit B {goal_cell} {pickup_dist} {opp_power}')
                    return
        else:
            assert False

    def do_move(self, step):
        # TODO: when in position and opp is nearby, may want to guess about future (idx1+) actions
        #       or just repeat the idx0 action, or random, or just idle, or lie, etc
        board = self.unit.board
        i = step - board.step

        action = self._do_move(step)

        # Lock in no-moves when the opp is nearby
        if action is None and self.target_unit:
            opp_cell = self.target_unit.cell(board.step)
            opp_dist = self.unit.cell(step).man_dist(opp_cell)
            if opp_dist <= 20:
                return self.unit.do_no_move(step)

        return action

    def do_dig(self, step):
        return

    def do_pickup(self, step):
        #if step == self.unit.board.step:
        #    log(f'role_blockade do_pickup {self.unit} {self}')
        return self._do_power_pickup(step)

    def do_transfer(self, step):
        #if step == self.unit.board.step:
        #    log(f'role_blockade do_transfer {self.unit} {self}')
        return self._do_transfer_resource_to_factory(step)

    def get_factory(self):
        return self.factory
