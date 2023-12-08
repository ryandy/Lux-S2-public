import sys

from .util import Action, Direction, Resource, log


class Strategy:
    def __init__(self):
        # Only one role/goal per unit. Only saved here once per invocation.
        # Not step-dependent. Just the latest known role/goal.
        self.modes = {}  # {factory_id: serialized Mode}
        self.roles = {}  # {unit_id: serialized Role}
        self.routes = {}  # {unit_id: list of cell_ids}
        self.unit_assigned_factories = {}  # {unit_id: factory_id}
        self.resource_assigned_factories = {}  # {cell_id: factory_id}

        self.unit_stats = {}  # {unit_id: UnitStats}
        self.dead_units = set()  # {unit_id}
        self.dead_factories = set()  # {factory_id}

        self.board_cache = {}  # {board_id: BoardCache}
        self.cell_caches = {}  # {cell_id: CellCache}
        self.factory_caches = {}  # {factory_id: FactoryCache}

    def check_dead_unit(self, unit_id):  # Has to be id because object no longer exists
        if unit_id not in self.dead_units:
            self.dead_units.add(unit_id)
            log(f'Unit died: {unit_id}')

    def check_dead_factory(self, factory_id):  # Has to be id because object no longer exists
        if factory_id not in self.dead_factories:
            self.dead_factories.add(factory_id)
            log(f'Factory exploded: {factory_id}')

    def save_mode(self, factory):
        assert factory.mode
        self.modes[factory.id] = factory.mode.serialize()

    def save_role(self, unit):
        assert unit.role
        self.roles[unit.id] = unit.role.serialize()

    def save_route(self, unit):
        if unit.route:
            self.routes[unit.id] = [cell.id for cell in unit.route]
        else:
            if unit.id in self.routes:
                del self.routes[unit.id]

    # Called at beginning of idx0 step simulation
    def save_unit_stats_begin(self, unit):
        if unit.id not in self.unit_stats:
            self.unit_stats[unit.id] = UnitStats(unit)
        stats = self.unit_stats[unit.id]
        stats.update_begin(unit, self.factory_caches)

    # Called at end of idx0 step simulation
    def save_unit_stats_end(self, unit):
        if unit.id not in self.unit_stats:
            self.unit_stats[unit.id] = UnitStats(unit)
        stats = self.unit_stats[unit.id]
        stats.update_end(unit)

    # Called during idx0 step simulation
    def save_unit_threat(self, unit, threatening_units):
        if unit.id not in self.unit_stats:
            self.unit_stats[unit.id] = UnitStats(unit)
        stats = self.unit_stats[unit.id]
        stats.save_threat(unit, threatening_units)

    def save_unit_assigned_factory(self, unit):
        self.unit_assigned_factories[unit.id] = (unit.assigned_factory.id
                                                 if unit.assigned_factory
                                                 else None)

    def save_resource_assigned_factory(self, cell):
        self.resource_assigned_factories[cell.id] = (cell.assigned_factory.id
                                                     if cell.assigned_factory
                                                     else None)

    def save_factory_routes(self, board):
        for factory in board.factories.values():
            if factory.id not in self.factory_caches:
                self.factory_caches[factory.id] = FactoryCache()
            factory_cache = self.factory_caches[factory.id]
            factory_cache.save_routes(factory)

    def save_cell_lowland_info(self, board):
        for cell in board.cells:
            if cell.id not in self.cell_caches:
                self.cell_caches[cell.id] = CellCache()
            cell_cache = self.cell_caches[cell.id]
            cell_cache.save_lowland_info(cell)

    def save_cell_factory_dists(self, board):
        for cell in board.cells:
            if cell.id not in self.cell_caches:
                self.cell_caches[cell.id] = CellCache()
            cell_cache = self.cell_caches[cell.id]
            cell_cache.save_factory_dists(cell)

    def save_cell_unit_history(self, board):
        for cell in board.cells:
            if cell.id not in self.cell_caches:
                self.cell_caches[cell.id] = CellCache()
            cell_cache = self.cell_caches[cell.id]
            cell_cache.save_unit_history(cell)


class BoardCache:
    def __init__(self, board):
        pass


class CellCache:
    def __init__(self):
        self.lowland_info_saved = False
        self.flatland_id = None
        self.flatland_size = 0
        self.lowland_id = None
        self.lowland_size = 0

        self.factory_dists = None  # {factory_id: dist}
        self.unit_history = None  # list of unit_ids
        self.ice_vuln_cell_ids = None  # list of cell_ids

        # TODO: Distance to all beginning-of-game flat regions
        # self.flat_region_dists = None  # {flat_region_id: dist}

    def save_lowland_info(self, cell):
        self.lowland_info_saved = True
        self.flatland_id = cell.flatland_id
        self.flatland_size = cell.flatland_size
        self.lowland_id = cell.lowland_id
        self.lowland_size = cell.lowland_size

    def save_factory_dists(self, cell):
        self.factory_dists = cell.factory_dists

    def save_unit_history(self, cell):
        self.unit_history = cell.unit_history


class FactoryCache:
    def __init__(self):
        # Set at beginning and not modified (but theoretically could be updated/refreshed)
        self.resource_routes = None  # list of list of cell_ids
        self.lowland_routes = None  # list of list of cell_ids
        self.factory_routes = None  # list of list of cell_ids

        # Modified over time
        self.pillage_cell_id_steps = []  # list of (cell_id, step)

    def save_routes(self, factory):
        self.resource_routes = []
        for resource_route in factory.resource_routes:
            self.resource_routes.append([c.id for c in resource_route])
        self.lowland_routes = []
        for lowland_route in factory.lowland_routes:
            self.lowland_routes.append([c.id for c in lowland_route])
        self.factory_routes = []
        for factory_route in factory.factory_routes:
            self.factory_routes.append([c.id for c in factory_route])


