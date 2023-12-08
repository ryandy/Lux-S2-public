import heapq
import itertools
import math
import sys
import time

from .cell import Cell
from .factory import Factory
from .mode_from_serial import mode_from_serial
from .mode_default import ModeDefault
from .mode_forge import ModeForge
from .mode_ice_conflict import ModeIceConflict
from .player import Player
from .role_from_serial import role_from_serial
from .unit import Unit
from .util import C, Action, Direction, Resource, log, profileit


class Board:
    def __init__(self, strategy):
        self.strategy = strategy
        self.step = None
        self.player = None
        self.opp = None
        self.size = None
        self.cells = []
        self.units = {}
        self.factories = {}
        self.env_cfg = None

        self.dist_call_id = 0  # used by .dist()
        self._opp_mines = None  # cached at the beginning of each invocation

    def summary(self, step):
        i = step - self.step

        pf = len(self.player.factories())
        of = len(self.opp.factories())

        player_units = self.player.units()
        plu = sum(u.type == 'LIGHT' for u in player_units)
        phu = len(player_units) - plu

        opp_units = self.opp.units()
        olu = sum(u.type == 'LIGHT' for u in opp_units)
        ohu = len(opp_units) - olu

        pp = round(sum(x.power[i] for x in self.player.factories() + self.player.units()) / 1000, 1)
        op = round(sum(x.power[i] for x in self.opp.factories() + self.opp.units()) / 1000, 1)

        pl = sum(c.lichen[i] for c in self.cells
                 if c.lichen[i] and c.lichen_strain[i] in self.player.strains)
        ol = sum(c.lichen[i] for c in self.cells
                 if c.lichen[i] and c.lichen_strain[i] in self.opp.strains)

        return f'{pf}-{of}F, {phu}-{ohu}HU, {plu}-{olu}LU, {pp}-{op}kP, {pl}-{ol}L'

    def to_string(self, step):
        i = step - self.step
        lines = ['']
        line = []
        for j in range(self.size):
            line.append(str(j // 10) if j//10 else ' ')
        lines.append(' '.join(line))
        line = []
        for j in range(self.size):
            line.append(str(j % 10))
        lines.append(' '.join(line))
        for y in range(self.size):
            line = []
            for x in range(self.size):
                cell = self.cell(x, y)
                c = '.'
                u = cell.unit(step)
                if u and u.type == 'HEAVY' and u.player_id == 0:
                    c = 'X'
                elif u and u.type == 'HEAVY' and u.player_id == 1:
                    c = 'O'
                elif u and u.type == 'LIGHT' and u.player_id == 0:
                    c = 'x'
                elif u and u.type == 'LIGHT' and u.player_id == 1:
                    c = 'o'
                elif cell.factory():
                    c = '#'
                elif cell.ice:
                    c = '_'
                elif cell.ore:
                    c = '~'
                elif cell.rubble[i] < 20:
                    c = '.'
                elif cell.rubble[i] < 40:
                    c = ','
                elif cell.rubble[i] < 60:
                    c = ':'
                elif cell.rubble[i] < 80:
                    c = ';'
                elif cell.rubble[i] < 100:
                    c = '^'
                if cell.factory() and cell.factory_center:
                    c = str(cell.factory_id)
                line.append(c)
            line.append(str(y))
            lines.append(' '.join(line))
        line = []
        for j in range(self.size):
            line.append(str(j // 10) if j//10 else ' ')
        lines.append(' '.join(line))
        line = []
        for j in range(self.size):
            line.append(str(j % 10))
        lines.append(' '.join(line))
        return '\n'.join(lines)

    @classmethod
    def from_obs(cls, obs, player_id, step, env_cfg, strategy, skip_routes=False):
        board = cls(strategy)
        board.step = step
        board.env_cfg = env_cfg

        assert len(obs['board']['ice']) == len(obs['board']['ice'][0]), 'square board'
        board.size = len(obs['board']['ice'])

        # Players
        if obs['teams']:  # This is only lacking during step 0 of early setup
            board.player = Player(board,
                                  player_id,
                                  obs['teams'][player_id]['water'],
                                  obs['teams'][player_id]['metal'],
                                  obs["teams"][player_id]['factory_strains'])
            opp_id = "player_0" if player_id == "player_1" else "player_1"
            board.opp = Player(board,
                               opp_id,
                               obs['teams'][opp_id]['water'],
                               obs['teams'][opp_id]['metal'],
                               obs["teams"][opp_id]['factory_strains'])

        # Cells
        for y in range(board.size):
            for x in range(board.size):
                cell = Cell(board,
                            x,
                            y,
                            obs['board']['ice'][x][y],
                            obs['board']['ore'][x][y],
                            obs['board']['rubble'][x][y],
                            obs['board']['lichen'][x][y],
                            obs['board']['lichen_strains'][x][y])
                board.cells.append(cell)

        # Factories
        for _, factories_info in obs['factories'].items():
            for _, factory_info in factories_info.items():
                factory_id = factory_info['strain_id']
                x, y = factory_info['pos'][0], factory_info['pos'][1]
                factory = Factory(board,
                                  factory_id,
                                  factory_info['team_id'],
                                  x,
                                  y,
                                  factory_info['cargo']['ice'],
                                  factory_info['cargo']['ore'],
                                  factory_info['cargo']['water'],
                                  factory_info['cargo']['metal'],
                                  factory_info['power'])
                board.factories[factory_id] = factory
                board.cell(x, y).register_factory(factory)

        # Units
        for _, units_info in obs['units'].items():
            for _, unit_info in units_info.items():
                unit_id = int(unit_info['unit_id'].split('_')[1])
                x, y = unit_info['pos'][0], unit_info['pos'][1]
                unit = Unit(board,
                            unit_id,
                            unit_info['team_id'],
                            x,
                            y,
                            unit_info['unit_type'],
                            unit_info['cargo']['ice'],
                            unit_info['cargo']['ore'],
                            unit_info['cargo']['water'],
                            unit_info['cargo']['metal'],
                            unit_info['power'],
                            unit_info['action_queue'])
                board.units[unit_id] = unit
                board.cell(x, y).register_unit(step, unit)

        # Calculate factory resource/lowland routes
        if not skip_routes:
            board.set_lowland_info(strategy)
            for factory in board.factories.values():
                factory.set_routes(strategy)  # Need lowland info first
        for cell in board.cells:
            cell.set_factory_dists(strategy)
            cell.set_unit_history(strategy)

        # Update units/cells with persisted roles/goals from previous invocation
        for factory_id, mode_data in strategy.modes.items():
            if factory_id in board.factories:
                factory = board.factories[factory_id]
                # Assign decoded mode, but don't call set_mode until update_roles_and_goals
                factory.mode = mode_from_serial(step, factory, mode_data)
            else:
                strategy.check_dead_factory(factory_id)

        # Update units/cells with persisted roles/goals from previous invocation
        for unit_id, role_data in strategy.roles.items():
            if unit_id in board.units:
                unit = board.units[unit_id]
                # Assign decoded role, but don't call set_role until update_roles_and_goals
                unit.role = role_from_serial(step, unit, role_data)
            else:
                strategy.check_dead_unit(unit_id)

        for unit_id, route_data in strategy.routes.items():
            if unit_id in board.units:
                unit = board.units[unit_id]
                unit.route = [board.cells[x] for x in route_data]

        for unit_id, factory_id in strategy.unit_assigned_factories.items():
            if unit_id in board.units:
                unit = board.units[unit_id]
                unit.assigned_factory = (board.factories[factory_id]
                                         if factory_id in board.factories
                                         else None)
                if factory_id not in board.factories:
                    strategy.check_dead_factory(factory_id)

        for cell_id, factory_id in strategy.resource_assigned_factories.items():
            cell = board.cell(0,0,cell_id)
            cell.assigned_factory = (board.factories[factory_id]
                                     if factory_id in board.factories
                                     else None)

        return board

    def cell(self, x, y, cid=None):
        if cid is not None:
            x = cid % self.size
            y = cid // self.size
        if x < 0 or y < 0 or x >= self.size or y >= self.size:
            return None
        return self.cells[y * self.size + x]

    def factory(self, factory_id):
        if factory_id is None:
            return None
        return self.factories[factory_id]

    def unit(self, unit_id):
        if unit_id is None:
            return None
        return self.units[unit_id]

    def get_player(self, player_id):
        return (self.player
                if (self.player.id == 0) == (player_id == 0 or player_id == 'player_0')
                else self.opp)

    @profileit
    def flood_fill(self, init_cell, cell_cond, cell_fn):
        '''Return count of contiguous cells that meet cond, beginning at x,y
           Optionally call a function exactly once for each cell that meets cond
        '''
        for cell in self.cells:
            cell.flood_temp = False
        queue = [init_cell]
        while queue:
            cell = queue.pop()
            if cell.flood_temp == False:
                cell.flood_temp = True
                if cell_cond(cell):
                    if cell_fn:
                        cell_fn(cell)
                    for new_cell in cell.neighbors():
                        if new_cell.flood_temp == False:
                            queue.append(new_cell)

    def opp_mines(self, heavy=None, ice=None):
        # The first call is made by Board::begin_step_simulation and returns a list of tuples
        # Later calls filter that list down to desired subsets and return a list of cells
        if self._opp_mines is not None:
            ret = []
            for cell, unit in self._opp_mines:
                if ((heavy is None or heavy == (unit.type == 'HEAVY'))
                    and (ice is None or (ice and cell.ice) or (not ice and cell.ore))):
                    ret.append(cell)
            return ret

        mines = set()
        for unit in self.opp.units():
            if ((heavy is not None) and heavy != (unit.type == 'HEAVY')):
                continue
            unit_mines = unit.get_mines(ice=ice)
            for unit_mine in unit_mines:
                mines.add((unit_mine, unit))
        return list(mines)

    def set_lowland_info(self, strategy):
        # If in strategy, load and return
        if (len(strategy.cell_caches) > 2000
            and 0 in strategy.cell_caches
            and strategy.cell_caches[0].lowland_info_saved):
            for cell in self.cells:
                cell.flatland_id = strategy.cell_caches[cell.id].flatland_id
                cell.flatland_size = strategy.cell_caches[cell.id].flatland_size
                cell.lowland_id = strategy.cell_caches[cell.id].lowland_id
                cell.lowland_size = strategy.cell_caches[cell.id].lowland_size
            return

        # Compute flatland/lowland info if not already present in persisted strategy.
        flatland_cell_cond = lambda cell: (cell.rubble[0] <= 0
                                           and not cell.factory()
                                           and not cell.ore
                                           and not cell.ice)
        flatland_cells = []
        def flatland_cell_fn(cell):
            flatland_cells.append(cell)

        lowland_cell_cond = lambda cell: (cell.rubble[0] <= 19  # Lights can move with 1 power
                                          and not cell.factory())
        lowland_cells = []
        def lowland_cell_fn(cell):
            lowland_cells.append(cell)

        next_id = 1
        for cell in self.cells:
            if flatland_cell_cond(cell) and cell.flatland_id is None:
                flatland_cells.clear()
                self.flood_fill(cell, flatland_cell_cond, flatland_cell_fn)
                for flatland_cell in flatland_cells:
                    flatland_cell.flatland_id = next_id
                    flatland_cell.flatland_size = len(flatland_cells)
                next_id += 1
        for cell in self.cells:
            if lowland_cell_cond(cell) and cell.lowland_id is None:
                lowland_cells.clear()
                self.flood_fill(cell, lowland_cell_cond, lowland_cell_fn)
                for lowland_cell in lowland_cells:
                    lowland_cell.lowland_id = next_id
                    lowland_cell.lowland_size = len(lowland_cells)
                next_id += 1

    def identify_disconnected_lichen(self, step):
        i = step - self.step

        self.player.lichen_disconnected_cells = []
        self.opp.lichen_disconnected_cells = []

        for cell in self.cells:
            if cell.lichen[i] > 0 and cell.lichen_connected[i] == False:
                if cell.lichen_strain[i] in self.player.strains:
                    self.player.lichen_disconnected_cells.append(cell)
                else:
                    self.opp.lichen_disconnected_cells.append(cell)

    def begin_step_simulation(self, step, strategy):
        i = step - self.step

        # First step only: persist static data/calculations
        if i == 0 and step == 0:
            strategy.save_factory_routes(self)
            strategy.save_cell_lowland_info(self)
            strategy.save_cell_factory_dists(self)
            strategy.save_cell_unit_history(self)  # Updated over time, but consistent reference

        # Update persisted cell unit history before first step index
        if i == 0:
            for unit in self.units.values():
                unit.cell(step).unit_history[step] = unit.id
                strategy.save_unit_stats_begin(unit)
            self._opp_mines = self.opp_mines()

        # Update lichen info so that power gain info is known for role updates
        # This will need to be re-calculated later after dig actions are made to determine water price
        for factory in self.factories.values():
            factory.calculate_lichen_count(step)
            factory.calculate_lichen_dists(step)
            # TODO: hack
            factory._power_gain = factory.power_gain(step)
            factory._power_usage = factory.power_usage(step)
        # After lichen calculation(s), identify disconnected lichen cells (only idx 0?)
        self.identify_disconnected_lichen(step)

    # TODO: handle self-collisions
    def end_step_simulation(self, step, strategy):
        i = step - self.step

        # Set next step's factory power and resources
        for factory in self.player.factories():
            factory.power[i+1] += factory.power[i]
            factory.power[i+1] += factory.power_gain(step)
            assert factory.power[i+1] >= 0

            new_water = (min(self.env_cfg.FACTORY_PROCESSING_RATE_WATER, factory.ice[i])
                         // self.env_cfg.ICE_WATER_RATIO)
            factory.water[i+1] += factory.water[i]
            factory.water[i+1] += new_water
            factory.water[i+1] -= self.env_cfg.FACTORY_WATER_CONSUMPTION
            factory.ice[i+1] += factory.ice[i]
            factory.ice[i+1] -= new_water * self.env_cfg.ICE_WATER_RATIO
            assert factory.ice[i+1] >= 0

            new_metal = (min(self.env_cfg.FACTORY_PROCESSING_RATE_METAL, factory.ore[i])
                         // self.env_cfg.ORE_METAL_RATIO)
            factory.metal[i+1] += factory.metal[i]
            factory.metal[i+1] += new_metal
            factory.ore[i+1] += factory.ore[i]
            factory.ore[i+1] -= new_metal * self.env_cfg.ORE_METAL_RATIO
            assert factory.ore[i+1] >= 0
            assert factory.metal[i+1] >= 0

        # Set next step's unit power and resources
        # TODO: enforce cargo limits
        for unit in self.player.units():
            unit.ice[i+1] += unit.ice[i]
            unit.ore[i+1] += unit.ore[i]
            unit.water[i+1] += unit.water[i]
            unit.metal[i+1] += unit.metal[i]
            unit.power[i+1] += unit.power[i]
            unit.power[i+1] += unit.power_gain(step)
            unit.power[i+1] = min(unit.power[i+1], unit.cfg.BATTERY_CAPACITY)
            assert unit.ice[i+1] >= 0
            assert unit.ore[i+1] >= 0
            assert unit.water[i+1] >= 0
            assert unit.metal[i+1] >= 0

        # Set next step's cell rubble/lichen
        for cell in self.cells:
            cell.rubble[i+1] += cell.rubble[i]
            cell.rubble[i+1] = min(cell.rubble[i+1], self.env_cfg.MAX_RUBBLE)

            # Assume opp lichen will grow, decrement everything else.
            cell.lichen[i+1] += cell.lichen[i]
            if (cell.lichen_strain[i] == -1) or (cell.lichen_strain[i] in self.player.strains):
                cell.lichen[i+1] -= self.env_cfg.LICHEN_LOST_WITHOUT_WATER
            elif cell.lichen[i] > 0:
                cell.lichen[i+1] += self.env_cfg.LICHEN_GAINED_WITH_WATER

            # Bound lichen to min/max range.
            cell.lichen[i+1] = min(cell.lichen[i+1], self.env_cfg.MAX_LICHEN_PER_TILE)
            cell.lichen[i+1] = max(cell.lichen[i+1], 0)

            # Set lichen strain
            if cell.lichen[i+1] == 0:
                cell.lichen_strain[i+1] = -1
            elif cell.lichen_strain[i+1] == -1:
                # Carryover lichen strain if not explicitly set
                cell.lichen_strain[i+1] = cell.lichen_strain[i]

        # Update factory assignments for units and cells:
        for unit in self.player.units():
            unit.assigned_factory = unit.role.get_factory() if unit.role else unit.assigned_factory

            if (unit.role
                and ((unit.role.NAME == 'miner' and unit.role.resource_cell.ice)
                     or (unit.role.NAME == 'antagonizer' and unit.role.target_cell.ice))
                and unit.type == 'HEAVY'):

                # Only change assignment for cell if original factory has exploded
                # TODO: We do not currently simulate factory explosions
                resource_cell = (unit.role.resource_cell
                                 if unit.role.NAME == 'miner'
                                 else unit.role.target_cell)
                if resource_cell.assigned_factory is None:
                    resource_cell.assigned_factory = unit.role.get_factory()

        # Save roles/goals in persisted strategy after first step index
        if i == 0:
            for factory in self.player.factories():
                strategy.save_mode(factory)

            for unit in self.units.values():
                if unit.id >= 900000:  # Future unit (created this step)
                    continue

                strategy.save_unit_stats_end(unit)

                if unit.player_id == self.player.id:
                    strategy.save_role(unit)
                    strategy.save_route(unit)
                    strategy.save_unit_assigned_factory(unit)

            for cell in self.cells:
                strategy.save_resource_assigned_factory(cell)

    def get_new_actions(self, verbose=True):
        '''Add new/altered action queues to the actions dict'''
        actions = {}
        for unit in self.player.units():
            if unit.id >= 900000: # Future unit
                continue
            # If we have enough power and the old action queue is empty/different
            if (unit.init_power >= unit.cfg.ACTION_QUEUE_POWER_COST
                and (len(unit.action_queue) == 0
                     or not Action.equal(unit.action_queue[0], unit.new_action_queue[0]))):
                # Print message if the previous action queue is being overwritten
                # Ignore if first action is repeated, it was always meant to expire eventually
                if verbose and len(unit.action_queue) > 0 and unit.action_queue[0][4] == 0:
                    for rem in range(len(unit.action_queue)):
                        if unit.action_queue[rem][4] > 0:
                            # rem now represents the number of non-repeat actions remaining in queue
                            break
                    print(f'{self.step} {unit.id} '
                          f'rem={rem} '
                          f'old={unit.action_queue[0:3]} '
                          f'new={unit.new_action_queue[0:3]} '
                          , file=sys.stderr)

                # Cut off end if agent_act loop exited early.
                if None in unit.new_action_queue:
                    unit.new_action_queue = unit.new_action_queue[:unit.new_action_queue.index(None)]

                embed_sig = (self.step == 1 and not C.AFTER_DEADLINE)
                actions[unit.id_str] = Action.compress_queue(
                    unit, unit.new_action_queue, embed_sig=embed_sig)

        for factory in self.player.factories():
            if factory.new_action is not None:
                actions[factory.id_str] = factory.new_action

        return actions

    def get_assigned_cells(self, step):
        i = step - self.step
        return [x for x in self.cells if x.assigned_unit_id[i] is not None]

    def update_roles_and_goals(self, step):
        '''
        # Iterate over units that already have roles: re-set those that are still valid
        # Iterate over units that have no roles: set new roles
        # Iterate over units and update goals
        '''
        i = step - self.step

        # Re-set factory modes that are still valid.
        for factory in self.player.factories():
            if factory.mode:
                if factory.mode.is_valid(step):
                    factory.set_mode(step)
                else:
                    factory.unset_mode(step)

        # Check for factories that meet special mode-changing criteria
        for factory in self.player.factories():
            new_mode = (
                None
                or ModeIceConflict.from_transition_antagonized(step, factory)
            )
            if new_mode:
                factory.set_mode(step, mode=new_mode)

        # Set factory modes for those without one.
        # Reverse so that in case of double ice superiority, the better factory goes default
        factories = self.player.factories()
        if step == 0:
            factories = reversed(factories)
        for factory in factories:
            if factory.mode is None:
                new_mode = (
                    None
                    or ModeIceConflict.from_ice_superiority(step, factory)
                    or ModeIceConflict.from_desperation(step, factory)
                    #or ModeForge.from_factory(step, factory)
                    or ModeDefault(step, factory)
                )
                factory.set_mode(step, mode=new_mode)

        # Iterate over units that already have roles: re-set those that are still valid
        for unit in self.player.units():
            if unit.role:
                if unit.role.is_valid(step):
                    unit.set_role(step)
                else:
                    unit.unset_role(step)

        # Update low power flags for all units. Used by RoleAttacker/RoleRecharge
        for unit in self.units.values():
            if (unit.player_id == self.player.id
                or (i == 0 and unit.player_id == self.opp.id)):
                unit.update_low_power_flag(step)

        # Check for units that meet special role-changing criteria
        for unit in self.player.units():
            factory = unit.assigned_factory or unit.cell(step).nearest_factory(self)
            new_role = factory.mode.get_transition_role(step, unit)
            if new_role:
                unit.set_role(step, role=new_role)

        loop_count = 0
        prev_inf_loop_unit = None
        while True:
            units = [u for u in self.player.units() if u.role is None]
            if len(units) == 0:
                break

            # For debugging potential infinite loops:
            loop_count += 1
            if loop_count > 5:
                log(f'Inf loop? {step} {units} {prev_inf_loop_unit} '
                    f'{prev_inf_loop_unit and prev_inf_loop_unit.role}')
                if loop_count > 100:
                    assert False
            if len(units) == 1:
                prev_inf_loop_unit = units[0]

            for unit in units:
                factory = unit.assigned_factory or unit.cell(step).nearest_factory(self)
                new_role = factory.mode.get_new_role(step, unit)
                assert new_role
                unit.set_role(step, role=new_role)

        # Update goals
        for unit in self.player.units():
            unit.update_goal(step)

    def route(self, *args, **kwargs):
        _, _, dest_cell = self.dist(*args, **kwargs)
        return self._route(dest_cell)

    def _route(self, dest_cell):
        route, cell = [], dest_cell
        while cell:
            route.append(cell)
            cell = cell.dist_temp[2]
        return list(reversed(route))

    def naive_cost_around_factory(self, step, unit, factory, src_cell, dest_cell,
                                  clockwise=None, ret_route=False):
        i = step - self.step

        if clockwise is None:
            c = self.naive_cost_around_factory(
                step, unit, factory, src_cell, dest_cell, clockwise=True)
            cc = self.naive_cost_around_factory(
                step, unit, factory, src_cell, dest_cell, clockwise=False)
            #if i == 0 and unit.type == 'HEAVY':
            #    log(f'NAIVE AROUND {unit} {factory} {src_cell} {dest_cell} {c} {cc}')
            if ret_route:
                if c < cc:
                    return self.naive_cost_around_factory(
                        step, unit, factory, src_cell, dest_cell, clockwise=True, ret_route=True)
                else:
                    return self.naive_cost_around_factory(
                        step, unit, factory, src_cell, dest_cell, clockwise=False, ret_route=True)
            return min(c, cc)

        cost = 0
        route = [src_cell]
        cell = src_cell
        while cell and cell is not dest_cell:
            if cell.man_dist_factory(factory) == 2:  # corner
                if cell.x > factory.x and cell.y > factory.y:  # southeast
                    cell = cell.neighbor(-1, 0) if clockwise else cell.neighbor(0, -1)
                elif cell.x > factory.x and cell.y < factory.y:  # northeast
                    cell = cell.neighbor(0, 1) if clockwise else cell.neighbor(-1, 0)
                elif cell.x < factory.x and cell.y > factory.y:  # southwest
                    cell = cell.neighbor(0, -1) if clockwise else cell.neighbor(1, 0)
                else:  # northwest
                    cell = cell.neighbor(1, 0) if clockwise else cell.neighbor(0, 1)
            else:  # edge
                if cell.x + 2 == factory.x:  # west
                    cell = cell.neighbor(0, -1) if clockwise else cell.neighbor(0, 1)
                elif cell.x - 2 == factory.x:  # east
                    cell = cell.neighbor(0, 1) if clockwise else cell.neighbor(0, -1)
                elif cell.y + 2 == factory.y:  # north
                    cell = cell.neighbor(1, 0) if clockwise else cell.neighbor(-1, 0)
                else:  # south
                    cell = cell.neighbor(-1, 0) if clockwise else cell.neighbor(1, 0)
            if cell:
                cost += math.floor(unit.cfg.MOVE_COST + unit.cfg.RUBBLE_MOVEMENT_COST * cell.rubble[i])
                if ret_route:
                    route.append(cell)
        if ret_route:
            return route
        return cost if cell else C.UNREACHABLE

    # Gives a general sense of rubbliness between src and dest
    # Tries to naively go around opp factories
    def naive_cost(self, step, unit, src_cell, dest_cell, is_factory=False, ret_route=False):
        i = step - self.step

        cost = 0
        route = [src_cell]
        prev_cell, cell, cur_dist = None, src_cell, src_cell.man_dist(dest_cell)
        in_opp_factory, opp_factory_prev_cell = False, None
        while (cell is not dest_cell) and not (is_factory and cell.factory() is dest_cell.factory()):
            if (not in_opp_factory
                and cell.factory()
                and cell.factory().player_id != unit.player_id
                and prev_cell):
                # Entered opp factory
                in_opp_factory, opp_factory_prev_cell = cell.factory(), prev_cell
                cost -= unit.cfg.MOVE_COST
                if ret_route:
                    route.pop()

            best_cell, min_rubble = None, C.UNREACHABLE
            for neighbor in [cell.neighbor_toward(dest_cell)] + cell.neighbors():
                if neighbor.man_dist(dest_cell) < cur_dist and neighbor.rubble[i] < min_rubble:
                    best_cell, min_rubble = neighbor, neighbor.rubble[i]
            prev_cell, cell, cur_dist = cell, best_cell, cur_dist - 1
            if not in_opp_factory:
                cost += math.floor(unit.cfg.MOVE_COST + unit.cfg.RUBBLE_MOVEMENT_COST * cell.rubble[i])
                if ret_route:
                    route.append(cell)

            if (in_opp_factory
                and (not cell.factory()
                     or cell.factory().player_id == unit.player_id)):
                if ret_route:
                    route += self.naive_cost_around_factory(
                        step, unit, in_opp_factory, opp_factory_prev_cell, cell, ret_route=True)[1:]
                else:
                    cost += self.naive_cost_around_factory(
                        step, unit, in_opp_factory, opp_factory_prev_cell, cell)
                in_opp_factory = False

        return route if ret_route else cost

    #@profileit
    def dist(self, step, src, unit,
             dest_cell=None, dest_cond=None, avoid_cond=None,
             unit_move_cost=None, unit_rubble_movement_cost=None,
             cost_lim=None, dist_lim=None, timeout_ms=None):
        '''Return (int, int, Cell) representing cost, distance, and destination cell'''
        i = step - self.step
        self.dist_call_id += 1
        self._dist_loop_count = 0
        start_time = time.time()

        # Set necessary dest_cond for rushing opponent factories
        # Set helpful dest_cond for returning to player factory
        if (dest_cond is None
            and dest_cell
            and dest_cell.factory_center
            and unit):
            dest_factory = self.factories[dest_cell.factory_id]
            if dest_factory.player_id != unit.player_id:
                # Just get near opp factory
                dest_cond = lambda _,c: (c.man_dist_factory(dest_factory) <= 1)
            else:
                # Just need to be on top of own factory
                dest_cond = lambda s,c: (c.factory() is dest_factory
                                         and (not c.assigned_unit(s)
                                              or c.assigned_unit(s) is unit))
                if avoid_cond is None:
                    # Cannot go "through" the dest factory e.g. if a cell has an assigned unit
                    avoid_cond = lambda _,c: (c.factory() is dest_factory)

        # Assume HEAVY if unit is unspecified
        if unit_move_cost is None:
            unit_move_cost = unit.cfg.MOVE_COST if unit else 20  # hardcoded
        if unit_rubble_movement_cost is None:
            unit_rubble_movement_cost = unit.cfg.RUBBLE_MOVEMENT_COST if unit else 1  # hardcoded

        # Set up the initial heap.
        queue, heap_unique = [], 0
        if isinstance(src, Cell):
            # TODO: Create dist lim in some cases?
            #       Need to be careful not to return C.UNREACHABLE to callers not expecting it
            #if dest_cell and not dist_lim:
            #    dist_lim = 4 + 2 * src.man_dist(dest_cell) # TODO
            src = [src]
        for cell in src:
            # cost, dist, cell, call_id
            cell.dist_temp = (0, 0, None, self.dist_call_id)
            heap_unique += 1
            astar_cost = (0
                          if dest_cell is None
                          else unit_move_cost * cell.man_dist(dest_cell))
            heapq.heappush(queue,
                           (astar_cost, 0, 0, heap_unique, cell))  # astar, cost, dist, unique, cell

        while queue:
            _, cost, dist, _, cell = heapq.heappop(queue)
            self._dist_loop_count += 1
            if self._dist_loop_count > 1500 and i == 0 and unit:
                self._dist_loop_count = -C.UNREACHABLE
                s = f'{unit} ({unit.role and unit.role.NAME}, goal={unit.role and unit.role.goal})'
                s += f' {src} -> {dest_cell}/{dest_cond}'
                log(s)

            # If the best remaining option is over cost_lim, we are done.
            if cost_lim is not None and cost > cost_lim:
                return C.UNREACHABLE, C.UNREACHABLE, None

            # Check for terminal condition; return cost, distance, and dest cell
            if cell is dest_cell or (dest_cond and dest_cond(step, cell)):
                return cost, dist, cell

            # TODO does not work as intended
            #      sometimes it's worth spending the time to actually get the answer
            #if timeout_ms and 1000 * (time.time() - start_time) > timeout_ms:
            #    return C.UNREACHABLE, C.UNREACHABLE, None

            # We can prune this search if this wasn't dest and we're at or above dist_lim.
            if dist_lim is not None:
                # TODO: if dest_cell and not dest_cond, can make this a stronger check.
                if dist >= dist_lim:
                    continue
                if dest_cell and not dest_cond:
                    best_possible_dist = dist + cell.man_dist(dest_cell)
                    if best_possible_dist > dist_lim:
                        continue

            # After checking for terminal condition, determine if this cell can be passed through.
            # Source cells (dist = 0) can be passed through by default.
            if dist > 0 and avoid_cond and avoid_cond(step, cell):
                continue

            for new_cell in cell.neighbors():
                # Cannot move through opponent factories
                if unit and new_cell.factory() and new_cell.factory().player_id != unit.player_id:
                    continue

                # Note: rubble[step+dist] is not known at step
                new_cost = math.floor(cost
                                      + unit_move_cost
                                      + unit_rubble_movement_cost * new_cell.rubble[i])
                new_dist = dist + 1

                # If this cell has not been initiated for this call to dist(), do it now.
                if new_cell.dist_temp[3] != self.dist_call_id:
                    # cost, dist, cell, call_id
                    new_cell.dist_temp = (C.UNREACHABLE, C.UNREACHABLE, None, self.dist_call_id)

                # If this is the best known route to new_cell, update its stats and add to heap.
                if new_cost < new_cell.dist_temp[0]:
                    # cost, dist, cell, call_id
                    new_cell.dist_temp = (new_cost, new_dist, cell, self.dist_call_id)
                    heap_unique += 1
                    astar_cost = new_cost + (0
                                             if dest_cell is None
                                             else unit_move_cost * new_cell.man_dist(dest_cell))
                    heapq.heappush(queue, (astar_cost, new_cost, new_dist, heap_unique, new_cell))

        if self._dist_loop_count < 0:
            elapsed = round(1000000 * (time.time() - start_time))
            log(f'dist() did not find destination; t={elapsed}us')
        return C.UNREACHABLE, C.UNREACHABLE, None  # cost, dist, cell

    def opp_is_tigga(self, step):
        assert step == 2
        validate_count = 0
        for unit in self.opp.units():
            if (unit.type == 'HEAVY'
                and unit._raw_action_queue is not None
                and len(unit._raw_action_queue) >= 2
                and unit._raw_action_queue[-1] is not None
                and unit._raw_action_queue[-2] is not None):
                act1, _, _, am1, r1, n1 = list(unit._raw_action_queue[-1])
                act2, _, _, am2, r2, n2 = list(unit._raw_action_queue[-2])
                # All heavies
                # Last action is transfer of 100 or 3000, r=1,n=1
                # 2nd to last is dig with r=n (5 or 10 usually)
                if (act1 == Action.TRANSFER
                    and am1 in (100, 3000)
                    and r1 == 1
                    and n1 == 1
                    and act2 == Action.DIG
                    and r2 == n2):
                    validate_count += 1
            if (unit.type == 'LIGHT'
                and unit._raw_action_queue is not None
                and len(unit._raw_action_queue) >= 2
                and unit._raw_action_queue[-1] is not None
                and unit._raw_action_queue[-2] is not None):
                act1, _, _, am1, r1, n1 = list(unit._raw_action_queue[-1])
                act2, _, _, am2, r2, n2 = list(unit._raw_action_queue[-2])
                if (act1 == Action.TRANSFER
                    and r1 == 1
                    and n1 == 1
                    and act2 == Action.DIG
                    and n2 > 1):
                    validate_count += 1
                elif (act1 == Action.DIG
                      and n1 > 1
                      and act2 == Action.MOVE
                      and r2 == 1):
                    validate_count += 1
        # Occasional lights
        return validate_count >= len(self.opp.factories()) - 1

    def opp_is_siesta(self, step):
        assert step == 2
        validate_count = 0
        for unit in self.opp.units():
            if (unit.type == 'HEAVY'
                and unit._raw_action_queue is not None
                and len(unit._raw_action_queue) >= 2
                and unit._raw_action_queue[0] is not None
                and unit._raw_action_queue[-1] is not None
                and unit._raw_action_queue[-2] is not None):
                act0, _, res0, am0, _, _ = list(unit._raw_action_queue[0])
                act1, dir1, res1, am1, r1, n1 = list(unit._raw_action_queue[-1])
                act2, dir2, res2, am2, r2, n2 = list(unit._raw_action_queue[-2])
                rsum = sum(spec[4] for spec in unit._raw_action_queue)
                nsum = sum(spec[5] for spec in unit._raw_action_queue)
                # All heavies
                # Never uses r
                # Total number of steps in queue is very high (70?)
                # Raw queue is quite long (usually max length of 20)
                # 2nd action (appears 1st now) is pickup 550 power
                # TODO: new pattern is dig1/1 .. tx1/1
                if (nsum >= 19
                    #and len(unit._raw_action_queue) >= 10
                    and act0 == Action.PICKUP
                    and res0 == Resource.POWER
                    and am0 >= 549):  # just to be safe (seems always 550)
                    if nsum >= 49:
                        #and rsum == 0):
                        validate_count += 1
                    elif (r1 == 1 and n1 == 1 and r2 == 1 and n2 == 1
                          and ((act1 == Action.TRANSFER and act2 == Action.DIG)
                               or (act1 == Action.DIG and act2 == Action.TRANSFER))):
                        validate_count += 1
                    elif ((act1 == Action.DIG and act2 == Action.MOVE and n2 > 1 and r2 == 1)
                          or (act1 == Action.MOVE and act2 == Action.DIG and n1 > 1 and r1 == 1)):
                        validate_count += 1
                    elif ((act1 == Action.TRANSFER and act2 == Action.MOVE and n2 > 1 and r2 == 1)
                          or (act1 == Action.MOVE and act2 == Action.TRANSFER and n1 > 1 and r1 == 1)):
                        validate_count += 1
                    elif act1 == Action.MOVE and dir1 == Direction.CENTER and n1 > 1:
                        validate_count += 1
        return validate_count == len(self.opp.factories())

    def opp_is_harm(self, step):
        assert step == 2
        validate_count = 0
        for unit in self.opp.units():
            if (unit.type == 'LIGHT'
                and unit._raw_action_queue is not None
                and len(unit._raw_action_queue) >= 2
                and unit._raw_action_queue[0] is not None
                and unit._raw_action_queue[1] is not None):
                # All lights
                # Never uses r
                # 2nd/3rd action (appears 1st/2nd now) is pickup 98 power
                act0, _, res0, am0, _, _ = list(unit._raw_action_queue[0])
                act1, _, res1, am1, _, _ = list(unit._raw_action_queue[1])
                rsum = sum(spec[4] for spec in unit._raw_action_queue)
                if (rsum == 0
                    and ((act0 == Action.PICKUP
                          and res0 == Resource.POWER)
                          #and am0 in (98, 103))
                         or (act1 == Action.PICKUP
                             and res1 == Resource.POWER))):
                             #and am1 == (98, 103))):
                    validate_count += 1
        return validate_count == len(self.opp.factories())

    def opp_is_flg(self, step):
        assert step == 2
        validate_count = 0
        for unit in self.opp.units():
            if (unit.type == 'HEAVY'
                and unit._raw_action_queue is not None
                and 12 <= len(unit._raw_action_queue) <= 19):
                # All heavies
                # Never uses r
                rsum = sum(spec[4] for spec in unit._raw_action_queue)
                nsum = sum(spec[5] for spec in unit._raw_action_queue)
                if rsum == 0 and nsum == len(unit._raw_action_queue):
                    validate_count += 1
        return validate_count > 0 and validate_count >= len(self.opp.factories()) - 2

    def opp_is_philipp(self, step):
        factory_count = len(self.opp.factories())

        if step == 2:
            validate_count = 0
            for unit in self.opp.units():
                if (unit.type == 'HEAVY'
                    and unit._raw_action_queue is not None
                    and 1 <= len(unit._raw_action_queue) <= 10):
                    # All heavies
                    # Never uses r
                    # First action is pickup or power transfer (does not appear now)
                    # Just movement
                    rsum = sum(spec[4] for spec in unit._raw_action_queue)
                    move_count = sum((spec[0] == Action.MOVE) for spec in unit._raw_action_queue)
                    if rsum == 0 and move_count == len(unit._raw_action_queue):
                        validate_count += 1
            return validate_count == factory_count

        elif step == 12:
            validate_count = 0
            for unit in self.opp.units()[:factory_count]:
                if unit.type == 'HEAVY':
                    log(f'{unit} {unit._raw_action_queue}')
                if (unit.type == 'HEAVY'
                    and unit._raw_action_queue is not None
                    and 2 <= len(unit._raw_action_queue) <= 6):
                    # All heavies
                    # Last two actions are always dig(x/5), tx(1/1)
                    act1, dir1, res1, am1, r1, n1 = list(unit._raw_action_queue[-1])
                    act2, dir2, res2, am2, r2, n2 = list(unit._raw_action_queue[-2])
                    if ((act1 == Action.TRANSFER and n1 == 1 and r1 == 1)
                        and (act2 == Action.DIG and r2 == 5)):
                        validate_count += 1
                    elif ((act2 == Action.TRANSFER and n2 == 1 and r2 == 1)
                          and (act1 == Action.DIG and r1 == 5)):
                        validate_count += 1
            # Allow for one to be antagonized or something
            return validate_count >= factory_count - 1

        else:
            assert False
