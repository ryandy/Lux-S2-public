from collections import defaultdict
import sys

from .mode_default import ModeDefault
from .role import Role
from .role_antagonizer import RoleAntagonizer
from .role_attacker import RoleAttacker
from .role_cow import RoleCow
from .role_miner import RoleMiner
from .role_pillager import RolePillager
from .role_recharge import RoleRecharge
from .role_transporter import RoleTransporter
from .role_water_transporter import RoleWaterTransporter
from .util import C, log


# TODO: Could use a similar mode later in games to target single opp factories with many
#       units at once in a sustained denial of ice attack. Could identify candidates by
#       lack of nearby ice and low-ish water, regardless of distance from this factory.
#
# TODO: Furthermore, this may be a new concept of a strategy-wide focus on one thing in particular.
#       Then all default factories could bump up priorities for relevant antagonizing/pillaging.
class ModeIceConflict(ModeDefault):
    '''Survive while antagonizing ice with a nearby opp factory
    Known seeds: 3, 279, 8951
    Only two ice patches: 43, 232444462
    Ice superiority against v5: 1, 10, 12, 13,
    '''
    NAME = 'ice_conflict'

    def __init__(self, step, factory, opp_factory, defensive=False):
        super().__init__(step, factory)
        self.opp_factory = opp_factory
        self.defensive = 1 if defensive else 0

    def __repr__(self):
        d = '(d)' if self.defensive else ''
        return f'IceConflict[{self.factory} -> {self.opp_factory}{d}]'

    def set_mode(self, step):
        super().set_mode(step)

    def unset_mode(self, step):
        if super().unset_mode(step):
            pass

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.opp_factory),
                self.serialize_obj(self.defensive),
                )

    @classmethod
    def from_serial(cls, step, factory, role_data):
        _ = step
        opp_factory = cls.deserialize_obj(factory.board, role_data[1])
        defensive = cls.deserialize_obj(factory.board, role_data[2])
        return ModeIceConflict(step, factory, opp_factory, defensive=defensive)

    @classmethod
    def from_transition_antagonized(cls, step, factory):
        board = factory.board
        i = step - board.step

        if (i > 0
            or (factory.mode and factory.mode.NAME == 'ice_conflict')):
            return

        # Really need a friendly factory to help support this mode.
        if len(board.player.factories()) == 1:
            return

        heavies = [u for u in factory.units(step) if u.type == 'HEAVY']
        factory_water = (factory.water[i]
                         + sum(u.water[i] for u in heavies)
                         + ((factory.ice[i] + sum(u.water[i] for u in heavies))
                            // board.env_cfg.ICE_WATER_RATIO))
        if (len(heavies) != 1
            or factory_water >= 130):
            return

        # We care specifically about when our single heavy miner is trying to mine ice but struggling
        heavy_unit = heavies[0]
        if (not heavy_unit.role
            or heavy_unit.role.NAME != 'miner'
            or not heavy_unit.role.resource_cell.ice
            # We don't want the antagonized flag to refer to some previous assignment,
            # so confirm that the miner is close to its resource cell first.
            or heavy_unit.cell(step).man_dist(heavy_unit.role.resource_cell) >= 2):
            return

        antagonizing_unit = heavy_unit.is_antagonized(step)
        if antagonizing_unit:
            is_contested = heavy_unit.role.resource_cell.is_contested()
            # If uncontested, only transition if antagonizer has tons of power
            if not is_contested and antagonizing_unit.power[i] // 20 < factory_water - 20:
                log(f'{heavy_unit} antagonized but can outlast {antagonizing_unit}')
                return

            # If miner can transition to a safer cell, let them try that first
            can_transition = RoleMiner.from_transition_heavy_to_uncontested_ice(step, heavy_unit)
            if can_transition:
                log(f'{heavy_unit} antagonized, wait to transition ice cell before getting defensive')
                return
            else:
                log(f'{heavy_unit} antagonized, has no uncontested ice cell to transition to')

            opp_factory_id = antagonizing_unit.stats() and antagonizing_unit.stats().last_factory_id
            opp_factory = (board.factories[opp_factory_id]
                           if opp_factory_id in board.factories
                           else None)

            if (opp_factory
                and opp_factory.cell().man_dist_factory(factory) > 15
                and antagonizing_unit.power[i] // 20 < factory_water - 20):
                log(f'{heavy_unit} antagonized, but can outlast distant {antagonizing_unit}')
                return

            # Force existing units to stop what they're doing
            # This is called before any role is validated/set, so we can just set roles to None
            for unit in factory.units(step):
                unit.role = None
            return ModeIceConflict.from_desperation(
                step, factory, attacking_factory=opp_factory)

    @classmethod
    def from_ice_superiority(cls, step, factory):
        if step != 0:
            # TODO check ice_conflict(+1) vs default count if step != 0
            return

        ice_routes = [r for r in factory.resource_routes if r[-1].ice]
        ice1 = bool(len(ice_routes) >= 1 and len(ice_routes[0]) - 1 <= 1)

        for opp_factory in factory.board.opp.factories():

            # No need to target the same factory twice
            if ice1:
                already_handled = False
                for own_factory in factory.board.player.factories():
                    if (own_factory.mode
                        and own_factory.mode.NAME == 'ice_conflict'
                        and own_factory.mode.opp_factory is opp_factory):
                        already_handled = True
                if already_handled:
                    continue

            man_dist = factory.cell().man_dist(opp_factory.cell())
            if man_dist > 10:
                continue
            # TODO: require friendly factory nearby for support?
            #       friendly factory and/or supplemental ice for light mining?
            if opp_factory.cell().ice_vulnerable_relative(step, factory):
                return ModeIceConflict(step, factory, opp_factory)

    @classmethod
    def from_desperation(cls, step, factory, attacking_factory=None):
        '''If a factory did not get an ice-adjacent spawn position, and we are not near an
        ice-vulnerable opp (assuming we call that constructor first), then we simply want to
        deny the nearest opp factory until they starve and we can adopt their ice.
        '''
        board = factory.board

        # Check for desperation unless we already know there is an opp factory attacking
        if not attacking_factory:
            # Make sure we don't re-set as ice conflict after the target factory explodes
            if step != 0:
                return
            ice_routes = [r for r in factory.resource_routes if r[-1].ice]
            if (len(ice_routes) >= 1
                and len(ice_routes[0]) - 1 <= 1):
                return

        opp_factory = (attacking_factory
                       or factory.cell().nearest_factory(board, player_id=board.opp.id))
        return ModeIceConflict(step, factory, opp_factory, defensive=bool(attacking_factory))

    def is_valid(self, step):
        board = self.factory.board
        i = step - board.step

        # Mode is invalidated after the target factory falls
        is_valid = bool(self.opp_factory)

        #if not C.AFTER_DEADLINE and not self.defensive:
        #    is_valid = False

        # Quickly abort non-critical ice conflict battles if there are too many going on
        if is_valid and not self.defensive and (i == 0 or step == 0):
            ice_routes = [r for r in self.factory.resource_routes if r[-1].ice]
            ice_cell = ice_routes[0][-1] if ice_routes else None
            opp_ice_routes = [r for r in self.opp_factory.resource_routes if r[-1].ice]
            opp_ice_cell = opp_ice_routes[0][-1] if opp_ice_routes else None
            if (ice_cell
                and ice_cell.man_dist_factory(self.factory) == 1
                and opp_ice_cell
                and opp_ice_cell.man_dist_factory(self.opp_factory) == 1):
                # This is a (luxury) ice superiority factory
                # If there are too many ice conflict factories relative to default -> invalidate
                ice_conflict_count = len([f for f in board.player.factories()
                                          if (f.mode
                                              and f.mode.NAME == 'ice_conflict'
                                              and f.mode.opp_factory
                                              and f.mode.opp_factory.water[i] > 15)])
                default_count = len([f for f in board.player.factories()
                                     if not f.mode or f.mode.NAME == 'default'])
                if ice_conflict_count > 2 * default_count:
                    is_valid = False

        # If in defensive mode, revert back to default once a second heavy arrives (can be protector)
        if is_valid and self.defensive and i == 0:
            heavies = [u for u in self.factory.units(step) if u.type == 'HEAVY']
            if len(heavies) > 1:
                is_valid = False

        # If in defensive mode, check if nearest ice cell(s) is unthreatened (and has been a bit)
        if is_valid and self.defensive and i == 0:
            ice_routes = [r for r in self.factory.resource_routes if r[-1].ice]
            ice_cell = ice_routes[0][-1] if ice_routes else None
            if ice_cell:
                threats = Role._get_threat_units(ice_cell, history_len=10, heavy=True)
                if not threats:
                    is_valid = False

        # Check if target factory has 0 remaining heavies
        if is_valid and self.defensive and i == 0:
            opp_metal = (self.opp_factory.metal[i]
                         + self.opp_factory.ore[i] // board.env_cfg.ORE_METAL_RATIO)
            opp_heavies = [u for u in self.opp_factory.units(step) if u.type == 'HEAVY']
            if len(opp_heavies) == 0 and opp_metal < 100:
                is_valid = False

        # Force most units to find new jobs if mode is being invalidated
        if not is_valid:
            # This is called before any role is validated/set, so we can just set roles to None
            for unit in self.factory.units(step):
                # Let any active water transporter finish their delivery.
                if not unit.role or unit.role.NAME != 'water_transporter':
                    unit.role = None

            # Ensure that at least one ice cell is assigned to this factory
            # If not, take the nearest one from a factory with more than 1
            factory_ice_cells = defaultdict(set)
            for player_factory in board.player.factories():
                ice_cells = [r[-1] for r in player_factory.resource_routes if r[-1].ice]
                for ice_cell in ice_cells:
                    if ice_cell.assigned_factory:
                        factory_ice_cells[ice_cell.assigned_factory].add(ice_cell)
            if (not self.factory in factory_ice_cells
                or len(factory_ice_cells[self.factory]) == 0):
                ice_cells = [r[-1] for r in self.factory.resource_routes if r[-1].ice]
                for ice_cell in ice_cells:
                    if (not ice_cell.assigned_factory
                        or len(factory_ice_cells[ice_cell.assigned_factory]) > 1):
                        if i == 0:
                            log(f'post-ice-conflict give {ice_cell} to {self.factory}'
                                f' from {ice_cell.assigned_factory}')
                        ice_cell.assigned_factory = self.factory
                        break

        return is_valid

    def get_new_role(self, step, unit):
        board = self.factory.board
        i = step - board.step
        heavy_ants = [u for u in self.factory.units(step)
                      if u.type == 'HEAVY' and u.role and u.role.NAME == 'antagonizer']
        heavy_ant_target_cell = heavy_ants[0].role.target_cell if heavy_ants else None
        light_count= len([u for u in self.factory.units(step) if u.type == 'LIGHT'])

        if unit.type == 'HEAVY':
            new_role = (
                None
                or RoleAntagonizer.from_factory(step, unit, self.opp_factory)

                # If we end up with lots of power, start doing normal stuff, otherwise conserve
                or (self.factory.power[i] > 3000
                    and len(heavy_ants) >= 1
                    and super().get_new_role(step, unit))
                or RoleRecharge(step, unit, self.factory)
            )
        else:
            new_role = (
                None
                or RoleWaterTransporter.from_ice_conflict_factory(step, unit, self.factory)

                # Wait for ant to figure out where it's going
                or (step >= 3
                    and RoleCow.from_custom_route(step, unit, heavy_ant_target_cell, max_count=1))

                # TODO: ice chains only
                or RoleAntagonizer.from_chain(
                    step, unit, max_dist=12, max_count=light_count-2)
                or RoleAntagonizer.from_mine(
                    step, unit, max_dist=12, ice=True, max_count=light_count-2)
                or RoleAntagonizer.from_mine(
                    step, unit, max_dist=10, ice=False, max_count=light_count-2)
                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=20, max_count=1)

                # If we end up with lots of power, start doing normal stuff, otherwise conserve
                or (self.factory.power[i] > 1000
                    and RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=20, max_count=2))
                or (self.factory.power[i] > 2500
                    and super().get_new_role(step, unit))
                or RoleRecharge(step, unit, self.factory)
            )
        return new_role

    def do_build(self, step):
        return self._do_build(step)

    def do_water(self, step):
        # Conserve water
        i = step - self.factory.board.step
        if self.factory.water[i] > 300 or step >= C.END_PHASE:
            return self._do_water(step)
