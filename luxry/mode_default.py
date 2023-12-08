import sys

from .mode import Mode
from .role_antagonizer import RoleAntagonizer
from .role_attacker import RoleAttacker
from .role_blockade import RoleBlockade
from .role_cow import RoleCow
from .role_generator import RoleGenerator
from .role_miner import RoleMiner
from .role_pillager import RolePillager
from .role_protector import RoleProtector
from .role_recharge import RoleRecharge
from .role_relocate import RoleRelocate
from .role_transporter import RoleTransporter
from .role_water_transporter import RoleWaterTransporter
from .util import C, log


class ModeDefault(Mode):
    '''Default factory behavior'''
    NAME = 'default'

    def __init__(self, step, factory):
        super().__init__(step, factory)

    def __repr__(self):
        return f'Default[{self.factory}]'

    def set_mode(self, step):
        super().set_mode(step)

    def unset_mode(self, step):
        if super().unset_mode(step):
            pass

    def serialize(self):
        return (self.NAME,
                )

    @classmethod
    def from_serial(cls, step, factory, mode_data):
        _ = step
        _ = mode_data
        return ModeDefault(step, factory)

    @classmethod
    def from_factory(cls, step, factory):
        return ModeDefaulta(step, factory)

    def is_valid(self, step):
        return True

    def get_transition_role(self, step, unit):
        # TODO: should set integer priorities for all roles. Can only transition if new pri is higher.
        return (
            None
            or RoleRecharge.from_transition_low_power_unit(step, unit)
            or RoleRecharge.from_transition_low_water_factory(step, unit)

            or RoleWaterTransporter.from_transition_ice_conflict_factory(step, unit)
            or RoleBlockade.from_transition_block_water_transporter(step, unit)
            or RoleBlockade.from_transition_block_different_water_transporter(step, unit)

            or RoleCow.from_transition_lichen_repair(step, unit)
            or RoleAttacker.from_transition_attack_water_transporter_ant(step, unit)
            or RoleAttacker.from_transition_attack_low_power_unit(step, unit)

            or RoleProtector.from_transition_from_transporter(step, unit)
            or RoleProtector.from_transition_protect_ice_miner(step, unit)
            or RoleRelocate.from_transition_assist_ice_conflict(step, unit)

            #or (C.F1 and not C.SIESTA
            #    and RoleAttacker.from_transition_attack_with_sidekick(step, unit))
            or RoleMiner.from_transition_heavy_to_ore(step, unit)
            or RoleMiner.from_transition_heavy_to_uncontested_ice(step, unit)

            or (unit.type == 'HEAVY'
                and RoleAttacker.from_transition_defend_territory(step, unit))

            or RoleTransporter.from_transition_from_protector(step, unit)
            or RolePillager.from_transition_end_of_game(step, unit)
            or RolePillager.from_transition_active_pillager(step, unit, max_dist=20)
            or RoleAntagonizer.from_transition_active_antagonizer_with_target_factory(step, unit)
            or RoleMiner.from_transition_active_ice_miner_to_closer_cell(step, unit)
        )

    def get_new_role(self, step, unit):
        if unit.type == 'HEAVY':
            new_role = (
                None
                #or (C.F1 and RoleGenerator.from_post_forge_heavy(step, unit, max_count=1))
                or (step >= 200 and RoleCow.from_factory_radius(step, unit, max_dist=1, max_rubble=20))
                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=10, max_count=1)
                or RoleCow.from_lichen_repair(step, unit, max_dist=5)
                or RoleRelocate.from_power_surplus(step, unit)

                or (C.PHILIPP
                    and RoleAttacker.from_transition_defend_territory(step, unit))

                or RoleAntagonizer.from_mine(step, unit, max_dist=20, ice=True, max_water=50)
                or (not C.HARM
                    and RoleAntagonizer.from_mine(step, unit, max_dist=20, ice=True, max_water=100))

                or RoleAntagonizer.from_mine(step, unit, max_dist=15)
                or (C.PHILIPP and step < 500
                    and RoleAntagonizer.from_mine(step, unit, max_dist=20))
                or (C.PHILIPP and step < 500
                    and RoleAntagonizer.from_mine(step, unit, max_dist=30))

                or RoleCow.from_lichen_bottleneck(step, unit, max_dist=1, min_rubble=20)
                or RoleCow.from_lichen_frontier(step, unit, max_dist=4, max_rubble=40)
                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=10, max_count=2)

                or RolePillager.from_lichen_cell_count(step, unit, max_dist=15, max_count=1)
                or RoleCow.from_lichen_frontier(step, unit, max_dist=10, max_rubble=40)
                or RoleCow.from_lichen_frontier(step, unit, max_dist=10)
                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=10, max_count=3)

                or RoleAttacker.from_transition_defend_territory(step, unit, max_count=1)

                or RoleAntagonizer.from_mine(step, unit, max_dist=25, ice=True, max_water=50)
                or RoleAntagonizer.from_mine(step, unit, max_dist=20)
                or RolePillager.from_lichen_cell_count(step, unit, max_dist=20, max_count=2)
                or RoleCow.from_lichen_frontier(step, unit, max_dist=15, max_rubble=40)
                or RoleCow.from_lichen_frontier(step, unit, max_dist=15)
                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=15, max_count=4)

                or RoleAttacker.from_transition_defend_territory(step, unit, max_count=3)

                or RoleAntagonizer.from_mine(step, unit, max_dist=40, ice=True, max_water=50)
                or RoleAntagonizer.from_mine(step, unit, max_dist=40)
                or RolePillager.from_lichen_cell_count(step, unit)
                or (step >= 750 and RoleRelocate.from_idle(step, unit))
                or RoleRecharge(step, unit, self.factory)
            )
        else:
            new_role = (
                None
                or RoleTransporter.from_new_unit(step, unit)
                or RolePillager.from_one_way(step, unit)
                or RolePillager.from_lichen_cell_count(step, unit, max_dist=20, max_count=1)
                or RoleAntagonizer.from_chain(step, unit, max_dist=20, max_count=1)
                or RoleRelocate.from_assist_ice_conflict(step, unit)
                or RoleAntagonizer.from_mine(step, unit, max_dist=20, ice=True, max_water=50)
                or RoleAntagonizer.from_mine(step, unit, max_dist=20, max_count=1)

                or RoleCow.from_lowland_route(step, unit, max_dist=2, min_size=50)
                or RoleCow.from_lowland_route(step, unit, max_dist=6, min_size=100)
                or RoleCow.from_lowland_route(step, unit, max_dist=4, min_size=15)
                or RoleCow.from_lichen_frontier(step, unit, max_rubble=19, max_connected=20)
                or RoleCow.from_lichen_bottleneck(step, unit, max_dist=10)
                or RoleCow.from_lichen_frontier(step, unit, max_rubble=39, max_connected=15)
                or RoleCow.from_lowland_route(step, unit, max_dist=6, min_size=50)
                or RoleCow.from_resource_route(
                    step, unit, ore=True, num_routes=1, max_dist=20, max_count=4)

                or (not C.HARM
                    and RoleAntagonizer.from_chain(step, unit, max_dist=20, max_count=3))
                or (C.PHILIPP
                    and RoleAntagonizer.from_chain(step, unit, max_dist=20))
                or (C.PHILIPP
                    and RoleAntagonizer.from_chain(step, unit, max_dist=30))
                or (C.PHILIPP
                    and RoleAntagonizer.from_chain(step, unit, max_dist=40))

                or RoleCow.from_lowland_route(step, unit, max_dist=8, min_size=100, max_count=3)
                or RoleCow.from_lichen_repair(step, unit, max_dist=10)
                or (step < 750 and RoleRelocate.from_power_surplus(step, unit))

                or RoleMiner.from_resource_route(step, unit, ore=True, dist_lim=10, max_count=2)
                or RoleCow.from_resource_route(
                    step, unit, ice=True, num_routes=2, max_dist=10, max_count=4)
                or RoleCow.from_lichen_frontier(step, unit, max_dist=10, max_rubble=19)

                or RoleAttacker.from_transition_defend_territory(
                    step, unit, max_count=4)

                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=10, max_count=2)
                or RoleAntagonizer.from_chain(step, unit, max_dist=20, max_count=3)
                or RoleAntagonizer.from_mine(step, unit, max_dist=20, max_count=3)
                or RolePillager.from_lichen_cell_count(step, unit, max_dist=15)
                or RoleCow.from_lichen_repair(step, unit, max_dist=15)

                or (C.PHILIPP and RoleCow.from_factory_radius(step, unit, max_dist=3))

                or RoleAttacker.from_transition_defend_territory(
                    step, unit, max_count=6)

                or RoleMiner.from_resource_route(step, unit, ore=True, dist_lim=20, max_count=3)
                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=15, max_count=3)
                or RoleAntagonizer.from_chain(step, unit, max_dist=25)
                or RoleAntagonizer.from_mine(step, unit, max_dist=25)
                or RolePillager.from_lichen_cell_count(step, unit, max_dist=20)

                or RoleAttacker.from_transition_defend_territory(
                    step, unit, max_count=8)

                or RoleCow.from_lichen_frontier(step, unit, max_dist=15, max_rubble=39)
                or RoleCow.from_lichen_frontier(step, unit, max_dist=4, max_rubble=79)
                or RoleAntagonizer.from_chain(step, unit, max_dist=40)
                or RoleAntagonizer.from_mine(step, unit, max_dist=40)
                or RolePillager.from_lichen_cell_count(step, unit)
                or RoleCow.from_lichen_frontier(step, unit)
                or RoleMiner.from_resource_route(step, unit, ore=True, dist_lim=20, max_count=4)
                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=15)
                or (step >= 750 and RoleRelocate.from_idle(step, unit))
                or RoleRecharge(step, unit, self.factory)
            )
        return new_role

    def do_build(self, step):
        return self._do_build(step)

    def do_water(self, step):
        return self._do_water(step)
