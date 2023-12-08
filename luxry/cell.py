import math
import sys

from .strategy import CellCache
from .util import C, Action, Direction, log


class Cell:
    def __init__(self, board, x, y, ice, ore, rubble, lichen, lichen_strain):
        self.board = board
        self.id = y * board.size + x
        self.x = x
        self.y = y
        self.ice = ice
        self.ore = ore
        self.rubble = [rubble] + [0] * C.FUTURE_LEN
        self.lichen = [lichen] + [0] * C.FUTURE_LEN
        self.lichen_strain = [lichen_strain] + [-1] * C.FUTURE_LEN
        self.lichen_connected = [False] + [False] * C.FUTURE_LEN
        self.lichen_dist = None
        self.lichen_bottleneck = None

        self.factory_center = False
        self.factory_id = None

        self.dist_temp = (C.UNREACHABLE, C.UNREACHABLE, None, -1)  # Used by Board::dist()
        self.flood_temp = False  # Used by Board::flood_fill()

        self.unit_id = [None] + [None] * C.FUTURE_LEN
        self.assigned_unit_id = [None] + [None] * C.FUTURE_LEN

        self.assigned_factory = None

        self._is_contested = None
        self._light_traffic = None
        self._heavy_traffic = None

        # Cached values
        self.flatland_id = None  # int
        self.flatland_size = 0  # int
        self.lowland_id = None  # int
        self.lowland_size = 0  # int
        self.factory_dists = None  # {factory_id: dist}
        self.unit_history = None  # list of unit_ids

    def __repr__(self):
        return f'({self.x},{self.y})'

    def serialize(self):
        return ('c', self.x, self.y)

    def register_factory(self, factory):
        self.factory_center = True
        for factory_cell in [self.north().west(), self.north(), self.north().east(),
                             self.west(), self, self.east(),
                             self.south().west(), self.south(), self.south().east()]:
            factory_cell.factory_id = factory.id
            factory_cell.ice = 0
            factory_cell.ore = 0

    def register_unit(self, step, unit):
        i = step - self.board.step
        # v7 vs v6:
        #   seed=3, step=195
        #   seed=6, step=65
        #if self.unit_id[i] is not None and self.unit_id[i] != unit.id:
        #   log(f'{step} Warning: {self} overwriting unit_id[{i}] from {self.unit_id[i]} to {unit.id}')
        for neighbor in self.neighbors():
            if neighbor.unit_id[i] == unit.id:
                log(f'{step} [{i}] Warning: {unit} registered at {neighbor} then {self}')
                assert False
        self.unit_id[i] = unit.id

    def set_assignment(self, step, unit):
        i = step - self.board.step
        #log(f'{step} {unit.id} {self.x},{self.y}')
        if self.assigned_unit_id[i] is not None:
            log(f'Warning: multiple units with same assignment @ {self.x},{self.y}')
            old_unit = self.board.unit(self.assigned_unit_id[i])
            log(f'Old: {old_unit.id} {old_unit.role.NAME}')
            log(f'New: {unit.id} {unit.role.NAME}')
            log(f'{unit.board.to_string(step)}')
            assert False
        self.assigned_unit_id[i] = unit.id

    def unset_assignment(self, step, unit):
        i = step - self.board.step
        #log(f'{step} {self.assigned_unit_id[i]} {self.x},{self.y} (UNSET)')
        if self.assigned_unit_id[i] is None:
            log(f'Warning: unsetting nonexistant assignment')
            assert False
        assert self.assigned_unit_id[i] == unit.id
        self.assigned_unit_id[i] = None

    def assigned_unit(self, step):
        i = step - self.board.step
        return self.board.unit(self.assigned_unit_id[i])

    def factory(self):
        '''Return reference to factory at cell, or None'''
        return self.board.factory(self.factory_id)

    def factory_corner(self):
        '''Returns True if this cell is the corner cell of a factory'''
        factory = self.factory()
        return factory and factory.x != self.x and factory.y != self.y

    def unit(self, step, player_id=None):
        i = step - self.board.step
        unit = self.board.unit(self.unit_id[i])
        if player_id is None:
            return unit
        return unit if unit and unit.player_id == player_id else None

    def neighbor(self, dx, dy):
        return self.board.cell(self.x + dx, self.y + dy)

    def neighbor_toward(self, other_cell):
        dx = other_cell.x - self.x
        dy = other_cell.y - self.y
        if abs(dx) > abs(dy):
            return self.neighbor(dx // abs(dx), 0)
        if dy != 0:
            return self.neighbor(0, dy // abs(dy))
        return self

    def path_cell_toward(self, step, unit, other_cell):
        # If adjacent, return the current cell
        if self.man_dist(other_cell) == 1:
            return self

        # Avoid factory cells and other static units.
        _, _, dest_cell = self.board.dist(
            step, self, unit,
            dest_cell=other_cell,
            avoid_cond=lambda s,c: ((c.assigned_unit(s)
                                     and c.assigned_unit(s) is not unit)
                                    or c.factory()))

        if dest_cell is None:
            return None

        route = self.board._route(dest_cell)
        return route[1]

    def north(self):
        return self.neighbor(0, -1)

    def west(self):
        return self.neighbor(-1, 0)

    def east(self):
        return self.neighbor(1, 0)

    def south(self):
        return self.neighbor(0, 1)

    def neighbors(self):
        cells = [self.north(), self.east(), self.south(), self.west()]
        return [cell for cell in cells if cell]

    def nearest_factory(self, board, player_id=None):  # this stuff needs to move to board
        if player_id is None:
            #assert False  # Too easy to get wrong
            factories = board.player.factories()
        elif player_id == 'all':
            factories = board.factories.values()
        else:
            factories = [f for f in board.factories.values() if f.player_id == player_id]

        nearest_factory, min_dist = None, C.UNREACHABLE
        for factory in factories:
            dist = self.man_dist_factory(factory)
            if dist < min_dist:
                nearest_factory, min_dist = factory, dist
        return nearest_factory

    def nearest_factory_dist(self, board, player_id=None):
        nearest_factory = self.nearest_factory(board, player_id=player_id)
        return self.man_dist_factory(nearest_factory) if nearest_factory else C.UNREACHABLE

    def nearest_unit(self, step, board, light=False, heavy=False, player_id=None):
        assert light or heavy

        if player_id is None:
            units = board.player.units()
        elif player_id == 'all':
            units = board.units.values()
        else:
            units = [u for u in board.units.values() if u.player_id == player_id]
        units = [u for u in units if ((light and u.type == 'LIGHT') or (heavy and u.type == 'HEAVY'))]

        nearest_unit, min_dist = None, C.UNREACHABLE
        for unit in units:
            pos_step = step if unit.player_id == board.player.id else board.step
            dist = self.man_dist(unit.cell(pos_step))
            if dist < min_dist:
                nearest_unit, min_dist = unit, dist
        return nearest_unit

    def is_between(self, cella, cellb):
        return (((cella.x <= self.x <= cellb.x)
                 or (cella.x >= self.x >= cellb.x))
                and ((cella.y <= self.y <= cellb.y)
                     or (cella.y >= self.y >= cellb.y)))

    def neighbor_to_direction(self, neighbor_cell):
        if neighbor_cell.y < self.y:
            return Direction.NORTH
        if neighbor_cell.x > self.x:
            return Direction.EAST
        if neighbor_cell.y > self.y:
            return Direction.SOUTH
        if neighbor_cell.x < self.x:
            return Direction.WEST
        return Direction.CENTER

    def man_dist(self, dest_cell):
        return abs(self.x - dest_cell.x) + abs(self.y - dest_cell.y)

    def man_dist_factory(self, factory):
        '''
        0 1 2 3 4
        . . . . . 0
        . x x x . 1
        . x x x . 2
        . x x x . 3
        . . . . . 4
        '''
        # nearest_factory() can return None if the opp agent is buggy
        if factory is None:
            return 100
        factory_cell = factory if isinstance(factory, Cell) else factory.cell()
        dx = max(0, abs(self.x - factory_cell.x) - 1)
        dy = max(0, abs(self.y - factory_cell.y) - 1)
        return dx + dy

    def moves_available(self, step, power):
        i = step - self.board.step
        count = 0
        for neighbor in [self] + self.neighbors():
            if (not neighbor.unit(step + 1)
                and (not neighbor.factory() or neighbor.factory().player_id == self.board.player.id)
                and power >= 1 + math.floor(neighbor.rubble[i] * 0.05)):
                count += 1
        return count

    def polar_sort_tuple(self, other_cell):
        x, y = other_cell.x - self.x, other_cell.y - self.y
        radians = round(math.atan2(y, x), 5)
        man_dist = self.man_dist(other_cell)
        return (radians, -man_dist, other_cell.x, other_cell.y)  # unique sort value

    def radius_cells(self, radius=None, min_radius=None, max_radius=None):
        if radius is not None:
            min_radius, max_radius = radius, radius
        elif min_radius is None:
            min_radius = 0
        assert max_radius is not None
        for radius in range(min_radius, max_radius+1):
            for dx in range(-radius, radius+1):
                dy = radius - abs(dx)
                cell = self.board.cell(self.x + dx, self.y + dy)
                if cell:
                    yield cell, radius
                if dy != 0:
                    cell = self.board.cell(self.x + dx, self.y - dy)
                    if cell:
                        yield cell, radius

    def radius_cells_factory(self, max_radius, min_radius=1):
        # Have to add 2 to represent the distance from factory center to factory corner
        # Then filter out cells that are within the factory and that are futher than max_radius
        assert min_radius >= 1
        for cell, _ in self.radius_cells(min_radius=min_radius+1, max_radius=max_radius+2):
            dist = cell.man_dist_factory(self)
            if min_radius <= dist <= max_radius:
                yield cell, dist

    def safe_to_move(self, step, unit):
        '''Is it safe for `unit` to move to this cell at step (in terms of friendly fire)'''
        board = self.board
        i = step - board.step

        # A friendly unit has already registered a move to this cell for next step.
        if self.unit(step + 1):  # Assume this cannot be `unit`
            return False

        # A friendly unit exists at this location and has not yet registered a move for this step.
        unit_at_dest = (self.unit(step)
                        if self.unit(step) and self.unit(step).player_id == unit.player_id
                        else None)

        # Avoiding low power friendlies is not a concern during the final night
        if (step >= 980
            and not (self.lichen[i] and self.lichen_strain[i] in board.player.strains)
            and (not unit_at_dest
                 or unit.type == unit_at_dest.type
                 or unit.type == 'HEAVY')):
            return True

        if (unit_at_dest
            and unit_at_dest is not unit
            and unit_at_dest.x[i+1] is None):
            # We need to confirm that it has sufficient power to move this step.
            move_costs = []
            for direction in range(Direction.MIN, Direction.MAX + 1):
                # Check valid moves for unit_at_dest
                if (direction == Direction.CENTER
                    or (direction == Direction.NORTH and self.y == 0)
                    or (direction == Direction.WEST and self.x == 0)
                    or (direction == Direction.EAST and self.x == self.board.size - 1)
                    or (direction == Direction.SOUTH and self.y == self.board.size - 1)):
                    continue
                # Avoid opp factories and pre-claimed cells
                move_cell = self.neighbor(*C.MOVE_DELTAS[direction])
                if (move_cell.unit(step + 1)
                    or (move_cell.factory() and move_cell.factory().player_id != unit.player_id)):
                    continue
                move_cost, _, _ = unit_at_dest.move_cost(step, direction)
                if move_cost == 'rma':
                    # TODO debug logging
                    # TODO: If we never figure this out, just continue here in this case
                    log(f'CrashB: {i}={step}-{self.board.step}')
                    log(f'{unit} {unit.cell(step)} -> {self}')
                    log(f'{unit_at_dest} {direction} {move_costs} {move_cell}')
                    log(f'{unit_at_dest.cell(step)}')
                    log(f'{[(x if x is not None else -9) for x in self.unit_id]}')
                    log(f'{[(x if x is not None else -9) for x in unit_at_dest.y]}')
                    log(f'{[(x if x is not None else -9) for x in unit_at_dest.x]}')
                    log(f'{unit_at_dest.role}')
                    log(f'{unit.role}')
                    assert False
                move_costs.append(move_cost)
            # This move is not safe if unit_at_dest has nowhere to go.
            if (len(move_costs) == 0
                or unit_at_dest.power[i] < min(move_costs)):
                return False

        # Need to check if this move dest is a potential escape route for another unit
        # Does a neighbor cell have a unit with no other move?
        for neighbor in self.neighbors():
            neighbor_unit = neighbor.unit(step)
            if (neighbor_unit  # There is a unit adjacent to this cell
                and neighbor_unit.player_id == unit.player_id  # It's a friendly unit
                and neighbor_unit is not unit  # It's a different unit
                and neighbor_unit.x[i+1] is None  # It does not have a move yet
                and neighbor.unit(step+1)  # A different unit will be taking its cell
                and neighbor.moves_available(
                    step, neighbor_unit.power[i]) <= 1):  # Only move available is `self`
                #if i == 0:
                #    log(f'{step} Escape route {unit.id} {unit.cell(step)} {self} {neighbor}')
                return False

        # Should be safe to move here (in terms of friendly fire).
        return True

    def set_factory_dists(self, strategy):
        assert self.factory_dists is None

        self.factory_dists = {}

        # Load from persisted strategy if possible
        if (self.id in strategy.cell_caches
            and strategy.cell_caches[self.id].factory_dists is not None):
            self.factory_dists = strategy.cell_caches[self.id].factory_dists
            return

        for factory_id, factory in self.board.factories.items():
            self.factory_dists[factory_id] = self.man_dist_factory(factory)

    def set_unit_history(self, strategy):
        assert self.unit_history is None

        if (self.id in strategy.cell_caches
            and strategy.cell_caches[self.id].unit_history is not None):
            self.unit_history = strategy.cell_caches[self.id].unit_history
            return

        self.unit_history = [None] * 1000

    def is_contested(self):
        if self._is_contested is not None:
            return self._is_contested

        board = self.board
        self._is_contested = False

        f0 = self.nearest_factory(board, player_id=0)
        f1 = self.nearest_factory(board, player_id=1)
        f0_dist = self.man_dist_factory(f0)
        f1_dist = self.man_dist_factory(f1)
        if min(f0_dist, f1_dist) <= 8 and abs(f0_dist - f1_dist) <= 4:
            f0_cost, _, _ = board.dist(
                board.step, self, None,
                dest_cell=f0.cell(),
                dest_cond=lambda s,c: (c.man_dist_factory(f0) == 0),
                avoid_cond=lambda s,c: c.factory(),
                unit_move_cost=20, unit_rubble_movement_cost=1)
            f1_cost, _, _ = board.dist(
                board.step, self, None,
                dest_cell=f1.cell(),
                dest_cond=lambda s,c: (c.man_dist_factory(f1) == 0),
                avoid_cond=lambda s,c: c.factory(),
                unit_move_cost=20, unit_rubble_movement_cost=1)
            if abs(f0_cost - f1_cost) <= 180:
                self._is_contested = True

        return self._is_contested

    def traffic(self):
        '''Returns value [0.0, 1.0] representing percentage of recent time with opp unit here'''
        if self._light_traffic is not None:
            return self._light_traffic, self._heavy_traffic

        LEN = 50
        board = self.board
        step = board.step

        self._light_traffic, self._heavy_traffic = 0, 0
        for s in range(step, max(-1,step-LEN), -1):
            uid = self.unit_history[s]
            if uid in board.units and board.units[uid].player_id == board.opp.id:
                if board.units[uid].type == 'HEAVY':
                    self._heavy_traffic += 1
                else:
                    self._light_traffic += 1

        self._light_traffic /= LEN
        self._heavy_traffic /= LEN
        return self._light_traffic, self._heavy_traffic

    # Is self vulnerable to the cell at other_factory?
    def ice_vulnerable_relative(self, step, other_factory):
        other_factory_cell = (other_factory
                              if isinstance(other_factory, Cell)
                              else other_factory.cell())

        # Every ice cell within 8 of self is roughly as close or closer to other_factory
        # First check dist
        ice_cells = []
        for ice_cell, self_dist in self.radius_cells_factory(8):
            if not ice_cell.ice:
                continue
            ice_cells.append(ice_cell)
            other_dist = ice_cell.man_dist_factory(other_factory_cell)
            # TODO: was 4, not sure if 6 is too far.. although it would have to be super flat
            if self_dist + 5 <= other_dist:
                return False

        # Check cost as well
        for ice_cell in ice_cells:
            self_cost, _, _ = self.board.dist(
                step, ice_cell, None,
                dest_cell=self,
                dest_cond=lambda s,c: (c.man_dist_factory(self) == 0),
                avoid_cond=lambda s,c: c.factory(),
                unit_move_cost=20, unit_rubble_movement_cost=1)
            other_cost, _, _ = self.board.dist(
                step, ice_cell, None,
                dest_cell=other_factory_cell,
                dest_cond=lambda s,c: (c.man_dist_factory(other_factory_cell) == 0),
                avoid_cond=lambda s,c: c.factory(),
                unit_move_cost=20, unit_rubble_movement_cost=1)
            if self_cost + 180 <= other_cost:
                return False

        return True

    def get_all_ice_vulnerable_relative_cells(self, strategy):
        # By definition self is ice_vulnerable_relative to itself so start there
        # Flood fill out until cells no longer pass the check
        if self.id in strategy.cell_caches:
            if strategy.cell_caches[self.id].ice_vuln_cell_ids is not None:
                return [self.board.cell(0,0,cid=cid)
                        for cid in strategy.cell_caches[self.id].ice_vuln_cell_ids]
        else:
            strategy.cell_caches[self.id] = CellCache()

        vuln_cells = []
        def cell_fn(cell):
            if (not cell.ice
                and not cell.ore
                #and not cell.factory()
                and self.man_dist(cell) > 6):
                vuln_cells.append(cell)

        self.board.flood_fill(
            self,
            lambda c: (self.man_dist(c) <= 6 or self.ice_vulnerable_relative(self.board.step, c)),
            cell_fn)

        strategy.cell_caches[self.id].ice_vuln_cell_ids = [c.id for c in vuln_cells]
        return vuln_cells
