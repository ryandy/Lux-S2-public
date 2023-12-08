from collections import defaultdict
import math
import sys

from .cell import Cell
from .role import Role
from .util import C, Resource, log, profileit


class RoleCow(Role):
    '''Clear rubble'''
    NAME = 'cow'

    def __init__(self, step, unit, factory, rubble_cell, repair=0, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.rubble_cell = rubble_cell
        self.repair = 1 if repair else 0
        self.goal = goal

        if self.goal is None:
            cur_cell = unit.cell(step)
            factory_dist = cur_cell.man_dist_factory(factory) if factory else C.UNREACHABLE
            rubble_cell_dist = cur_cell.man_dist(rubble_cell)
            self.goal = rubble_cell if rubble_cell_dist < factory_dist else factory

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        rgoal = '*' if self.goal is self.rubble_cell else ''
        repair = 'r' if self.repair else ''
        return f'Cow[{self.factory}{fgoal} -> {self.rubble_cell}{repair}{rgoal}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.rubble_cell),
                self.serialize_obj(self.repair),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        rubble_cell = cls.deserialize_obj(unit.board, role_data[2])
        repair = cls.deserialize_obj(unit.board, role_data[3])
        goal = cls.deserialize_obj(unit.board, role_data[4])
        return RoleCow(step, unit, factory, rubble_cell, repair=repair, goal=goal)

    @classmethod
    @profileit
    def from_transition_lichen_repair(cls, step, unit):
        board = unit.board
        i = step - board.step

        if (i != 0
            or step < 200):
            return

        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)
        if (unit.role
            and ((unit.role.NAME == 'recharge' and not unit.cell(step).factory())
                 or unit.role.NAME == 'blockade'
                 or unit.role.NAME == 'water_transporter'
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'cow' and unit.role.repair)
                 #or (unit.role.NAME == 'generator' and step < C.END_PHASE)
                 or (unit.role.NAME == 'miner'
                     and unit.type == 'HEAVY'
                     and unit.role.resource_cell.ore
                     and unit.ore[i]))):
            return

        factory_water = factory.water[i] + factory.ice[i] // board.env_cfg.ICE_WATER_RATIO
        water_threshold = 200 if C.PHILIPP else 60
        if (factory_water < water_threshold
            and unit.type == 'HEAVY'
            and (not unit.role
                 or (unit.role
                     and unit.role.NAME == 'miner'
                     and unit.role.resource_cell.ice))):
            return

        heavies = [u for u in factory.units(step) if u.type == 'HEAVY']

        # Don't distract a solo ice_conflict heavy
        if (unit.type == 'HEAVY'
            and factory.mode
            and factory.mode.NAME == 'ice_conflict'
            and len(heavies) == 1):
            return

        if (unit.type == 'HEAVY'
            and len(heavies) == 1
            and C.PHILIPP):
            return

        # Has to be somewhat nearby
        if unit.cell(step).man_dist_factory(factory) > 8:
            return

        # Only one heavy at a time
        if unit.type == 'HEAVY':
            cow_limit = 2 if C.PHILIPP else 1
            repair_cows = [u for u in factory.units(step)
                           if (u.type == unit.type
                               and u.role
                               and u.role.NAME == 'cow'
                               and u.role.repair)]
            if len(repair_cows) >= cow_limit:
                return

        max_dist = 3 if C.PHILIPP else 1
        if unit.type == 'LIGHT':
            return (RoleCow.from_factory_radius(
                step, unit, max_dist=max_dist, max_dist_from_unit=8,
                min_rubble=1, max_rubble=4)
                    or RoleCow.from_factory_radius(
                        step, unit, max_dist=max_dist, max_dist_from_unit=8,
                        min_rubble=1, max_rubble=20))
        else:
            return RoleCow.from_factory_radius(
                step, unit, max_dist=max_dist, max_dist_from_unit=8,
                min_rubble=20, max_rubble=20)

    @classmethod
    @profileit
    def from_lichen_repair(cls, step, unit, max_dist=100):
        board = unit.board
        i = step - board.step

        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(unit.board)
        factory_cache = factory.cache()

        # Only one heavy repair cow at a time.
        if unit.type == 'HEAVY':
            cow_limit = 2 if C.PHILIPP else 1
            repair_cows = [u for u in factory.units(step)
                           if (u.type == 'HEAVY'
                               and u.role
                               and u.role.NAME == 'cow'
                               and u.role.repair)]
            if len(repair_cows) >= cow_limit:
                return

        best_cell, min_score = None, C.UNREACHABLE
        for cell_id, pillage_step in reversed(factory_cache.pillage_cell_id_steps):
            if pillage_step < board.step - 50:
                break
            cell = board.cell(0,0,cid=cell_id)
            if (cell.rubble[i] > 0
                and cell.man_dist_factory(factory) <= max_dist
                and (not cell.assigned_unit(step)
                     or (unit.type == 'HEAVY' and cell.assigned_unit(step).type == 'LIGHT'))
                and cls._dest_is_safe(step, unit, cell)):
                score = (cur_cell.man_dist(cell)
                         + math.ceil(cell.rubble[i] / unit.cfg.DIG_RUBBLE_REMOVED))
                if score < min_score:
                    best_cell, min_score = cell, score
        if best_cell:
            cls._handle_displaced_unit(step, best_cell)
            return RoleCow(step, unit, factory, best_cell, repair=1)

    @classmethod
    @profileit
    def from_resource_route(cls, step, unit, ice=False, ore=False,
                            num_routes=1, max_dist=15, max_count=None):
        assert ice or ore

        i = step - unit.board.step
        board = unit.board
        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)

        if max_count:
            factory_cows = [u for u in factory.units(step)
                            if u.type == unit.type and u.role and u.role.NAME == 'cow']
            if 1 + len(factory_cows) > max_count:
                return

        # Give priority to routes currently being mined by heavies
        resource_routes = [route for route in factory.resource_routes
                           if (ice and route[-1].ice) or (ore and route[-1].ore)]
        active_routes, inactive_routes = [], []
        for r in resource_routes:
            if (ice
                and r[-1].assigned_factory
                and r[-1].assigned_factory is not factory):
                continue
            if (r[-1].assigned_unit(step)
                and r[-1].assigned_unit(step).type == 'HEAVY'
                and r[-1].assigned_unit(step).role
                and r[-1].assigned_unit(step).role.NAME == 'miner'):
                active_routes.append(r)
            else:
                inactive_routes.append(r)
        resource_routes = active_routes + inactive_routes

        for resource_route in resource_routes[:num_routes]:
            if len(resource_route) - 1 > max_dist:
                break
            for route_cell in resource_route[1:]:
                if (not route_cell.assigned_unit(step)
                    and route_cell.rubble[i] > 0
                    and route_cell.nearest_factory_dist(board, player_id=board.opp.id) > 2
                    and cls._dest_is_safe(step, unit, route_cell)):
                    return RoleCow(step, unit, factory, route_cell)

    @classmethod
    @profileit
    def from_lowland_route(cls, step, unit, max_dist=8, min_size=6, max_count=None):
        i = step - unit.board.step
        board = unit.board
        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)

        if max_count:
            factory_cows = [u for u in factory.units(step)
                            if u.type == unit.type and u.role and u.role.NAME == 'cow']
            if 1 + len(factory_cows) > max_count:
                return

        # TODO: maybe worth doing if allows lichen spread away from opps?
        for neighbor, _ in factory.radius_cells(1):
            if neighbor.lowland_size >= 100:
                return

        for lowland_route in factory.lowland_routes:
            #if i == 0 and factory.id == 2:
            #    log(f'unit{unit.id} {lowland_route[0]}->{lowland_route[-1]} len={len(lowland_route)}')
            # Check the max number of cells of digging needed
            # TODO: maybe just check the size / rubble ?
            #       In addition to max_dist/min_size, also a max rubble/size ratio
            #       Maybe consider flat vs low too?
            if len(lowland_route) - 2 <= max_dist:
                if lowland_route[-1].lowland_size >= min_size:
                    for route_cell in lowland_route[1:]:
                        if (not route_cell.assigned_unit(step)
                            and route_cell.rubble[i] > 0
                            and cls._dest_is_safe(step, unit, route_cell)):
                            return RoleCow(step, unit, factory, route_cell)
            else:
                break

    @classmethod
    @profileit
    def from_factory_route(cls, step, unit, partial_dist=8, num_routes=1):
        i = step - unit.board.step
        board = unit.board
        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)

        factory_routes = [r for r in factory.factory_routes
                          if r[-1].factory() and r[-1].factory().player_id == board.player.id]
        for factory_route in factory_routes[:num_routes]:
            for route_cell in factory_route[1:partial_dist+1]:
                if (not route_cell.assigned_unit(step)
                    and route_cell.rubble[i] > 0
                    and cls._dest_is_safe(step, unit, route_cell)):
                    return RoleCow(step, unit, factory, route_cell)

    @classmethod
    @profileit
    def from_custom_route(cls, step, unit, target_cell, max_count=None):
        if not target_cell:
            return

        i = step - unit.board.step
        board = unit.board
        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)

        if max_count:
            factory_cows = [u for u in factory.units(step)
                            if u.type == unit.type and u.role and u.role.NAME == 'cow']
            if 1 + len(factory_cows) > max_count:
                return

        route = unit.board.route(
            step, target_cell, None,
            dest_cell=factory.cell(),
            dest_cond=lambda s,c: (c.factory() is factory),
            avoid_cond=lambda s,c: c.factory(),
            unit_move_cost=20, unit_rubble_movement_cost=1)

        best_cell, min_dist = None, C.UNREACHABLE
        for route_cell in (route or []):
            dist = route_cell.man_dist_factory(factory)
            if dist >= min_dist:
                continue
            if (not route_cell.assigned_unit(step)
                and route_cell.rubble[i] > 0
                and cls._dest_is_safe(step, unit, route_cell)):
                best_cell, min_dist = route_cell, dist
        if best_cell:
            return RoleCow(step, unit, factory, best_cell)

    @classmethod
    @profileit
    def from_lichen_frontier(cls, step, unit, max_dist=100, max_rubble=100, max_connected=10000):
        i = step - unit.board.step
        board = unit.board
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        if len(factory.lichen_connected_cells) > max_connected:
            return

        # If there are (or will be) 10+ flat boundary cells, exit
        boundary_assigned = [c for c in factory.lichen_rubble_boundary_cells
                             if c.assigned_unit(step)]
        if len(factory.lichen_flat_boundary_cells) + len(boundary_assigned) > 9:
            return

        cur_near_factory = cur_cell.man_dist_factory(factory) <= max_dist
        best_cell, min_cost = None, C.UNREACHABLE
        for lichen_expansion_cell in factory.lichen_rubble_boundary_cells:
            if (lichen_expansion_cell.rubble[i] > max_rubble
                or lichen_expansion_cell.assigned_unit(step)
                or not cls._dest_is_safe(step, unit, lichen_expansion_cell)):
                continue

            # Target cell cannot be too far from home factory
            if lichen_expansion_cell.man_dist_factory(factory) > max_dist:
                continue

            # Unit must be near home factory or target cell
            if not cur_near_factory and lichen_expansion_cell.man_dist(cur_cell) > max_dist:
                continue

            dist_unit_to_cell = cur_cell.man_dist(lichen_expansion_cell)
            dist_cell_to_factory = lichen_expansion_cell.man_dist_factory(factory)
            dist_cell_to_opp_factory = lichen_expansion_cell.nearest_factory_dist(
                board, player_id=board.opp.id)
            cell_rubble = lichen_expansion_cell.rubble[i]
            cost = ((unit.cfg.MOVE_COST * dist_unit_to_cell)
                    + (unit.cfg.DIG_COST * math.ceil(cell_rubble / unit.cfg.DIG_RUBBLE_REMOVED))
                    + (unit.cfg.MOVE_COST * dist_cell_to_factory))
            # Prefer cowing away from opp factory
            cost -= 0.2 * unit.cfg.MOVE_COST * dist_cell_to_opp_factory # TODO A/B test with 1.0
            if cost < min_cost:
                best_cell, min_cost = lichen_expansion_cell, cost
        if best_cell:
            return RoleCow(step, unit, factory, best_cell)

    @classmethod
    @profileit
    def from_lichen_bottleneck(cls, step, unit, max_dist=100, min_rubble=1, max_rubble=100):
        assert min_rubble > 0
        i = step - unit.board.step
        board = unit.board
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        cur_near_factory = cur_cell.man_dist_factory(factory) <= max_dist
        scores = defaultdict(int)  # {cell_id: score}
        for cell in factory.lichen_bottleneck_cells:
            at_least_one_assigned = False
            for neighbor in cell.neighbors():
                if (neighbor.factory()
                    or neighbor.ice
                    or neighbor.ore
                    or neighbor.rubble[i] < min_rubble
                    or neighbor.rubble[i] > max_rubble
                    or not cls._dest_is_safe(step, unit, neighbor)):
                    continue

                # Target cell cannot be too far from home factory
                if neighbor.man_dist_factory(factory) > max_dist:
                    continue

                # Unit must be near home factory or target cell
                if not cur_near_factory and neighbor.man_dist(cur_cell) > max_dist:
                    continue

                if neighbor.assigned_unit(step):
                    at_least_one_assigned = True
                    break

                # Want low lichen dist (from bottleneck cell)
                # Want neighbor cell to be adjacent to multiple bottlneck cells
                # Somewhat want low rubble on neighbor cell
                # Avoid neighbor cell if bottlneck cell has other neighbor cell already assigned
                if neighbor.id not in scores:
                    scores[neighbor.id] += 0.02 * (100 - neighbor.rubble[i])
                scores[neighbor.id] += (100 - cell.lichen_dist)

            # Bottleneck already being addressed
            if at_least_one_assigned:
                for neighbor in cell.neighbors():
                    if neighbor.id in scores:
                        del scores[neighbor.id]

        if scores:
            best_cell_id, best_score = max(scores.items(), key=lambda x: x[1])
            best_cell = board.cell(0,0,cid=best_cell_id)
            #if i == 0 and factory.id == 0:
            #    log(f'{unit} cow at {best_cell} because of bottleneck')
            return RoleCow(step, unit, factory, best_cell)
        #else:
        #    if i == 0 and factory.id == 0 and factory.lichen_bottleneck_cells:
        #        log(f'{unit} no cow bottleneck')

    @classmethod
    @profileit
    def from_factory_radius(cls, step, unit, max_dist=100,
                            min_rubble=1, max_rubble=100, max_dist_from_unit=100):
        assert min_rubble > 0
        i = step - unit.board.step
        board = unit.board

        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        # Backup plan: For each cell around factory in radius order:
        for man_dist in range(1, max_dist+1):
            best_cell, min_dist = None, C.UNREACHABLE
            for rubble_cell, _ in factory.radius_cells(man_dist, min_radius=man_dist):
                if (rubble_cell.rubble[i] >= min_rubble
                    and rubble_cell.rubble[i] <= max_rubble
                    and (not rubble_cell.assigned_unit(step)
                         or (unit.type == 'HEAVY' and rubble_cell.assigned_unit(step).type == 'LIGHT'))
                    and not rubble_cell.ice
                    and not rubble_cell.ore
                    and cls._dest_is_safe(step, unit, rubble_cell)):
                    dist_from_unit = rubble_cell.man_dist(cur_cell)
                    if dist_from_unit < min_dist and dist_from_unit <= max_dist_from_unit:
                        best_cell, min_dist = rubble_cell, dist_from_unit
            if best_cell:
                is_repair = (max_dist <= 3 and max_rubble <= 20)
                cls._handle_displaced_unit(step, best_cell)
                return RoleCow(step, unit, factory, best_cell, repair=is_repair)

    def is_valid(self, step):
        board = self.unit.board
        i = step - board.step
        is_valid = (self.factory
                    and self.rubble_cell.rubble[i] > 0
                    and self._dest_is_safe(step, self.unit, self.rubble_cell))

        if is_valid:
            factory_water = (self.factory.water[i]
                             + self.factory.ice[i] // board.env_cfg.ICE_WATER_RATIO)
            if (factory_water < 40
                and self.unit.type == 'HEAVY'):
                ice_miners = [u for u in self.factory.units(step)
                              if (u.type == 'HEAVY'
                                  and (not u.role
                                       or (u.role
                                           and u.role.NAME == 'miner'
                                           and u.role.resource_cell.ice)))]
                if len(ice_miners) == 0:
                    if i == 0:
                        log(f'cow invalidate {self.unit} water={factory_water}')
                    is_valid = False

        return is_valid

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)
        self.rubble_cell.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            self.rubble_cell.unset_assignment(step, self.unit)

    def goal_cell(self, step):
        # Temporarily override goal cell if on factory center
        if self.unit.cell(step) is self.get_factory().cell():
            return self.rubble_cell

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

        if self.goal is self.rubble_cell:
            # Handled by RoleRecharge
            pass
        elif self.goal is self.factory:
            if self.unit.type == 'HEAVY' and self.rubble_cell.man_dist_factory(self.factory) == 1:
                power_threshold = (self.unit.cfg.ACTION_QUEUE_POWER_COST
                                   + 3 * self.unit.cfg.MOVE_COST
                                   + 2 * self.unit.cfg.DIG_COST
                                   + self.rubble_cell.rubble[i])
            else:
                power_threshold = 10 * self.unit.cfg.DIG_COST  # 15?

            if unit_power >= power_threshold:
                self.goal = self.rubble_cell
        else:
            assert False

    def do_move(self, step):
        return self._do_move(step)

    def do_dig(self, step):
        i = step - self.unit.board.step
        cur_cell = self.unit.cell(step)
        goal_cell = self.goal_cell(step)

        if self.goal is self.rubble_cell and cur_cell is goal_cell:
            if self.unit.power[i] >= self.unit.dig_cost(step):
                return self.unit.do_dig(step)

    def do_pickup(self, step):
        return self._do_power_pickup(step)

    def do_transfer(self, step):
        return self._do_transfer_resource_to_factory(step)

    def get_factory(self):
        return self.factory