class UnitStats:
    def __init__(self, unit):
        self.init_step = unit.board.step
        self.cell_ids = []
        self.mine_cell_id_steps = []
        self.pillage_cell_id_steps = []
        self.threat_unit_id_steps = []
        self.power = []
        self.next_queued_action = []
        self.last_factory_id = None

        self.prev_cell_id = None
        self.prev_ice = 0
        self.prev_ore = 0
        self.prev_water = 0
        self.prev_prev_water = 0
        self.prev_rubble = 0
        self.prev_lichen_strain = -1

        self.role_antagonizer = 0
        self.role_attacker = 0
        self.role_blockade = 0
        self.role_cow = 0
        self.role_generator = 0
        self.role_miner = 0
        self.role_pillager = 0
        self.role_protector = 0
        self.role_recharge = 0
        self.role_relocate = 0
        self.role_sidekick = 0
        self.role_transporter = 0
        self.role_water_transporter = 0

        self.no_move = 0
        self.move = 0
        self.power_transfer = 0
        self.resource_transfer = 0
        self.dig = 0
        self.pickup = 0
        self.self_destruct = 0
        self.action_queue_update = 0

    #def __repr__(self):
    #    return (f'{self.unit_type.lower()}_{self.unit_id:2}: {round(100*self.dig/self.steps):3}% '
    #            f'dig={self.dig:2} n/a={self.no_move:2} '
    #            f'pwr={self.power_transfer:2} rsc={self.resource_transfer:2} pck={self.pickup:2} '
    #            f'mov={self.move:2}')

    def save_threat(self, unit, threatening_units):
        for opp_unit in threatening_units:
            self.threat_unit_id_steps.append((opp_unit.id, unit.board.step))

    def update_begin(self, unit, factory_caches):
        # Track position/power/next_action for all units
        board_step = unit.board.step
        cur_cell = unit.cell(board_step)
        if cur_cell.factory():
            self.last_factory_id = cur_cell.factory().id
        self.cell_ids.append(cur_cell.id)
        self.power.append(unit.power[0])
        self.next_queued_action.append(list(unit.action_queue[0])
                                       if unit.action_queue and unit.action_queue[0]
                                       else None)

        # Monitor mining
        if cur_cell.id == self.prev_cell_id:
            if ((cur_cell.ice and unit.ice[0] > self.prev_ice)
                or (cur_cell.ore and unit.ore[0] > self.prev_ore)
                or ((cur_cell.ice or cur_cell.ore) and cur_cell.rubble[0] == self.prev_rubble - 20)):
                self.mine_cell_id_steps.append((cur_cell.id, board_step))
            if (self.prev_lichen_id != -1
                and cur_cell.lichen_strain[0] == -1
                and self.prev_rubble == 0
                and cur_cell.rubble[0] > 0):
                self.pillage_cell_id_steps.append((cur_cell.id, self.prev_lichen_id, board_step - 1))
                if self.prev_lichen_id in factory_caches:
                    factory_cache = factory_caches[self.prev_lichen_id]
                    factory_cache.pillage_cell_id_steps.append((cur_cell.id, board_step))

        if unit.water[0] == 0 and self.prev_water > 0 and unit.player_id == unit.board.opp.id:
            log(f'~~ OPP {unit} WATER TRANSFER @ {cur_cell} ~~')

        self.prev_cell_id = cur_cell.id
        self.prev_ice = unit.ice[0]
        self.prev_ore = unit.ore[0]
        self.prev_prev_water = self.prev_water
        self.prev_water = unit.water[0]
        self.prev_rubble = cur_cell.rubble[0]
        self.prev_lichen_id = cur_cell.lichen_strain[0]

    def update_end(self, unit):
        # Keep track of roles/actions for player units
        if unit.player_id == unit.board.opp.id:
            return

        assert unit.role
        if unit.role.NAME == 'antagonizer':
            self.role_antagonizer += 1
        elif unit.role.NAME == 'attacker':
            self.role_attacker += 1
        elif unit.role.NAME == 'blockade':
            self.role_blockade += 1
        elif unit.role.NAME == 'cow':
            self.role_cow += 1
        elif unit.role.NAME == 'generator':
            self.role_cow += 1
        elif unit.role.NAME == 'miner':
            self.role_miner += 1
        elif unit.role.NAME == 'pillager':
            self.role_pillager += 1
        elif unit.role.NAME == 'protector':
            self.role_protector += 1
        elif unit.role.NAME == 'recharge':
            self.role_recharge += 1
        elif unit.role.NAME == 'relocate':
            self.role_relocate += 1
        elif unit.role.NAME == 'sidekick':
            self.role_sidekick += 1
        elif unit.role.NAME == 'transporter':
            self.role_transporter += 1
        elif unit.role.NAME == 'water_transporter':
            self.role_water_transporter += 1
        else:
            assert False

        action = unit.new_action_queue[0]
        if action[0] == Action.MOVE and action[1] == Direction.CENTER:
            self.no_move += 1
        elif action[0] == Action.MOVE:
            self.move += 1
        elif action[0] == Action.TRANSFER and action[2] == Resource.POWER:
            self.power_transfer += 1
        elif action[0] == Action.TRANSFER:
            self.resource_transfer += 1
        elif action[0] == Action.DIG:
            self.dig += 1
        elif action[0] == Action.PICKUP:
            self.pickup += 1
        elif action[0] == Action.SELF_DESTRUCT:
            self.self_destruct += 1
        else:
            assert False

        if (len(unit.action_queue) == 0
            or (not Action.equal(unit.action_queue[0], unit.new_action_queue[0])
                and unit.action_queue[0][4] == 0)):
            self.action_queue_update += 1
