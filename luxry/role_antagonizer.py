from collections import defaultdict
import sys

from .cell import Cell
from .role import Role
from .role_recharge import RoleRecharge
from .util import C, Action, Resource, log, profileit


class RoleAntagonizer(Role):
    '''Shut down opponent-utilized resource cells'''
    NAME = 'antagonizer'

    def __init__(self, step, unit, factory, target_cell, chain=False, target_factory=None, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.target_cell = target_cell
        # Target unit is not an exclusive assignment. Multiple antagonizers can track one opp unit.
        # Target cells will still be exclusive though.
        self.chain = 1 if chain else 0
        self.target_factory = target_factory
        self.goal = goal
        self._can_destroy_factory = None

        if self.goal is None:
            cur_cell = unit.cell(step)
            factory_dist = cur_cell.man_dist_factory(factory) if factory else C.UNREACHABLE
            target_cell_dist = cur_cell.man_dist(target_cell)
            self.goal = target_cell if target_cell_dist < factory_dist else factory

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        tgoal = '*' if self.goal is not self.factory else ''
        return (f'Antagonizer[{self.factory}{fgoal} -> '
                f'{self.target_factory or ""}{self.target_cell}{tgoal}]')

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.target_cell),
                self.serialize_obj(self.chain),
                self.serialize_obj(self.target_factory),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        target_cell = cls.deserialize_obj(unit.board, role_data[2])
        chain = cls.deserialize_obj(unit.board, role_data[3])
        target_factory = cls.deserialize_obj(unit.board, role_data[4])
        goal = cls.deserialize_obj(unit.board, role_data[5])
        return RoleAntagonizer(step, unit, factory, target_cell, chain=chain,
                               target_factory=target_factory, goal=goal)

    @classmethod
    @profileit
    def from_mine(cls, step, unit, max_dist=100, ice=None, max_count=None, max_water=None):
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        if max_count:
            like_antagonizers = [u for u in factory.units(step)
                                 if u.type == unit.type and u.role and u.role.NAME == 'antagonizer']
            if 1 + len(like_antagonizers) > max_count:
                return

        # TODO also prioritize based on dist to opp factory, as those are more valuable
        best_cell, min_dist = None, C.UNREACHABLE
        for cell in board.opp_mines(heavy=(unit.type=='HEAVY'), ice=ice):
            if ice and max_water is not None:
                opp_factory = cell.nearest_factory(board, player_id=board.opp.id)
                opp_units = [u for u in opp_factory.units(step) if u.type == 'HEAVY']
                water = (opp_factory.water[i]
                         + ((opp_factory.ice[i] + sum(u.ice[i] for u in opp_units))
                            // board.env_cfg.ICE_WATER_RATIO))
                if water > max_water:
                    continue
            dist = cell.man_dist_factory(factory)
            if (dist <= max_dist
                and dist < min_dist
                and (not cell.assigned_unit(step)
                     or unit.type == 'HEAVY' and cell.assigned_unit(step).type == 'LIGHT')):
                best_cell, min_dist = cell, dist
        if best_cell:
            cls._handle_displaced_unit(step, best_cell)
            return RoleAntagonizer(step, unit, factory, best_cell)

    # TODO: prioritize unassigned chains
    # TODO: prioritize ice and/or ore?
    # TODO: check that a heavy exists at the end of the chain (Note: chain may already be disrupted)
    @classmethod
    @profileit
    def from_chain(cls, step, unit, max_dist=100, max_count=None):
        if unit.type == 'HEAVY':
            return

        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        if max_count:
            like_antagonizers = [u for u in factory.units(step)
                                 if (u.type == unit.type
                                     and u.role
                                     and u.role.NAME == 'antagonizer'
                                     and u.role.chain)]
            if 1 + len(like_antagonizers) > max_count:
                return

        best_cell, min_dist = None, C.UNREACHABLE
        for opp_unit in board.opp.units():
            if opp_unit.is_chain():
                cell = opp_unit.cell(board.step)
                dist = cell.man_dist_factory(factory)
                if (dist <= max_dist
                    and dist < min_dist
                    and not cell.assigned_unit(step)):
                    #and cls._dest_is_safe(step, unit, cell)):
                    best_cell, min_dist = cell, dist
        if best_cell:
            if i == 0:
                log(f'{unit} antagonize chain at {best_cell}')
            return RoleAntagonizer(step, unit, factory, best_cell, chain=True)

    @classmethod
    @profileit
    def from_cell(cls, step, unit, target_cell):
        if not target_cell:
            return

        board = unit.board
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        if (not target_cell.assigned_unit(step)
            or (unit.type == 'HEAVY' and target_cell.assigned_unit(step).type == 'LIGHT')):
            cls._handle_displaced_unit(step, target_cell)
            return RoleAntagonizer(step, unit, factory, target_cell)

    @classmethod
    @profileit
    def from_factory(cls, step, unit, target_factory):
        ''' Only works if there is a unit within dist-4 of target_factory.cell() at board.step'''
        if not target_factory:
            return

        board = unit.board
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        target_cell = cls._get_target_cell_from_target_factory(step, unit, target_factory)
        if target_cell:
            cls._handle_displaced_unit(step, target_cell)
            return RoleAntagonizer(step, unit, factory, target_cell, target_factory=target_factory)

    @classmethod
    @profileit
    def from_transition_active_antagonizer_with_target_factory(cls, step, unit):
        # Only does anything at step idx i=0. Update target cell based on new opp info.
        if (step != unit.board.step
            or not unit.role
            or unit.role.NAME != 'antagonizer'
            or not unit.role.target_factory):
            return

        new_target_cell = cls._get_target_cell_from_target_factory(
            step, unit, unit.role.target_factory, prev_target_cell=unit.role.target_cell)

        # It's possible there is no longer a valid target cell, then transition to recharge
        if new_target_cell:
            if new_target_cell is not unit.role.target_cell:
                cls._handle_displaced_unit(step, new_target_cell)
                return RoleAntagonizer(
                    step, unit, unit.role.factory, new_target_cell,
                    target_factory=unit.role.target_factory)
        else:
            return RoleRecharge(step, unit, unit.role.factory)

    def is_valid(self, step):
        if not self.factory:
            return False

        board = self.unit.board
        i = step - board.step

        # If i > 0 then there is no new info available to invalidate based on
        # If tracking a unit, no need to do the other checks for target_cell
        if i > 0 or self.target_factory or self.can_destroy_factory(step):
            return True

        # If a heavy takes over a cell that a light is antagonizing, invalidate the ant.
        if self.unit.type == 'LIGHT':
            uid = self.target_cell.unit_history[step]
            if (uid in board.units
                and board.units[uid].type == 'HEAVY'
                and board.units[uid].player_id == board.opp.id
                and step >= 2
                and self.target_cell.unit_history[step-1] == uid
                and self.target_cell.unit_history[step-2] == uid):
                return False

        opp_here_recently = False
        for j in range(board.step, max(-1, board.step-15), -1):
            if opp_here_recently:
                break
            uid = self.target_cell.unit_history[j]
            if uid in board.units and board.units[uid].player_id == board.opp.id:
                opp_here_recently = True

        return (opp_here_recently
                or self.target_cell in board.opp_mines(heavy=(self.unit.type=='HEAVY')))

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)
        self.target_cell.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            self.target_cell.unset_assignment(step, self.unit)

    @classmethod
    def _get_target_cell_from_target_factory(cls, step, unit, target_factory, prev_target_cell=None):
        # For any given resource cell
        # Check it and neighbors for cell with most denied resources (and rubble/dist?)

        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        # Try units that have recently been to target factory
        # Otherwise use any nearby unit(s)
        opp_units = [u for u in target_factory.units(step) if u.type == unit.type]
        if not opp_units:
            for cell, _ in target_factory.cell().radius_cells(max_radius=4):
                opp_unit = cell.unit(board.step)
                if (opp_unit
                    and opp_unit.player_id != unit.player_id
                    and opp_unit.type == unit.type):
                    opp_units.append(opp_unit)

        scores = defaultdict(int)
        for opp_unit in opp_units:
            future_mines = opp_unit.get_mines(past_steps=0, future_steps=10)
            for cell in future_mines:
                scores[cell] += 100 if cell.ice else 10
            past_mines15 = opp_unit.get_mines(past_steps=15, future_steps=0)
            for cell in past_mines15:
                if cell.ice:
                    scores[cell] += 10
                #if cell.ore:
                #    if cell.man_dist_factory(factory) < 5:
                #        scores[cell] += 0.5
            past_mines3 = opp_unit.get_mines(past_steps=3, future_steps=0)
            for cell in past_mines3:
                if cell.ore:
                    scores[cell] += 1
            #if i == 0 and unit.id == 11:
            #    log(f'  opp: {opp_unit} {future_mines} {past_mines}')
        #if step == board.step:
        #    log(f'scores A: {scores}')

        if (prev_target_cell
            and (prev_target_cell.ice or prev_target_cell.ore)
            and cls._get_threat_units(prev_target_cell, history_len=1, max_radius=1, heavy=True)):
            scores[prev_target_cell] += 100 if prev_target_cell.ice else 10
        #if step == board.step:
        #    log(f'scores B: {scores}')

        # Theoretically we could aim to deny at a cell other than a resource cell
        # This doesn't work great in pracice so currently this only adds extra points for adj resources
        deny_scores = defaultdict(int)
        for cell, score in scores.items():
            if (not cell.assigned_unit(step)
                or cell.assigned_unit(step) is unit
                or (unit.type == 'HEAVY' and cell.assigned_unit(step).type == 'LIGHT')):
                for deny_cell in [cell]:# + cell.neighbors():
                    if (not deny_cell.assigned_unit(step)
                        or deny_cell.assigned_unit(step) is unit
                        or (unit.type == 'HEAVY' and deny_cell.assigned_unit(step).type == 'LIGHT')):
                        deny_scores[deny_cell] += score
                        for neighbor in [deny_cell] + deny_cell.neighbors():
                            if neighbor.ice:
                                deny_scores[deny_cell] += 1
                            elif neighbor.ore:
                                deny_scores[deny_cell] += 0.1
        #if step == board.step and unit.id == 1:
        #    log(f'deny_scores A: {deny_scores}')

        #if prev_target_cell:# and not (prev_target_cell.ice or prev_target_cell.ore):
        #    deny_scores[prev_target_cell] += 10
        #if step == board.step:
        #    log(f'deny_scores B: {deny_scores}')

        best_cell, best_score = None, -C.UNREACHABLE
        for cell, score in deny_scores.items():
            rubble = cell.rubble[0]
            own_dist = cell.man_dist_factory(factory)
            #opp_dist = cell.man_dist_factory(target_factory)
            total_score = score - own_dist - 0.01*rubble
            if total_score > best_score:
                best_cell, best_score = cell, total_score
                #if step == board.step and unit.id == 11:
                #    log(f'{unit} best cell {best_cell} {best_score}')
        return best_cell  # could be None

    # TODO: should we transition role to target_factory ant at some point?
    def can_destroy_factory(self, step):
        if self._can_destroy_factory is not None:
            return self._can_destroy_factory
        self._can_destroy_factory = False

        if (not self.unit.type == 'HEAVY'
            or not self.target_cell.ice):
            return self._can_destroy_factory

        # Be patient in an offensive ice conflict
        if (self.factory.mode
            and self.factory.mode.NAME == 'ice_conflict'
            and not self.factory.mode.defensive):
            return self._can_destroy_factory

        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)

        if cur_cell.man_dist(self.target_cell) > 2:
            return self._can_destroy_factory

        # Must be one of the nearest ice cells to opp factory
        opp_factory = (self.target_factory
                       or self.target_cell.nearest_factory(board, player_id=board.opp.id))
        resource_dist = self.target_cell.man_dist_factory(opp_factory)
        ice_routes = [r for r in opp_factory.resource_routes if r[-1].ice]
        if len(ice_routes) >= 1 and resource_dist > len(ice_routes[0])-1:
            return self._can_destroy_factory

        opp_units = [u for u in opp_factory.units(step)]
        oscillate_cost = (self.target_cell.rubble[i]
                          + min([c.rubble[i] for c in self.target_cell.neighbors() if not c.factory()])
                          + 2 * self.unit.cfg.MOVE_COST) / 2
        water = (opp_factory.water[i]
                 + sum(u.water[i] for u in opp_units)
                 + ((opp_factory.ice[i] + sum(u.ice[i] for u in opp_units))
                    // board.env_cfg.ICE_WATER_RATIO))
        step_count = self.unit.power[i] // oscillate_cost
        if step + step_count >= 1000:
            return self._can_destroy_factory

        water += 0.4 * step_count  # assume there is a light also mining
        self._can_destroy_factory = step_count >= water
        if i == 0 and self._can_destroy_factory:
            log(f'{self.unit} ant can destroy {opp_factory} {self.unit.power[i]} '
                f'{oscillate_cost} {step_count} {water}')
        return self._can_destroy_factory

    def goal_cell(self, step):
        # Temporarily override goal cell if on factory center
        cur_cell = self.unit.cell(step)
        if cur_cell is self.get_factory().cell():
            return self.target_cell

        if isinstance(self.goal, Cell):
            # If adjacent to goal and unthreatened, stay still
            # If tracking a unit to own factory-adjacent resource, always go to cell
            #    This will tire the opp and give us a chance to do some mining
            if (cur_cell.man_dist(self.goal) == 1
                and not (self.target_factory
                         and self.goal.man_dist_factory(self.get_factory()) == 1)):
                threat_exists = False
                for neighbor in [cur_cell] + cur_cell.neighbors():
                    opp_unit = neighbor.unit(self.unit.board.step)
                    if (opp_unit
                        and opp_unit.player_id != self.unit.player_id
                        and opp_unit.type == self.unit.type):
                        threat_exists = True
                        break
                if not threat_exists:
                    return cur_cell
            return self.goal

        # Goal is a factory
        return self.goal.cell()

    def update_goal(self, step):
        assert self.goal
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        unit_power = self.unit.power[i]
        unit_resource = max(self.unit.ice[i], self.unit.ore[i])

        if self.goal is self.target_cell:
            # Power threshold handled by RoleRecharge
            resource_threshold = self.unit.cfg.CARGO_SPACE // 2
            if unit_resource >= resource_threshold:
                self.goal = self.factory
        elif self.goal is self.factory:
            is_ice_conflict = (self.factory.mode and self.factory.mode.NAME == 'ice_conflict')
            if is_ice_conflict and step < 10 and self.unit.type == 'HEAVY':
                # Force ice conflict heavy ant to pickup before heading out at the start
                power_threshold = 600
            elif (is_ice_conflict
                  and cur_cell.factory()
                  and self.unit.power[i] < 2000
                  and self.factory.power[i] >= 500):
                # Pick up power if you're there and the factory has some
                power_threshold = self.unit.power[i] + 1
            else:
                min_moves = 10 if is_ice_conflict else 40
                power_threshold = (self.unit.cfg.ACTION_QUEUE_POWER_COST
                                   + board.naive_cost(step, self.unit, cur_cell, self.target_cell)
                                   + min_moves * self.unit.cfg.MOVE_COST
                                   + board.naive_cost(
                                       step, self.unit, self.target_cell, self.factory.cell()))
            power_threshold = min(power_threshold, self.unit.cfg.BATTERY_CAPACITY)
            resource_threshold = self.unit.cfg.CARGO_SPACE // 5
            if (unit_power >= power_threshold
                and unit_resource < resource_threshold):
                self.goal = self.target_cell
        else:
            assert False

    def do_move(self, step):
        # TODO: would like oscillation to cover another resource cell if possible
        #       not sure if that involves editing goal_to_move/threatened_by_opp for special
        #       antagonizer consideration?
        #       Something like negative risk?
        return self._do_move(step)

    def do_dig(self, step):
        # Only for ants tracking an opp unit. Only occassionally if not adjacent.
        cur_cell = self.unit.cell(step)
        resource_dist = cur_cell.man_dist_factory(self.factory)
        if (not self.target_factory
            or (not cur_cell.ice and not cur_cell.ore)
            or (resource_dist > 1 and step % 2 != 0)):
            return

        board = self.unit.board
        i = step - board.step
        goal_cell = self.goal_cell(step)
        opp_resource_dist = cur_cell.nearest_factory_dist(board, player_id=board.opp.id)

        if (cur_cell is goal_cell
            and (resource_dist < opp_resource_dist
                 or cur_cell.rubble[i] == 0)):
            if self.unit.power[i] >= self.unit.dig_cost(step):
                return self.unit.do_dig(step)

    def do_pickup(self, step):
        return self._do_power_pickup(step)

    def do_transfer(self, step):
        return self._do_transfer_resource_to_factory(step)

    def get_factory(self):
        return self.factory
