import math
import sys

from .cell import Cell
from .entity import Entity
from .unit import Unit
from .util import C, Direction, FactoryAction, log, profileit


class Factory(Entity):
    def __init__(self, board, factory_id, player_id, x, y,
                 ice, ore, water, metal, power):
        super().__init__()
        self.board = board
        self.id = factory_id
        self.id_str = f'factory_{factory_id}'
        self.player_id = player_id
        self.x = x
        self.y = y
        self.ice = [ice] + [0] * C.FUTURE_LEN
        self.ore = [ore] + [0] * C.FUTURE_LEN
        self.water = [water] + [0] * C.FUTURE_LEN
        self.metal = [metal] + [0] * C.FUTURE_LEN
        self.power = [power] + [0] * C.FUTURE_LEN
        self.type = 'FACTORY'
        self.new_action = None
        self._units = [[]] + [[] for _ in range(C.FUTURE_LEN)]

        self.mode = None

        self.lichen_count = [None] + [None] * C.FUTURE_LEN
        self.lichen_connected_cells = None
        self.lichen_growth_cells = None
        self.lichen_flat_boundary_cells = None
        self.lichen_rubble_boundary_cells = None
        self.lichen_frontier_cells = None
        self.lichen_opp_boundary_cells = None
        self.lichen_bottleneck_cells = None

        self.resource_routes = None  # list of list of Cells
        self.lowland_routes = None  # list of list of Cells
        self.factory_routes = None  # list of list of Cells

    def __repr__(self):
        return f'Factory{self.id}'

    def serialize(self):
        return ('f', self.id)

    def set_mode(self, step, mode=None):
        i = step - self.board.step

        if mode:
            if self.mode:
                if i == 0:
                    log(f'X! {self} {self.mode}')
                self.unset_mode(step)
            if i == 0:
                log(f'   {self} {mode}')
            self.mode = mode
        self.mode.set_mode(step)

    def unset_mode(self, step):
        i = step - self.board.step

        if i == 0:
            log(f'X  {self} {self.mode}')
        self.mode.unset_mode(step)
        self.mode = None

    def calculate_lichen_count(self, step):
        i = step - self.board.step

        self.lichen_connected_cells = []  # cells with lichen
        self.lichen_growth_cells = []  # connected + this step's new lichen cells
        self.lichen_flat_boundary_cells = []  # adjacent to connected w/ no rubble
        self.lichen_rubble_boundary_cells = []  # adjacent to connected w/ rubble
        self.lichen_frontier_cells = []  # edge of connected, adjacent to flatland
        self.lichen_opp_boundary_cells = set()  # adjacent to connected/boundary w/ opp lichen

        def lichen_growth_cond(cell):
            if cell.factory_id == self.id or cell.lichen_strain[i] == self.id:
                return True
            if cell.factory():  # Other factory
                return False
            if cell.lichen[i] == 0 and not cell.ice and not cell.ore:
                # Check cell neighbors for boundary conditions
                # Not worth continuing if this cell cannot grow lichen
                cell_is_boundary = False
                for neighbor in cell.neighbors():
                    if neighbor.lichen_strain[i] not in (-1, self.id):
                        self.lichen_opp_boundary_cells.add(neighbor)
                        cell_is_boundary = True
                    if neighbor.factory_id not in (None, self.id):
                        cell_is_boundary = True
                if cell_is_boundary:
                    return False

                factory_dist = cell.man_dist_factory(self)
                max_adj_lichen = max([0] + [n.lichen[i]
                                            for n in cell.neighbors()
                                            if n.lichen_strain[i] == self.id])
                if cell.rubble[i] > 0:
                    # Rubble adjacent to factory/lichen>0
                    if factory_dist == 1 or max_adj_lichen > 0:
                        self.lichen_rubble_boundary_cells.append(cell)
                else:
                    # Flat adjacent to factory/lichen>0
                    if factory_dist == 1 or max_adj_lichen > 0:
                        self.lichen_flat_boundary_cells.append(cell)

                    # Identify neighbors with lichen (the frontier; good for pillaging/defending)
                    if max_adj_lichen > 0:
                        for neighbor in cell.neighbors():
                            if neighbor.lichen_strain[i] == self.id and neighbor.lichen[i] > 0:
                                self.lichen_frontier_cells.append(neighbor)

                    # Flatland adjacent to factory/lichen>19
                    return (factory_dist == 1
                            or max_adj_lichen >= self.board.env_cfg.MIN_LICHEN_TO_SPREAD)
            return False

        def lichen_cell_fn(cell):
            if cell.factory():
                return
            self.lichen_growth_cells.append(cell)
            if cell.lichen[i] > 0:
                self.lichen_connected_cells.append(cell)
                cell.lichen_connected[i] = True

        self.board.flood_fill(self.cell(), lichen_growth_cond, lichen_cell_fn)
        self.lichen_count[i] = len(self.lichen_connected_cells)

    @profileit
    def calculate_lichen_dists(self, step):
        i = step - self.board.step

        self.lichen_bottleneck_cells = []
        if not self.lichen_connected_cells:
            return

        self.board.dist(
            step, self.cells(), None,
            dest_cond=lambda s,c: False,
            avoid_cond=lambda s,c: (c.lichen_strain[i] != self.id),
            unit_move_cost=1, unit_rubble_movement_cost=0)

        for cell in self.cells() + self.lichen_connected_cells:
            cell.lichen_dist = cell.dist_temp[0]

        # Identify lichen bottlenecks
        for cell in self.lichen_connected_cells:
            to_near, to_same, to_far, far_cells = 0, 0, 0, []
            for neighbor in cell.neighbors():
                if neighbor.lichen_dist is None:
                    continue
                if cell.lichen_dist < neighbor.lichen_dist:
                    to_far += 1
                    far_cells.append(neighbor)
                elif cell.lichen_dist == neighbor.lichen_dist:
                    to_same += 1
                else:
                    to_near += 1

            # TODO: the only next for each prev, the only prev for each next?

            # Potentially a bottleneck
            # Check to see if there is any other path to far_cell(s)
            if to_near == 1 and to_same == 0 and to_far >= 1:
                for far_cell in far_cells:
                    other_path_exists = False
                    further_lichen = False
                    for neighbor in far_cell.neighbors():
                        if neighbor is cell or neighbor.lichen_dist is None:
                            continue
                        if neighbor.lichen_dist <= far_cell.lichen_dist:
                            other_path_exists = True
                        else:
                            further_lichen = True
                    if further_lichen and not other_path_exists:
                        cell.lichen_bottleneck = True
                        self.lichen_bottleneck_cells.append(cell)
                        break
        #if i == 0 and self.id == 0:
        #    log(f'bottlenecks: {self.lichen_bottleneck_cells}')

    def get_player(self):
        return self.board.get_player(self.player_id)

    def cell(self):
        return self.board.cell(self.x, self.y)

    def set_unit(self, step, unit):
        i = step - self.board.step
        self._units[i].append(unit.id)

    def unset_unit(self, step, unit):
        i = step - self.board.step
        if unit.id in self._units[i]:
            self._units[i].remove(unit.id)

    def units(self, step):
        board = self.board

        # For opp factories use stats.last_factory_id
        if self.player_id == board.opp.id:
            ret = []
            for unit in self.get_player().units():
                factory_exploded = False
                stats = unit.stats()
                if stats:
                    factory_id = stats.last_factory_id
                    if factory_id == self.id:
                        ret.append(unit)
                    elif factory_id not in board.factories:
                        factory_exploded = True
                if not stats or factory_exploded:
                    if unit.cell(board.step).nearest_factory(board, player_id=unit.player_id) is self:
                        ret.append(unit)
            return ret

        # For player factories use unit.assigned_factory
        i = step - board.step
        return [u for u in self.get_player().units()
                if (u.assigned_factory is self
                    or (u.assigned_factory is None
                        and u.x[i] is not None  # Skip units being created this turn
                        and u.cell(step).nearest_factory(board, player_id=u.player_id) is self))]

    def cache(self):
        return (self.board.strategy.factory_caches[self.id]
                if self.id in self.board.strategy.factory_caches else None)

    def power_gain(self, step):
        i = step - self.board.step
        return (self.board.env_cfg.FACTORY_CHARGE
                + self.lichen_count[i] * self.board.env_cfg.POWER_PER_CONNECTED_LICHEN_TILE)

    def power_reserved(self, step):
        # Power being reserved for building a heavy
        i = step - self.board.step

        if (self.metal[i] + self.ore[i] // self.board.env_cfg.ORE_METAL_RATIO
            >= self.board.env_cfg.ROBOTS["HEAVY"].METAL_COST):
            return self.board.env_cfg.ROBOTS["HEAVY"].INIT_POWER
        return 0

    def power_usage(self, step, skip_unit=None):
        i = step - self.board.step

        # TODO: consider distance to goals (role-specific calculation) to determine efficiency
        power_usage = 0
        for unit in self.units(step):
            if unit is skip_unit:
                continue
            dig = unit.cfg.DIG_COST
            move = unit.cfg.MOVE_COST
            aq = unit.cfg.ACTION_QUEUE_POWER_COST
            gain = 6 if unit.type == 'HEAVY' else 0.6

            if (unit.is_antagonized(self.board.step)
                and not (unit.role and unit.role.NAME == 'antagonizer')):
                power_usage += aq + 1.5 * move - gain
            elif unit.role is None:
                #power_usage += 0.5 * dig - gain
                power_usage += 1.5 * move - gain
            elif unit.role.NAME == 'miner':
                power_usage += 0.9 * dig + 0.1 * move - gain
            elif unit.role.NAME == 'cow':
                # Lower efficiency due to increased transit
                power_usage += 0.7 * dig + move - gain
            elif unit.role.NAME == 'generator':
                power_usage += -gain
            elif unit.role.NAME == 'pillager' and unit.role.one_way:
                power_usage += 0
            elif unit.role.NAME == 'pillager':
                # Lower efficiency due to increased transit
                power_usage += 0.3 * dig + move - gain
            elif unit.role.NAME == 'transporter':
                # They hardly use any power, just passing it to a miner
                power_usage += -gain
            elif unit.role.NAME == 'water_transporter':
                # Rough terrain, butpower cost is split by two factories
                power_usage += move - gain
            elif unit.role.NAME == 'antagonizer':
                power_usage += 1.5 * move - gain
            elif unit.role.NAME == 'attacker':
                power_usage += aq + 1.5 * move - gain
            elif unit.role.NAME == 'sidekick':
                power_usage += aq + 1.5 * move - gain
            elif unit.role.NAME == 'blockade':
                power_usage += aq + move - gain
            elif unit.role.NAME == 'recharge' and unit.cell(step).factory():
                power_usage += -gain
            elif unit.role.NAME == 'recharge':
                power_usage += 1.5 * move - gain
            elif unit.role.NAME == 'relocate':
                # Keep them on the books for a bit to prevent post-build whiplash
                power_usage += 1.5 * move - gain
            elif unit.role.NAME == 'protector':
                power_usage += aq + 0.25 * move - gain
            else:
                assert False

        # Assume large amounts of ore/metal will be turned into heavies in near-term.
        metal = self.metal[i] + self.ore[i] // self.board.env_cfg.ORE_METAL_RATIO
        if metal >= self.board.env_cfg.ROBOTS["HEAVY"].METAL_COST:
            power_usage += 1.5 * 20 - 6

        return power_usage

    def build_light_metal_cost(self):
        unit_cfg = self.board.env_cfg.ROBOTS["LIGHT"]
        return unit_cfg.METAL_COST

    def build_light_power_cost(self, step):
        unit_cfg = self.board.env_cfg.ROBOTS["LIGHT"]
        return unit_cfg.POWER_COST

    def _create_new_unit(self, step, heavy=True):
        '''Returns new unit built by this factory at step'''
        i = step - self.board.step
        unit_id = 900000 + 1000 * self.id + step
        unit_type = 'HEAVY' if heavy else 'LIGHT'
        unit = Unit(self.board,
                    unit_id,
                    self.player_id,
                    None,
                    None,
                    unit_type,
                    0, 0, 0, 0,
                    0,
                    [None]*(i+1) # This makes _need_action_queue_cost() work correctly
                    )

        unit.x[i+1] = self.x
        unit.y[i+1] = self.y
        unit.power[i+1] = self.board.env_cfg.ROBOTS[unit_type].INIT_POWER

        self.board.units[unit_id] = unit
        self.cell().register_unit(step+1, unit)

    def _can_build_without_collision(self, step):
        i = step - self.board.step

        # If there is already going be a unit here next turn, pass.
        if self.cell().unit(step+1):
            return False

        # If there is a unit here, determine if it has enough power to move.
        # If it has already decided on a move, then we know we're ok.
        # Note: movement is 0 rubble in all directions
        # Assume it will be able to move without causing a collision
        unit = self.cell().unit(step)
        if (unit
            and unit.x[i+1] is None
            and unit.power[i] < unit.cfg.ACTION_QUEUE_POWER_COST + unit.cfg.MOVE_COST):
            return False
        return True

    def can_build_light(self, step):
        i = step - self.board.step
        return (self._can_build_without_collision(step)
                and self.power[i] >= self.build_light_power_cost(step)
                and self.metal[i] >= self.build_light_metal_cost())

    def do_build_light(self, step):
        i = step - self.board.step
        self._create_new_unit(step, heavy=False)
        self.power[i] -= self.build_light_power_cost(step)
        self.metal[i] -= self.build_light_metal_cost()
        return FactoryAction.LIGHT

    def build_heavy_metal_cost(self):
        unit_cfg = self.board.env_cfg.ROBOTS["HEAVY"]
        return unit_cfg.METAL_COST

    def build_heavy_power_cost(self, step):
        unit_cfg = self.board.env_cfg.ROBOTS["HEAVY"]
        return unit_cfg.POWER_COST

    def can_build_heavy(self, step):
        i = step - self.board.step
        return (self._can_build_without_collision(step)
                and self.power[i] >= self.build_heavy_power_cost(step)
                and self.metal[i] >= self.build_heavy_metal_cost())

    def do_build_heavy(self, step):
        i = step - self.board.step
        self._create_new_unit(step, heavy=True)
        self.power[i] -= self.build_heavy_power_cost(step)
        self.metal[i] -= self.build_heavy_metal_cost()
        return FactoryAction.HEAVY

    def water_cost(self, step):
        i = step - self.board.step
        return math.ceil(len(self.lichen_growth_cells)
                         / self.board.env_cfg.LICHEN_WATERING_COST_FACTOR)

    def can_water(self, step):
        i = step - self.board.step
        return self.water[i] >= self.water_cost(step)

    def do_water(self, step):
        i = step - self.board.step

        for cell in self.lichen_growth_cells:
            cell.lichen[i+1] += (self.board.env_cfg.LICHEN_GAINED_WITH_WATER
                                 + self.board.env_cfg.LICHEN_LOST_WITHOUT_WATER)
            cell.lichen_strain[i+1] = self.id

        self.water[i+1] -= self.water_cost(step)

        return FactoryAction.WATER

    def has_unassigned_corner(self, step):
        deltas = [(-1, -1), (1, -1), (-1, 1), (1, 1)]
        for delta in deltas:
            if not self.cell().neighbor(*delta).assigned_unit(step):
                return True
        return False

    def assigned_units(self, step):
        assigned_units = []
        for cell in self.cells():
            assigned_unit = cell.assigned_unit(step)
            if assigned_unit:
                assigned_units.append(assigned_unit)
        return assigned_units

    def unassigned_cells(self, step):
        '''Never returns factory center cell'''
        unassigned_cells = []
        for cell in self.cells():
            if not cell.assigned_unit(step):
                unassigned_cells.append(cell)
        return unassigned_cells

    def cells(self):
        '''Never returns factory center cell'''
        cells = []
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                if not (dx == dy == 0):
                    cells.append(self.cell().neighbor(dx, dy))
        return cells

    def neighbors(self):
        move_deltas = [[2,-1], [2,0], [2,1], [-2,-1], [-2,0], [-2,1],
                       [-1,2], [0,2], [1,2], [-1,-2], [0,-2], [1,-2]]
        cells = [self.cell().neighbor(*move_delta) for move_delta in move_deltas]
        return [cell for cell in cells if cell]

    def neighbor_toward(self, other_cell):
        nearest_cell, min_dist = None, C.UNREACHABLE
        for cell in self.cells():
            if cell.man_dist(other_cell) < min_dist:
                nearest_cell, min_dist = cell, cell.man_dist(other_cell)
        return nearest_cell.neighbor_toward(other_cell)

    def radius_cells(self, max_radius, min_radius=1):
        for cell, dist in self.cell().radius_cells_factory(max_radius, min_radius=min_radius):
            yield cell, dist

    def nearest_unit(self, step):
        return self.cell().nearest_unit(
            step, self.board, light=True, heavy=True, player_id=self.player_id)

    # TODO: consider loss of efficiency due to antagonization/oscillation
    def get_water_income(self, step, skip_unit=None):
        water_income = 0

        ice_miners = [u for u in self.units(step)
                      if u.role and u.role.NAME == 'miner' and u.role.resource_cell.ice]
        for ice_miner in ice_miners:
            if (ice_miner is skip_unit
                or ice_miner.role._get_threat_units(
                    ice_miner.cell(step), history_len=1, max_radius=1, heavy=True)
                or ice_miner.is_antagonized(self.board.step)):
                continue
            move_dist = ice_miner.role.resource_cell.man_dist_factory(self)
            rubble_estimate = 1.1
            move_cost = (2 * rubble_estimate * move_dist
                         * (ice_miner.cfg.MOVE_COST - 0.6 * ice_miner.cfg.CHARGE))
            digs_by_power = ((0.9 * ice_miner.cfg.BATTERY_CAPACITY - move_cost)
                             / (ice_miner.cfg.DIG_COST - 0.6 * ice_miner.cfg.CHARGE))
            digs_by_cargo = 0.75 * ice_miner.cfg.CARGO_SPACE / ice_miner.cfg.DIG_RESOURCE_GAIN
            digs = min(digs_by_power, digs_by_cargo)
            ice_cargo = digs * ice_miner.cfg.DIG_RESOURCE_GAIN
            period = 2 * move_dist + digs + 2
            unit_water_income = (ice_cargo / self.board.env_cfg.ICE_WATER_RATIO) / period
            water_income += unit_water_income

        return water_income

    def set_routes(self, strategy, flat_size_threshold=6):
        assert self.resource_routes is None
        assert self.lowland_routes is None
        assert self.factory_routes is None

        self.resource_routes = []
        self.lowland_routes = []
        self.factory_routes = []

        # Load from persisted strategy if possible
        if self.id in strategy.factory_caches:
            for resource_route in strategy.factory_caches[self.id].resource_routes:
                self.resource_routes.append(
                    [self.board.cell(0,0,cid=cell_id) for cell_id in resource_route])
            for lowland_route in strategy.factory_caches[self.id].lowland_routes:
                self.lowland_routes.append(
                    [self.board.cell(0,0,cid=cell_id) for cell_id in lowland_route])
            for factory_route in strategy.factory_caches[self.id].factory_routes:
                self.factory_routes.append(
                    [self.board.cell(0,0,cid=cell_id) for cell_id in factory_route])
            return

        # Find 15 closest ice and 15 closest ore
        resource_cells = []
        ice_count, ore_count = 0, 0
        for cell, man_dist in self.radius_cells(2 * self.board.size):
            if (ice_count < 15 and cell.ice) or (ore_count < 15 and cell.ore):
                ice_count, ore_count = ice_count + int(cell.ice), ore_count + int(cell.ore)
                resource_cells.append((cell, man_dist))
                if len(resource_cells) == 30:
                    break

        # Find 10 closest low-/flat-land regions
        lowland_cells = []
        lowlands_checked = set()
        for cell, man_dist in self.radius_cells(2 * self.board.size):
            if cell.lowland_size >= flat_size_threshold:
                if cell.lowland_id not in lowlands_checked:
                    lowlands_checked.add(cell.lowland_id)
                    lowland_cells.append((cell, man_dist))
                if cell.flatland_id not in lowlands_checked:
                    lowlands_checked.add(cell.flatland_id)
                    if cell.flatland_size >= flat_size_threshold:
                        lowland_cells.append((cell, man_dist))
                if len(lowland_cells) == 10:
                    break

        avoid_ice_factory_cond = lambda _,c: (
            c.factory()
            or (self.player_id == self.board.player.id
                and (c.ice
                     or any(n for n in c.neighbors()
                            if n.factory() and n.factory().player_id != self.player_id))))

        avoid_ice_ore_factory_cond = lambda _,c: (
            c.factory()
            or (self.player_id == self.board.player.id
                and (c.ice
                     or c.ore
                     or any(n for n in c.neighbors()
                            if n.factory() and n.factory().player_id != self.player_id))))

        for resource_cell, man_dist in resource_cells:
            # Consider rubble a bit more for ore, which may be further / less traveled
            costs = 50,1 if resource_cell.ore else 100,1
            route = self.board.route(
                self.board.step, self.cells(), None,
                dest_cell=resource_cell,
                avoid_cond=avoid_ice_factory_cond,
                unit_move_cost=costs[0], unit_rubble_movement_cost=costs[1])
            if route:
                self.resource_routes.append(route)
        self.resource_routes.sort(key=lambda r: len(r))

        lowlands_checked = set([None])
        destinations_checked = set()
        for lowland_cell, man_dist in lowland_cells:
            for region_id in (lowland_cell.lowland_id, lowland_cell.flatland_id):
                if region_id in lowlands_checked:
                    continue
                lowlands_checked.add(region_id)

                # TODO: src=self.cells()+self.lichen_connected_cells
                #       This needs to be called after lichen update
                route = self.board.route(
                    self.board.step, self.cells(), None,
                    dest_cond=lambda _,c: (c.lowland_id == region_id or c.flatland_id == region_id),
                    avoid_cond=avoid_ice_ore_factory_cond,
                    unit_move_cost=100, unit_rubble_movement_cost=1)
                if route and route[-1] not in destinations_checked:
                    destinations_checked.add(route[-1])
                    self.lowland_routes.append(route)
        self.lowland_routes.sort(key=lambda r: len(r))

        # Broken and unused
        #for factory in self.board.factories.values():
        #    if factory is self:
        #        continue
        #    # Don't go through/by non-target opp factories. Avoid ice.
        #    avoid_cond = lambda _,c: (
        #        c.ice
        #        or (factory.player_id == self.board.player.id
        #            and c.man_dist_factory(
        #                c.nearest_factory(self.board, player_id=self.board.opp.id)) <= 2))
        #    route = self.board.route(
        #        self.board.step, self.cells(), None,
        #        dest_cell=factory.cell(),
        #        dest_cond=lambda _,c: (c.factory_id == factory.id),
        #        avoid_cond=avoid_cond,
        #        unit_move_cost=20, unit_rubble_movement_cost=1)
        #    if route:
        #        self.factory_routes.append(route)
        #self.factory_routes.sort(key=lambda r: len(r))
