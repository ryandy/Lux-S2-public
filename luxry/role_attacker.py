import math
import sys

from .cell import Cell
from .role import Role
from .role_sidekick import RoleSidekick
from .util import C, log, profileit


class RoleAttacker(Role):
    '''Pursue opp units'''
    NAME = 'attacker'

    def __init__(self, step, unit, factory, target_unit,
                 sidekick_unit=None, low_power_target=0, defender=0, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.target_unit = target_unit
        self.sidekick_unit = sidekick_unit
        self.low_power_target = 1 if low_power_target else 0
        self.defender = 1 if defender else 0
        self.goal = goal

        if self.goal is None:
            if not factory or not target_unit:
                self.goal = 'temp invalid'
            elif target_unit.low_power or sidekick_unit:
                self.goal = target_unit
            else:
                cur_cell = unit.cell(step)
                target_recent_cell = target_unit.cell(unit.board.step)
                factory_dist = cur_cell.man_dist_factory(factory) if factory else C.UNREACHABLE
                target_dist = cur_cell.man_dist(target_recent_cell)
                self.goal = target_unit if target_dist < factory_dist else factory

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        tgoal = '*' if self.goal is self.target_unit else ''
        atype = 'sk' if self.sidekick_unit else ('lp' if self.low_power_target else 'd')
        return f'Attacker[{self.factory}{fgoal} -> {self.target_unit}({atype}){tgoal}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.target_unit),
                self.serialize_obj(self.sidekick_unit),
                self.serialize_obj(self.low_power_target),
                self.serialize_obj(self.defender),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        target_unit = cls.deserialize_obj(unit.board, role_data[2])
        sidekick_unit = cls.deserialize_obj(unit.board, role_data[3])
        low_power_target = cls.deserialize_obj(unit.board, role_data[4])
        defender = cls.deserialize_obj(unit.board, role_data[5])
        goal = cls.deserialize_obj(unit.board, role_data[6])
        return RoleAttacker(step, unit, factory, target_unit,
                            sidekick_unit=sidekick_unit,
                            low_power_target=low_power_target,
                            defender=defender,
                            goal=goal)

    @classmethod
    @profileit
    def from_transition_attack_water_transporter_ant(cls, step, unit):
        board = unit.board
        i = step - board.step
        if i != 0:
            return

        # role_blockade handled below
        if (unit.role
            and ((unit.role.NAME == 'recharge' and not unit.cell(step).factory())
                 or (unit.role.NAME == 'cow' and unit.role.repair and unit.type == 'HEAVY')
                 or (unit.role.NAME == 'attacker' and unit.role.low_power_target)
                 or unit.role.NAME == 'water_transporter'
                 or unit.role.NAME == 'blockade'
                 #or unit.role.NAME == 'generator' # would necessarily be very nearby so ok
                 or unit.role.NAME == 'protector'
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'miner'
                     and unit.role.resource_cell.ice
                     and unit.type == 'HEAVY'
                     and unit.role.factory.water[i] < 200
                     and len([u for u in unit.role.factory.units(step)
                              if (u.type == 'HEAVY' and u.role
                                  and u.role.NAME == 'miner' and u.role.resource_cell.ice)]) == 1))):
            return

        cur_cell = unit.cell(step)
        player_factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        # Pursue units antagonizing own water transporters
        for player_unit in board.player.units():
            if (player_unit.role
                and player_unit.role.NAME == 'water_transporter'):
                opp_ant = player_unit.is_antagonized(step)
                if (opp_ant
                    and unit.type == opp_ant.type
                    and unit.power[i] >= opp_ant.power[i]
                    and not opp_ant.cell(step).factory()
                    and not opp_ant.assigned_unit(step)
                    and cur_cell.man_dist(opp_ant.cell(step)) < 15):
                    log(f'{unit} attack {opp_ant} antagonizing wt {player_unit}')
                    return RoleAttacker(step, unit, player_factory, opp_ant, low_power_target=1)

        # Pursue units carrying water
        for opp_unit in board.opp.units():
            if (opp_unit.water[i] >= 5
                and unit.type == opp_unit.type
                and unit.power[i] >= opp_unit.power[i]
                and not opp_unit.cell(step).factory()
                and not opp_unit.assigned_unit(step)
                and cur_cell.man_dist(opp_unit.cell(step)) < 15):
                log(f'{unit} attack {opp_unit} with {opp_unit.water[i]} water')
                return RoleAttacker(step, unit, player_factory, opp_unit, low_power_target=1)

    @classmethod
    @profileit
    def from_transition_attack_low_power_unit(cls, step, unit):
        board = unit.board
        i = step - board.step
        if i != 0:
            return

        # role_blockade handled below
        if (unit.role
            and ((unit.role.NAME == 'recharge' and not unit.cell(step).factory())
                 or (unit.role.NAME == 'cow' and unit.role.repair and unit.type == 'HEAVY')
                 or (unit.role.NAME == 'attacker' and unit.role.low_power_target)
                 or unit.role.NAME == 'water_transporter'
                 #or unit.role.NAME == 'generator' # would necessarily be very nearby so ok
                 or unit.role.NAME == 'protector'
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'miner'
                     and unit.role.resource_cell.ice
                     and unit.type == 'HEAVY'
                     and unit.role.factory.water[i] < 200
                     and len([u for u in unit.role.factory.units(step)
                              if (u.type == 'HEAVY' and u.role
                                  and u.role.NAME == 'miner' and u.role.resource_cell.ice)]) == 1))):
            return

        cur_cell = unit.cell(step)
        player_factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        for opp_unit in board.opp.units():
            if (not opp_unit.low_power
                or unit.type != opp_unit.type
                or opp_unit.assigned_unit(step)):
                continue

            destruct_cost = (opp_unit.cfg.SELF_DESTRUCT_COST
                             if opp_unit.type == 'LIGHT'
                             else opp_unit.cfg.DIG_COST)
            if step >= 980 and opp_unit.power[0] < destruct_cost:
                continue

            # Don't chase after chain miners receiving power from lights
            # TODO: compare opp_dist to nearest_opp_light.man_dist(opp_cell)?
            opp_cell = opp_unit.cell(step)
            if (opp_unit.type == 'HEAVY'
                and (opp_cell.ice or opp_cell.ore)):
                continue

            # Only ok to transition blockade unit if its mission continues
            if unit.role and unit.role.NAME == 'blockade':
                if opp_unit is not unit.role.target_unit:
                    continue
                min_rubble = min([c.rubble[0] for c in opp_cell.neighbors()])
                move_cost = (opp_unit.cfg.MOVE_COST
                             + math.floor(opp_unit.cfg.RUBBLE_MOVEMENT_COST * min_rubble))
                if opp_unit.power[0] >= move_cost:
                    continue

            # Don't run across the map for heavy kills toward the end of the game
            cur_cell_opp_dist = cur_cell.man_dist(opp_cell)
            if (step >= 900
                and unit.type == 'HEAVY'
                and cur_cell_opp_dist > 10):
                continue

            # Don't go for any kill that you wouldn't be able to return from before the game ends
            if 2 * cur_cell_opp_dist > 1000 - step:
                continue

            # If opp unit can reach safety before I can get to its nearest factory, pass
            steps_until_safe = opp_unit.steps_until_power(opp_unit.low_power_threshold) + 1
            cutoff_cell = opp_unit.low_power_route[-2]
            cur_cell_cutoff_dist = cur_cell.man_dist(cutoff_cell)
            if cur_cell_cutoff_dist >= steps_until_safe:
                continue

            # Need to determine the exact (estimated) cutoff point
            opp_power = opp_unit.power[i]
            route_idx, pursuit_step, steps_delayed = 1, step, 0
            while steps_delayed < cur_cell_opp_dist and opp_cell is not cutoff_cell:
                # Determine next cell
                next_cell = opp_unit.low_power_route[route_idx]
                # Move
                power_to_move = math.floor(opp_unit.cfg.MOVE_COST
                                           + (opp_unit.cfg.RUBBLE_MOVEMENT_COST
                                              * next_cell.rubble[i]))
                # Power decrement
                if opp_power >= power_to_move:
                    opp_cell = next_cell
                    opp_power -= power_to_move
                    route_idx += 1
                else:
                    steps_delayed += 1
                # Power gain
                opp_power += opp_unit.power_gain(pursuit_step)
                # Increment step
                pursuit_step += 1

            # TODO: check if unit can/should go get power first and pass that as init goal to cnstr
            #        Maybe a separate (lower priority) transition constructor(/arg) for that?
            opp_cell, cutoff_cell = opp_unit.cell(step), opp_cell
            unit_power = unit.power[i]
            if unit.role and unit.role.NAME == 'blockade' and opp_unit.water[i] >= 5:
                naive_power_threshold = (
                    unit.cfg.ACTION_QUEUE_POWER_COST
                    + board.naive_cost(step, unit, cur_cell, opp_cell)
                    + board.naive_cost(step, unit, opp_cell, cutoff_cell)
                )
            else:
                naive_power_threshold = (
                    3 * unit.cfg.ACTION_QUEUE_POWER_COST
                    + board.naive_cost(step, unit, cur_cell, opp_cell)
                    + board.naive_cost(step, unit, opp_cell, cutoff_cell)
                    + board.naive_cost(step, unit, cutoff_cell, player_factory.cell(),is_factory=True))
            if unit_power > naive_power_threshold:
                return RoleAttacker(step, unit, player_factory, opp_unit, low_power_target=1)

    @classmethod
    @profileit
    def from_transition_attack_with_sidekick(cls, step, unit):
        board = unit.board
        i = step - board.step
        if i != 0:
            return

        role_transition_cond = lambda u: not (
            unit.role
            and ((unit.role.NAME == 'recharge' and not unit.cell(step).factory())
                 or (unit.role.NAME == 'cow' and unit.role.repair and unit.type == 'HEAVY')
                 or unit.role.NAME == 'attacker'
                 or unit.role.NAME == 'sidekick'
                 or unit.role.NAME == 'water_transporter'
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'blockade' and unit.role.partner)
                 or unit.role.NAME == 'generator'
                 or unit.role.NAME == 'protector'
                 or (unit.role.NAME == 'relocate'
                     and unit.role.target_factory.mode
                     and unit.role.target_factory.mode.NAME == 'ice_conflict')
                 or (unit.role.NAME == 'miner'
                     and unit.role.resource_cell.ice
                     and unit.type == 'HEAVY'
                     and unit.role.factory.water[i] < 300
                     and len([u for u in unit.role.factory.units(step)
                              if (u.type == 'HEAVY' and u.role
                                  and u.role.NAME == 'miner' and u.role.resource_cell.ice)]) == 1)))
        if not role_transition_cond(unit):
            return

        cur_cell = unit.cell(step)
        player_factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        # Check neighbors for a friendly and an opp in a sidekick attack configuration.
        opp_neighbors, player_neighbors = [], []
        for neighbor in cur_cell.neighbors():
            other_unit = neighbor.unit(step)
            if not other_unit or unit.type != other_unit.type:
                continue
            if other_unit.player_id == unit.player_id:
                if role_transition_cond(other_unit):
                    player_neighbors.append(other_unit)
            else:
                if (not other_unit.cell(step).factory()
                    and not other_unit.assigned_unit(step)):
                    opp_neighbors.append(other_unit)

        opp_neighbors.sort(key=lambda u: u.power[0])
        player_neighbors.sort(key=lambda u: u.power[0], reverse=True)
        for opp_unit in opp_neighbors:
            destruct_cost = (opp_unit.cfg.SELF_DESTRUCT_COST
                             if opp_unit.type == 'LIGHT'
                             else opp_unit.cfg.DIG_COST)
            if step >= 980 and opp_unit.power[0] < destruct_cost:
                continue

            for player_unit in player_neighbors:
                # Only ok to transition blockade unit if its mission continues
                if (((unit.role and unit.role.NAME == 'blockade')
                     or (player_unit.role and player_unit.role.NAME == 'blockade'))
                    and opp_unit.water[0] < 5):
                    continue
                if RoleSidekick.in_position(step, player_unit, unit, opp_unit):
                    sidekick_factory = (player_unit.assigned_factory
                                        or player_unit.cell(step).nearest_factory(board))
                    sidekick_new_role = RoleSidekick(
                        step, player_unit, sidekick_factory, unit, opp_unit)
                    player_unit.set_role(step, role=sidekick_new_role)
                    return RoleAttacker(
                        step, unit, player_factory, opp_unit, sidekick_unit=player_unit)

    @classmethod
    @profileit
    def from_transition_defend_territory(cls, step, unit, max_count=None):
        board = unit.board
        i = step - board.step
        if i != 0:
            return

        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        if max_count:
            like_attackers = [u for u in factory.units(step)
                              if u.type == unit.type and u.role and u.role.NAME == 'attacker']
            if 1 + len(like_attackers) > max_count:
                return

        # Only do this if opp has many lights or during endgame
        #opp_light_count = len([u for u in board.opp.units() if u.type == 'LIGHT'])
        #opp_factory_count = len([f for f in board.opp.factories()])
        #opp_lights_per_factory = opp_light_count / opp_factory_count
        #if opp_lights_per_factory <= 15 and step < C.END_PHASE:
        #    return

        heavy_count = len([u for u in factory.units(step)
                           if (u.type == 'HEAVY'
                               and (not u.role
                                    or u.role.NAME not in ('generator', 'attacker')))])
        role_transition_cond = lambda u: not (
            unit.role
            and ((unit.role.NAME == 'recharge' and not unit.cell(step).factory())
                 or (unit.role.NAME == 'cow' and unit.role.repair)
                 or unit.role.NAME == 'attacker'
                 or unit.role.NAME == 'sidekick'
                 or unit.role.NAME == 'water_transporter'
                 or unit.role.NAME == 'protector'
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'generator' and step < C.END_PHASE)
                 or (unit.role.NAME == 'relocate'
                     and unit.role.target_factory.mode
                     and unit.role.target_factory.mode.NAME == 'ice_conflict')
                 or (unit.type == 'HEAVY'
                     and unit.role.get_factory().mode
                     and unit.role.get_factory().mode.NAME == 'ice_conflict')
                 or (unit.role.NAME == 'miner'
                     and unit.role.resource_cell.ore
                     and unit.type == 'HEAVY')
                 or (unit.type == 'HEAVY'
                     and heavy_count == 1
                     and unit.role.factory.water[i] < 250)
                 or (unit.role.NAME == 'miner'
                     and unit.role.resource_cell.ice
                     and unit.type == 'HEAVY'
                     and unit.role.factory.water[i] < 250
                     and len([u for u in unit.role.factory.units(step)
                              if (u.type == 'HEAVY' and u.role
                                  and u.role.NAME == 'miner' and u.role.resource_cell.ice)]) == 1)))
        if not role_transition_cond(unit):
            return

        if (not unit.role
            and heavy_count == 1
            and factory.water[i] < 250):
            return

        best_unit, best_score = None, C.UNREACHABLE
        for opp_unit in board.opp.units():
            opp_cell = opp_unit.cell(step)
            opp_dist = opp_cell.man_dist(cur_cell)
            if (unit.type != opp_unit.type
                or opp_dist >= 10
                or opp_cell.factory()
                or opp_unit.assigned_unit(step)):
                continue

            destruct_cost = (opp_unit.cfg.SELF_DESTRUCT_COST
                             if opp_unit.type == 'LIGHT'
                             else opp_unit.cfg.DIG_COST)
            if step >= 980 and opp_unit.power[0] < destruct_cost:
                continue

            if (opp_unit.water[0] >= 5
                # Reduce oscillating validity by requiring proximity AND lichen
                or (any((c.lichen_strain[i] == factory.id
                         or c.factory() is factory)
                        for c in ([opp_cell] + opp_cell.neighbors()))
                    and (opp_cell.man_dist_factory(factory)
                         < opp_cell.nearest_factory_dist(board, player_id=board.opp.id)))):
                # Want small opp_dist
                #      small power
                #      carrying water (to assist blockade)
                score = opp_dist + 3 * opp_unit.power[i] / opp_unit.cfg.BATTERY_CAPACITY
                if opp_unit.water[0] >= 5:
                    score -= 10
                if score < best_score:
                    best_unit, best_score = opp_unit, score
        if best_unit:
            return RoleAttacker(step, unit, factory, best_unit, defender=1)

    def is_valid(self, step):
        board = self.unit.board
        i = step - board.step
        opp_cell = self.target_unit.cell(board.step) if self.target_unit else None

        if i == 0 and step >= 980 and self.target_unit and self.defender:
            destruct_cost = (self.target_unit.cfg.SELF_DESTRUCT_COST
                             if self.target_unit.type == 'LIGHT'
                             else self.target_unit.cfg.DIG_COST)
            if self.target_unit.power[0] < destruct_cost:
                return False

        is_valid = (self.factory
                    and self.target_unit
                    and not opp_cell.factory()
                    and (self.low_power_target
                         or (self.defender
                             and (self.target_unit.water[0] >= 5
                                  or any((c.lichen_strain[i] == self.factory.id
                                          or c.factory() is self.factory)
                                         for c in ([opp_cell] + opp_cell.neighbors()))
                                  or (opp_cell.man_dist_factory(self.factory)
                                      <= opp_cell.nearest_factory_dist(board,
                                                                       player_id=board.opp.id))))
                         or (self.sidekick_unit
                             and self.sidekick_unit.role
                             and self.sidekick_unit.role.NAME == 'sidekick')))  # May notice step late

        if is_valid:
            factory_water = (self.factory.water[i]
                             + self.factory.ice[i] // board.env_cfg.ICE_WATER_RATIO)
            if (factory_water < 40
                and self.unit.type == 'HEAVY'
                and self.defender):
                ice_miners = [u for u in self.factory.units(step)
                              if (u.type == 'HEAVY'
                                  and (not u.role
                                       or (u.role
                                           and u.role.NAME == 'miner'
                                           and u.role.resource_cell.ice)))]
                if len(ice_miners) == 0:
                    if i == 0:
                        log(f'attacker invalidate {self.unit} water={factory_water}')
                    is_valid = False

        # Switch into low power target mode if possible
        if (i == 0
            and is_valid
            and self.target_unit
            and self.target_unit.low_power
            and not self.low_power_target):
            self.low_power_target = 1
            self.defender = 0
            self.sidekick_unit = None

        return is_valid

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)
        self.target_unit.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            self.target_unit.unset_assignment(step, self.unit)

    def goal_cell(self, step):
        # Possible for factory and target_unit to be invalidated same step
        # Handle this function being called before the role is officially invalidated.
        if self.goal == 'temp invalid':
            return self.unit.cell(step)

        # Temporarily override goal cell if on factory center
        if self.unit.cell(step) is self.get_factory().cell():
            return self.target_unit.cell(self.unit.board.step)

        if self.goal.type == 'FACTORY':
            return self.goal.cell()

        # Just return opp's current location
        return self.goal.cell(self.unit.board.step)

        # TODO: This still seems promising
        #       Probably want to track toward midpoint until distance from route/opp-factory line
        #       is less than ~3-4, then track toward opp. Just want to avoid oscillating.
        # Goal is a unit. Try to cut them off on their way to their nearest factory.
        board = self.unit.board
        cur_cell = self.unit.cell(step)
        opp_cell = self.goal.cell(board.step)
        nearest_opp_factory = opp_cell.nearest_factory(board, player_id=board.opp.id)
        mid_cell = board.cell((opp_cell.x + nearest_opp_factory.cell().x) // 2,
                              (opp_cell.y + nearest_opp_factory.cell().y) // 2)
        mid_cell_dist = mid_cell.man_dist(cur_cell)
        opp_cell_dist = opp_cell.man_dist(cur_cell)

        # Track toward the mid cell until you're close to it or the target unit.
        if 2 < mid_cell_dist < opp_cell_dist:
            return mid_cell
        return opp_cell

    def update_goal(self, step):
        assert self.goal
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        unit_power = self.unit.power[i]
        target_cell = self.target_unit.cell(board.step)

        if self.goal is self.target_unit:
            # Handled by RoleRecharge
            pass
        elif self.goal is self.factory:
            if self.factory.power[i] >= 5000:
                power_threshold = 2900
            else:
                power_threshold = (self.unit.cfg.ACTION_QUEUE_POWER_COST
                                   + board.naive_cost(step, self.unit, cur_cell, target_cell)
                                   + 20 * self.unit.cfg.ACTION_QUEUE_POWER_COST
                                   + 20 * self.unit.cfg.MOVE_COST
                                   + board.naive_cost(
                                       step, self.unit, target_cell,
                                       self.factory.cell(), is_factory=True))
            power_threshold = min(power_threshold, self.unit.cfg.BATTERY_CAPACITY)
            if unit_power >= power_threshold:
                self.goal = self.target_unit
        else:
            assert False

    def do_move(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        opp_cell = self.target_unit.cell(board.step)

        # If we've arrived at opp cell after traveling a bit, who knows what that'll look like, so lie
        if (cur_cell is opp_cell
            and self.unit.cell(board.step).man_dist(opp_cell) >= 4):
            self.unit.set_lie_step(step)
            return

        return self._do_move(step)

    def do_dig(self, step):
        return

    def do_pickup(self, step):
        return self._do_power_pickup(step)

    def do_transfer(self, step):
        return self._do_transfer_resource_to_factory(step)

    def get_factory(self):
        return self.factory
