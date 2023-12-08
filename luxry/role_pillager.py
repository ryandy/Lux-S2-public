import math
import sys

from .cell import Cell
from .role import Role
from .role_recharge import RoleRecharge
from .util import C, Resource, log, profileit


class RolePillager(Role):
    '''Pillage lichen around opponent factories'''
    NAME = 'pillager'

    def __init__(self, step, unit, factory, lichen_cell, one_way=False, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.lichen_cell = lichen_cell
        self.one_way = 1 if one_way else 0
        self.goal = goal

        if self.goal is None:
            if one_way:
                self.goal = lichen_cell
            else:
                cur_cell = unit.cell(step)
                factory_dist = cur_cell.man_dist_factory(factory) if factory else C.UNREACHABLE
                lichen_cell_dist = cur_cell.man_dist(lichen_cell)
                self.goal = lichen_cell if lichen_cell_dist < factory_dist else factory

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        lgoal = '*' if self.goal is self.lichen_cell else ''
        ptype = '(1w)' if self.one_way else ''
        return f'Pillager[{self.factory}{fgoal} -> {self.lichen_cell}{ptype}{lgoal}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.lichen_cell),
                self.serialize_obj(self.one_way),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        lichen_cell = cls.deserialize_obj(unit.board, role_data[2])
        one_way = cls.deserialize_obj(unit.board, role_data[3])
        goal = cls.deserialize_obj(unit.board, role_data[4])
        return RolePillager(step, unit, factory, lichen_cell, one_way=one_way, goal=goal)

    @classmethod
    def _handle_displaced_unit(cls, step, cell):
        displaced_unit = cell.assigned_unit(step)
        if displaced_unit:
            if displaced_unit.role and displaced_unit.role == 'pillager':
                role = displaced_unit.role
                nearest_opp_factory = role.lichen_cell.nearest_factory(
                    cell.board, player_id=cell.board.opp.id)
                role.lichen_cell = nearest_opp_factory.cell()

                displaced_unit.unset_role(step)
                displaced_unit.set_role(step, role=role)
            else:
                displaced_unit.unset_role(step)

    @classmethod
    def _dest_is_safe(cls, step, unit, cell):
        end_phase = (step >= C.END_PHASE
                     or unit.role and unit.role.NAME == 'pillager' and unit.role.one_way)
        return end_phase or Role._dest_is_safe(step, unit, cell)

    @classmethod
    def _cell_score(cls, step, unit, cell):
        end_phase = (step >= C.END_PHASE
                     or unit.role and unit.role.NAME == 'pillager' and unit.role.one_way)

        if not end_phase and cell.lichen_dist is None:
            return 0

        board = unit.board
        i = step - board.step
        cell_lichen = cell.lichen[i]
        if cell.lichen_dist is None:
            cell_lichen -= (1000 - step)
        if cell_lichen <= 0:
            return 0

        cur_cell = unit.cell(step)
        player_factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        dist_unit_to_cell = cur_cell.man_dist(cell)
        dist_cell_to_factory = (0
                                if end_phase
                                else cell.man_dist_factory(player_factory))
        digs_necessary = math.ceil(cell_lichen / unit.cfg.DIG_LICHEN_REMOVED)

        if (not end_phase
            and dist_unit_to_cell < cur_cell.man_dist_factory(player_factory)):
            # Confirm unit has enough power to dig through target cell
            power_threshold = (
                unit.cfg.ACTION_QUEUE_POWER_COST
                + 3 * unit.cfg.MOVE_COST
                + unit.cfg.ACTION_QUEUE_POWER_COST
                #+ board.naive_cost(step, unit, cur_cell, cell)
                + 1.5 * unit.cfg.MOVE_COST * dist_unit_to_cell
                + unit.cfg.DIG_COST * digs_necessary
                #+ board.naive_cost(step, unit, cell, player_factory.cell(), is_factory=True))
                + 1.5 * unit.cfg.MOVE_COST * dist_cell_to_factory)
            if unit.power[i] < power_threshold:
                return 0

        cost = ((unit.cfg.MOVE_COST * dist_unit_to_cell)
                + (unit.cfg.DIG_COST * digs_necessary)
                + (0.25 * unit.cfg.MOVE_COST * dist_cell_to_factory))

        traffic = 0
        for c in [cell] + cell.neighbors():
            lt, ht = c.traffic()
            if unit.type == 'HEAVY':
                traffic += ht
            else:
                traffic += lt + ht
            if c.assigned_unit(step):
                traffic += 0.01
        traffic = traffic/5
        traffic = min(0.2, traffic) * 5

        cell_value = 1
        if end_phase:
            cell_value += cell_lichen/100
        else:
            if cell.lichen_bottleneck:  # Encourage attacking bottlenecks
                cell_value += 1
            if cell in player_factory.lichen_frontier_cells:  # Stop growth
                cell_value += 1

        # TODO: A/B test
        if cell.x%2 == cell.y%2:  # Grids might be nice
            cell_value += 0.5

        # TODO: A/B test
        #if cell.lichen_dist is not None and 3 <= cell.lichen_dist <= 4:
        #    cell_value += 1.5  # or 2?

        cell_value *= (1.1 - traffic)

        return cell_value / cost

    @classmethod
    @profileit
    def from_one_way(cls, step, unit):
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)

        if (not unit.type == 'LIGHT'
            or not unit.power[i] == 50
            or not cur_cell.factory_center):
            return

        player_factory = cur_cell.factory()
        light_lim = C.LIGHT_LIM + (step // 100)
        #light_lim = min(light_lim, C.LIGHT_LIM + 2)
        light_count = len([u for u in player_factory.units(step)
                           if u.type == 'LIGHT' and (not u.role or u.role.NAME != 'relocate')])

        if light_count > light_lim:
            return cls.from_lichen_cell_count(step, unit, one_way=True)

    @classmethod
    @profileit
    def from_lichen_cell_count(cls, step, unit,
                               max_dist=100, max_lichen=100, max_count=None, one_way=False):
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        player_factory = unit.assigned_factory or cur_cell.nearest_factory(board)
        steps_remaining = 1000 - step
        end_phase = (step >= C.END_PHASE
                     or unit.role and unit.role.NAME == 'pillager' and unit.role.one_way)

        # Modify max_dist for transitions
        if max_dist < 100 and unit.role and unit.role.NAME == 'pillager':
            max_dist = unit.role.lichen_cell.man_dist_factory(player_factory) + 5

        if max_count:
            like_pillagers = [u for u in player_factory.units(step)
                              if u.type == unit.type and u.role and u.role.NAME == 'pillager']
            if 1 + len(like_pillagers) > max_count:
                return

        cells = []
        for factory in board.opp.factories():
            cells.extend(factory.lichen_connected_cells)
        if end_phase:
            cells.extend([c for c in board.opp.lichen_disconnected_cells
                          if c.lichen[i] > 100 - step])

        cur_near_player_factory = cur_cell.man_dist_factory(player_factory) <= max_dist
        best_cell, best_score = None, 0
        for cell in cells:
            if (cell.lichen[i] > max_lichen
                # Target cell cannot be too far from home factory
                or cell.man_dist_factory(player_factory) > max_dist
                # Unit must be near home factory or target cell
                or (not cur_near_player_factory and cell.man_dist(cur_cell) > max_dist)
                or (not cls._dest_is_safe(step, unit, cell) and not end_phase)):
                continue

            cur_cell_dist = cur_cell.man_dist(cell)
            assigned_unit = cell.assigned_unit(step)
            if (cur_cell_dist <= steps_remaining - 1
                and (not assigned_unit
                     or (unit.type == 'HEAVY' and assigned_unit.type == 'LIGHT')
                     or (step >= 980  # Last night
                         and unit.type == assigned_unit.type
                         and unit.power[i] >= unit.cfg.DIG_COST
                         and (assigned_unit.power[i] < assigned_unit.cfg.DIG_COST
                              or cur_cell_dist < assigned_unit.cell(step).man_dist(cell))))):
                score = cls._cell_score(step, unit, cell)
                if score > best_score:
                    best_cell, best_score = cell, score

        if best_cell:
            cls._handle_displaced_unit(step, best_cell)
            return RolePillager(step, unit, player_factory, best_cell, one_way=one_way)

        # During endphase, assign units to opp boundary cells after all lichen is assigned
        if not end_phase:
            return

        # Only bounday camp as a heavy if we've already scanned the whole board
        if unit.type == 'HEAVY' and max_dist < 100:
            return

        # For transitions, if already assigned to boundary cell, no need to continue
        if (unit.role
            and unit.role.NAME == 'pillager'
            and unit.role.lichen_cell.nearest_factory_dist(board, player_id=board.opp.id) == 1):
            return

        # During endgame also check for dist-1 boundary cells
        cells = []
        for factory in board.opp.factories():
            boundary_cells = [c for c in (factory.lichen_flat_boundary_cells
                                          + factory.lichen_rubble_boundary_cells)
                              if (c.man_dist_factory(factory) == 1
                                  and c.rubble[i] <= 20)]
            cells.extend(boundary_cells)

        best_cell, best_score = None, -C.UNREACHABLE
        for cell in cells:
            if (
                # Target cell cannot be too far from home factory
                cell.man_dist_factory(player_factory) > max_dist
                # Unit must be near home factory or target cell
                or (not cur_near_player_factory and cell.man_dist(cur_cell) > max_dist)):
                continue

            cur_cell_dist = cur_cell.man_dist(cell)
            assigned_unit = cell.assigned_unit(step)
            if (cur_cell_dist <= steps_remaining - 1
                and (not assigned_unit
                     or (unit.type == 'HEAVY' and assigned_unit.type == 'LIGHT')
                     or (step >= 980  # Last night
                         and unit.type == assigned_unit.type
                         and unit.power[i] >= unit.cfg.DIG_COST
                         and (assigned_unit.power[i] < assigned_unit.cfg.DIG_COST
                              or cur_cell_dist < assigned_unit.cell(step).man_dist(cell))))):
                score = -cur_cell_dist
                if score > best_score:
                    best_cell, best_score = cell, score

        if best_cell:
            if i == 0:
                log(f'{unit} endgame boundary pillager {best_cell}')
            cls._handle_displaced_unit(step, best_cell)
            return RolePillager(step, unit, player_factory, best_cell, one_way=one_way)

    # TODO: cost/dist is way too high sometimes
    @classmethod
    @profileit
    def from_lichen_frontier(cls, step, unit, max_dist=100, max_lichen=100, max_count=None):
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        player_factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        # Modify max_dist for transitions
        if max_dist < 100 and unit.role and unit.role.NAME == 'pillager':
            max_dist = unit.role.lichen_cell.man_dist_factory(player_factory) + 10

        if max_count:
            like_pillagers = [u for u in player_factory.units(step)
                              if u.type == unit.type and u.role and u.role.NAME == 'pillager']
            if 1 + len(like_pillagers) > max_count:
                return

        cells = []
        for factory in board.opp.factories():
            cells.extend([c for c in factory.lichen_connected_cells if c.lichen_bottleneck])
            cells.extend(factory.lichen_frontier_cells)

        cur_near_player_factory = cur_cell.man_dist_factory(player_factory) <= max_dist
        best_cell, min_cost = None, C.UNREACHABLE
        for cell in cells:
            #if (i == 0
            #    and unit.id in (17,21)
            #    and cell.lichen[i] > 0):
            #    log(f'{unit} considering lichen_frontier {cell}: {cell.assigned_unit(step)}'
            #        f' {cls._dest_is_safe(step, unit, cell)} {max_dist}')
            if (cell.lichen[i] > max_lichen
                or cell.assigned_unit(step)
                or not cls._dest_is_safe(step, unit, cell)):
                continue

            # Target cell cannot be too far from home factory
            if cell.man_dist_factory(player_factory) > max_dist:
                #if (i == 0
                #    and unit.id in (17,21)):
                #    log(f'{unit} NOT considering lichen_frontier A {cell}:'
                #        f' {cell.man_dist_factory(player_factory)}')
                continue

            # Unit must be near home factory or target cell
            if not cur_near_player_factory and cell.man_dist(cur_cell) > max_dist:
                #if (i == 0
                #    and unit.id in (17,21)):
                #    log(f'{unit} NOT considering lichen_frontier B {cell}: '
                #        f' {cur_near_player_factory}'
                #        f' {cell.man_dist(cur_cell)}')
                continue

            dist_unit_to_cell = cur_cell.man_dist(cell)
            dist_cell_to_factory = (cell.man_dist_factory(player_factory)
                                    if step < C.END_PHASE
                                    else 0)  # No need to consider during endgame
            cell_lichen = cell.lichen[i]
            cost = ((unit.cfg.MOVE_COST * dist_unit_to_cell)
                    + (unit.cfg.DIG_COST * math.ceil(cell_lichen / unit.cfg.DIG_LICHEN_REMOVED))
                    + (unit.cfg.MOVE_COST * dist_cell_to_factory))
            if cell.lichen_bottleneck:  # Encourage attacking bottlenecks
                cost *= 0.8
            if cost < min_cost:
                best_cell, min_cost = cell, cost
        if best_cell:
            return RolePillager(step, unit, player_factory, best_cell)

    # TODO: cost/dist is way too high sometimes
    @classmethod
    @profileit
    def from_lichen_forest(cls, step, unit, max_dist=100):
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        player_factory = unit.assigned_factory or cur_cell.nearest_factory(board)
        steps_remaining = 1000 - step

        # Modify max_dist for transitions
        if max_dist < 100 and unit.role and unit.role.NAME == 'pillager':
            max_dist = unit.role.lichen_cell.man_dist_factory(player_factory) + 10

        cells = [c for c in board.opp.lichen_disconnected_cells
                 if step < C.END_PHASE or c.lichen[i] > steps_remaining]
        for factory in board.opp.factories():
            cells.extend(factory.lichen_connected_cells)

        # Find a high-lichen cell to attack
        best_cell, max_score = None, -C.UNREACHABLE
        for cell in cells:
            cur_cell_dist = cur_cell.man_dist(cell)
            assigned_unit = cell.assigned_unit(step)
            if ((not assigned_unit
                 or (unit.type == 'HEAVY' and assigned_unit.type == 'LIGHT')
                 or (step >= 980  # Last night
                     and unit.type == assigned_unit.type
                     and unit.power[i] >= unit.cfg.DIG_COST
                     and (assigned_unit.power[i] < assigned_unit.cfg.DIG_COST
                          or cur_cell_dist < assigned_unit.cell(step).man_dist(cell))))
                and cls._dest_is_safe(step, unit, cell)):

                # Target cell cannot be too far from home factory
                if ((cell.man_dist_factory(player_factory) > max_dist)
                    or (cur_cell_dist > steps_remaining - 1)):
                    continue

                lichen_value = cell.lichen[i]
                if cell.lichen_dist:  # Encourage attacking cells near factory
                    lichen_value += 5 * (5 - cell.lichen_dist)
                if cell.lichen_bottleneck:  # Encourage attacking bottlenecks
                    lichen_value += 25
                score = lichen_value / (1 + cur_cell_dist)
                if score > max_score:
                    best_cell, max_score = cell, score
        if best_cell:
            cls._handle_displaced_unit(step, best_cell)
            return RolePillager(step, unit, player_factory, best_cell)

    # TODO: cost/dist is way too high sometimes
    @classmethod
    @profileit
    def from_transition_active_pillager(cls, step, unit, max_dist=100, max_lichen=100):
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        steps_remaining = 1000 - step

        if not unit.role or unit.role.NAME != 'pillager':
            return

        if unit.role.lichen_cell.lichen[i]:
            # TODO: transition if antagonized or unsafe? (can be quite slow)
            #or not unit.role._dest_is_safe(step, unit, unit.role.lichen_cell)
            #or (i == 0
            #    and cur_cell.man_dist(unit.role.lichen_cell) <= 1
            #    and unit.is_antagonized(step))):
            return

        # Displaced units can be set to a nearby factory center
        max_radius = 4 if unit.role.lichen_cell.factory_center else 2

        # Current lichen cell is barren, but we can maintain validity by moving to another cell.
        for radius in range(1, max_radius+1):
            best_cell, best_score = None, 0
            for neighbor, _ in unit.role.lichen_cell.radius_cells(radius=radius):
                cur_cell_dist = cur_cell.man_dist(neighbor)
                if (neighbor.lichen[i] > 0
                    and neighbor.lichen_strain[i] in board.opp.strains
                    and (not neighbor.assigned_unit(step)
                         or (unit.type == 'HEAVY' and neighbor.assigned_unit(step).type == 'LIGHT'))
                    and cur_cell_dist < steps_remaining - 1):
                    #and cls._dest_is_safe(step, unit, neighbor)):
                    score = cls._cell_score(step, unit, neighbor)
                    if score > best_score:
                        best_cell, best_score = neighbor, score
            if best_cell:
                cls._handle_displaced_unit(step, best_cell)
                return RolePillager(step, unit, unit.role.factory, best_cell)

        end_phase = (step >= C.END_PHASE
                     or unit.role and unit.role.NAME == 'pillager' and unit.role.one_way)

        # This can return None, which means the unit will maintain its pillager role w/ no lichen
        if end_phase:
            new_role = (
                None
                or RolePillager.from_lichen_cell_count(step, unit, max_dist=max_dist)
            )
        else:
            new_role = (
                None
                or RolePillager.from_lichen_cell_count(step, unit, max_dist=max_dist)
                or RoleRecharge(step, unit, unit.role.factory)
            )
        return new_role

    # TODO Can be very slow
    # TODO transition to recharge at END_PHASE instead
    #      that way unit will go to get power and will be re-prioritized correctly
    #      e.g. we may want to switch an ore miner to ice mining rather than pillager at 900
    #      or maybe better yet, switch factory modes and invalidate most unit roles
    @classmethod
    @profileit
    def from_transition_end_of_game(cls, step, unit):
        if step < C.END_PHASE:
            return

        board = unit.board
        i = step - board.step

        if (unit.role
            and ((unit.role.NAME == 'recharge' and not unit.cell(step).factory())
                 or (unit.role.NAME == 'cow' and unit.role.repair)
                 or unit.role.NAME == 'attacker'
                 or unit.role.NAME == 'sidekick'
                 or unit.role.NAME == 'protector'
                 or (unit.role.NAME == 'miner' and unit.role.resource_cell.ice)
                 or (unit.role.NAME == 'miner' and unit.role.resource_cell.ore and unit.ore[i])
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.target_cell.ice
                     and unit.role.target_cell.man_dist_factory(unit.role.get_factory()) < 10)
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or unit.role.NAME == 'pillager'
                 or (unit.role.NAME == 'relocate'
                     and unit.role.target_factory.mode
                     and unit.role.target_factory.mode.NAME == 'ice_conflict')
                 or (unit.type == 'LIGHT' and unit.role.NAME == 'transporter')
                 or unit.role.NAME == 'water_transporter'
                 or unit.role.NAME == 'blockade')):
            return

        cur_cell = unit.cell(step)
        player_factory = unit.assigned_factory or cur_cell.nearest_factory(unit.board)

        if (unit.role
            and unit.type == 'HEAVY'
            and unit.role.NAME in ('generator', 'transporter')
            and player_factory.power[i] < 3000):
            return

        # If factory lacks a heavy ice miner, don't transition until there is one
        factory_water = player_factory.water[i] + player_factory.ice[i]// board.env_cfg.ICE_WATER_RATIO
        if factory_water < 500:
            heavy_ice_miners = [u for u in player_factory.units(step)
                                if (u.type == 'HEAVY'
                                    and u.role
                                    and u.role.NAME == 'miner'
                                    and u.role.resource_cell.ice)]
            if not heavy_ice_miners:
                return

        # Find a high-lichen near-factory cell to attack toward the end of the game
        player_factory_dist = cur_cell.man_dist_factory(player_factory)
        best_cell, max_score = None, -C.UNREACHABLE
        for cell in unit.board.cells:
            if (cell.lichen[i] > 0
                and cell.lichen_strain[i] in board.opp.strains
                and not cell.assigned_unit(step)
                and cell.lichen[i] > max_score
                and cell.lichen_strain[i] in board.factories):
                lichen_dist = cell.man_dist_factory(player_factory)
                opp_factory_dist = cell.man_dist_factory(board.factories[cell.lichen_strain[i]])
                score = cell.lichen[i] - opp_factory_dist
                if (step + player_factory_dist + lichen_dist + 20 < 1000
                    and score > max_score):
                    best_cell, max_score = cell, score
        if best_cell:
            return RolePillager(step, unit, player_factory, best_cell)

        # Backup: normal pillager
        #return RolePillager.from_lichen_forest(step, unit)
        return RolePillager.from_lichen_cell_count(step, unit)

    def is_valid(self, step):
        i = step - self.unit.board.step

        # Does not necessarily need lichen
        return self.factory
        # and self._dest_is_safe(step, self.unit, self.lichen_cell))

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)
        if not self.lichen_cell.factory_center:
            self.lichen_cell.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            if not self.lichen_cell.factory_center:
                self.lichen_cell.unset_assignment(step, self.unit)

    def goal_cell(self, step):
        # Temporarily override goal cell if on factory center
        if self.unit.cell(step) is self.get_factory().cell():
            return self.lichen_cell

        if isinstance(self.goal, Cell):
            return self.goal

        # Goal is a factory
        return self.goal.cell()

    def update_goal(self, step):
        assert self.goal
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        unit_power = self.unit.power[i]

        if self.goal is self.lichen_cell:
            # Handled by RoleRecharge
            pass
        elif self.goal is self.factory:
            # TODO: different threshold if much closer to lichen cell than factory (same above?)
            if step >= C.END_PHASE and self.factory.power[i] < 500:
                power_threshold = (self.unit.cfg.ACTION_QUEUE_POWER_COST
                                   + self.unit.cfg.SELF_DESTRUCT_COST
                                   + board.naive_cost(step, self.unit, cur_cell, self.lichen_cell))
            elif step >= C.END_PHASE:
                power_threshold = (self.unit.cfg.ACTION_QUEUE_POWER_COST
                                   + 6 * self.unit.cfg.DIG_COST
                                   + board.naive_cost(step, self.unit, cur_cell, self.lichen_cell))
            else:
                power_threshold = (self.unit.cfg.ACTION_QUEUE_POWER_COST
                                   + 6 * self.unit.cfg.DIG_COST
                                   + board.naive_cost(step, self.unit, cur_cell, self.lichen_cell)
                                   + board.naive_cost(
                                       step, self.unit, self.lichen_cell, self.factory.cell()))
            power_threshold = min(power_threshold, self.unit.cfg.BATTERY_CAPACITY)
            if unit_power >= power_threshold:
                self.goal = self.lichen_cell
        else:
            assert False

    def do_move(self, step):
        i = step - self.unit.board.step
        cur_cell = self.unit.cell(step)
        end_phase = (step >= C.END_PHASE or self.one_way)
        # Being assigned to a 0-lichen cell should only happen toward the end of the game.
        # Wait for a do-no-move action so that we can just get out of the way if necessary.
        # TODO: when assigned to 0-lichen and near end-of-game, force a move in the direction
        #       of the nearest cell with lichen or the nearest opp factory. Do not use the normal
        #       unit functions for pathfinding. Collisions encouraged (maybe only if closer to opp
        #       than own)
        if self.one_way:
            if (self.unit.power[i] >= 25
                or self._get_threat_units(cur_cell, history_len=1, max_radius=1, light=True)):
                return self._do_move(step)
        elif self.lichen_cell.lichen[i] > 0:
            return self._do_move(step)
        elif (end_phase
              and self.unit.power[i] >= 6 * self.unit.cfg.MOVE_COST):
            return self._do_move(step)

    def do_dig(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        goal_cell = self.goal_cell(step)
        end_phase = (step >= C.END_PHASE or self.one_way)

        if ((end_phase
             or (self.goal is self.lichen_cell and cur_cell is goal_cell))
            and cur_cell.lichen[i] > 0  # May stay assigned to cell after lichen is gone
            and cur_cell.lichen_strain[i] in board.opp.strains):

            selfdestruct_cost = self.unit.selfdestruct_cost(step)
            if (end_phase
                and step < 970
                and cur_cell.lichen_bottleneck):
                if (2 * selfdestruct_cost > self.unit.power[i] >= selfdestruct_cost
                    and self.unit.type == 'LIGHT'):
                    if i == 0:
                        log(f'{self.unit} pillager endgame bottleneck self-destruct')
                    return self.unit.do_selfdestruct(step)
                if self.unit.power[i] >= self.unit.dig_cost(step):
                    if i == 0:
                        log(f'{self.unit} pillager endgame bottleneck dig')
                    return self.unit.do_dig(step)

            # If endgame, step <= 997, not yet at goal, and threatened, return
            # Keep going to goal, then blow up or get killed there
            if (end_phase
                and step <= 997
                and not cur_cell is goal_cell
                and self._get_threat_units(
                    cur_cell, history_len=1, max_radius=1,
                    heavy=(self.unit.type=='HEAVY'),
                    light=(self.unit.type=='LIGHT'))):
                return

            # If low power and a self-destruct could do more than digging for the rest of the game
            if end_phase and self.unit.type == 'LIGHT':
                turns_remaining = 1000 - step
                digs_remaining = self.unit.power[i] // self.unit.cfg.DIG_COST
                max_lichen_by_digging = ((self.unit.cfg.DIG_LICHEN_REMOVED - 1) *
                                         min(turns_remaining, digs_remaining))
                if cur_cell.lichen[i] > max_lichen_by_digging:
                    if 2 * selfdestruct_cost >= self.unit.power[i] >= selfdestruct_cost:
                        if i == 0:
                            log(f'{self.unit} pillager endgame self-destruct')
                        return self.unit.do_selfdestruct(step)

            if self.unit.power[i] >= self.unit.dig_cost(step):
                return self.unit.do_dig(step)

    def do_pickup(self, step):
        if not self.one_way:
            return self._do_power_pickup(step)

    def do_transfer(self, step):
        return self._do_transfer_resource_to_factory(step)

    def get_factory(self):
        return self.factory
