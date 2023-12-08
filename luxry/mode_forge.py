import sys

from .mode_default import ModeDefault
from .role_antagonizer import RoleAntagonizer
from .role_attacker import RoleAttacker
from .role_cow import RoleCow
from .role_miner import RoleMiner
from .role_pillager import RolePillager
from .role_protector import RoleProtector
from .role_recharge import RoleRecharge
from .role_relocate import RoleRelocate
from .role_transporter import RoleTransporter
from .role_water_transporter import RoleWaterTransporter
from .util import C, log


class ModeForge(ModeDefault):
    '''Factory prioritizing ore'''
    NAME = 'forge'

    MAX_ORE_DIST = 3

    def __init__(self, step, factory, ore_cell):
        super().__init__(step, factory)
        self.ore_cell = ore_cell

    def __repr__(self):
        return f'Forge[{self.factory} -> {self.ore_cell}]'

    def set_mode(self, step):
        super().set_mode(step)

    def unset_mode(self, step):
        if super().unset_mode(step):
            pass

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.ore_cell),
                )

    @classmethod
    def from_serial(cls, step, factory, mode_data):
        _ = step
        ore_cell = cls.deserialize_obj(factory.board, mode_data[1])
        return ModeForge(step, factory, ore_cell)

    # Note: this constructor is called by agent_early_setup for both players
    @classmethod
    def from_factory(cls, step, factory):
        board = factory.board
        i = step - board.step
        other_player_id = (factory.player_id + 1) % 2

        # If a step0 ice conflict was invalidated, make sure it goes to default
        if step == 1:
            return

        # Allow post-ice-conflict factories to become forges if conditions are met
        if step >= 2:
            # Lichen won't survive long with a forge
            if factory.lichen_connected_cells:
                return

            # Best if there is no nearby opp factory
            nearest_opp_factory = factory.cell().nearest_factory(board, player_id=other_player_id)
            if factory.cell().man_dist_factory(nearest_opp_factory) < 15:
                return

            # Make sure we have enough water to be a forge for at least a few steps
            factory_water = factory.water[i] + factory.ice[i] // board.env_cfg.ICE_WATER_RATIO
            water_threshold = 60
            if factory_water < water_threshold:
                return

            # If we already have a bunch of lights, no need
            lights = [u for u in factory.units(step) if u.type == 'LIGHT']
            if len(lights) >= C.LIGHT_LIM - 2:
                return

        # Verify that no opp factory has ice superiority over this one
        for opp_factory in board.factories.values():
            if opp_factory.player_id != other_player_id:
                continue
            if factory.cell().ice_vulnerable_relative(step, opp_factory):
                return

        # Make sure we can still support our ice conflict factories if we become a forge
        factories = [f for f in factory.get_player().factories() if f is not factory]
        ice_conflict_count = len([f for f in factories
                                  if f.mode and f.mode.NAME == 'ice_conflict'])
        forge_count = len([f for f in factories
                           if f.mode and f.mode.NAME == 'forge'])
        default_count = len([f for f in factories
                             if not f.mode or f.mode.NAME == 'default'])
        if ice_conflict_count > 2 * default_count + 0.5 * (forge_count + 1):
            return

        # Don't use resource routes so we can skip routes during early_stage
        best_cell, best_score = None, 0
        for ore_cell, self_dist in factory.radius_cells(cls.MAX_ORE_DIST):
            if not ore_cell.ore:
                continue
            # TODO: check naive cost too?
            opp_dist = ore_cell.nearest_factory_dist(board, player_id=other_player_id)
            dist_diff = opp_dist - self_dist

            # TODO evaluate if this stricter condition is better
            #path_cell = ore_cell.neighbor_toward(factory.cell())
            #path_rubble = ore_cell.rubble[0] + path_cell.rubble[0]
            #opp_factory_dist = factory.cell().nearest_factory_dist(board, player_id=other_player_id)
            #extra_condition = (opp_factory_dist >= 15
            #                   and ((self_dist <= 2 and path_rubble <= 60)
            #                        or (self_dist <= 1 and path_rubble <= 100)))

            if dist_diff > 0: # and extra_condition:
                score = dist_diff + (0.1 - ore_cell.rubble[0]/1000)
                if score > best_score:
                    best_cell, best_score = ore_cell, score
        if best_cell:
            return ModeForge(step, factory, best_cell)

    def is_valid(self, step):
        board = self.factory.board
        i = step - board.step
        valid = True

        # Switch to default mode if water runs low.
        if valid:
            nearest_opp_factory = self.factory.cell().nearest_factory(board, player_id=board.opp.id)
            factory_water = (self.factory.water[i]
                             + self.factory.ice[i] // board.env_cfg.ICE_WATER_RATIO)
            if (factory_water < 40
                or (factory_water < 80
                    and self.factory.cell().man_dist_factory(nearest_opp_factory) <= 8)):
                #if i == 0:
                #    log(f'{self} invalid A')
                valid = False

        # Switch to default mode if we lose our heavy or it cannot mine ore.
        if valid and step >= 10:
            heavy_ore_miners = [u for u in self.factory.units(step)
                                if (u.type == 'HEAVY'
                                    and u.role
                                    and u.role.NAME == 'miner'
                                    and u.role.resource_cell.ore)]
            if not heavy_ore_miners:
                #if i == 0:
                #    log(f'{self} invalid B')
                valid = False

        # Switch to default mode if we reach the limit for light units.
        if valid:
            factory_metal = (self.factory.metal[i]
                             + self.factory.ore[i] // board.env_cfg.ORE_METAL_RATIO)
            current_lights = [u for u in self.factory.units(step) if u.type == 'LIGHT']
            relocating_lights = [u for u in current_lights if u.role and u.role.NAME == 'relocate']
            if C.F1 and self.ore_cell.man_dist_factory(self.factory) == 1:
                # Mine enough ore for a heavy generator after building up to the limit of lights
                current_heavies = [u for u in self.factory.units(step) if u.type == 'HEAVY']
                future_heavies = factory_metal // board.env_cfg.ROBOTS['HEAVY'].METAL_COST
                future_lights = (factory_metal // board.env_cfg.ROBOTS['LIGHT'].METAL_COST
                                 - 10 * future_heavies)
                valid = (len(current_lights) - len(relocating_lights) + future_lights < C.LIGHT_LIM
                         or len(current_heavies) + future_heavies < 2)
                #if not valid:
                #    if i == 0:
                #        log(f'{self} invalid C')
            else:
                future_lights = factory_metal // board.env_cfg.ROBOTS['LIGHT'].METAL_COST
                valid = len(current_lights) - len(relocating_lights) + future_lights < C.LIGHT_LIM
                #if not valid:
                #    if i == 0:
                #        log(f'{self} invalid D')

        # If invalidating, force most units to find new jobs.
        if not valid:
            # This is called before any role is validated/set, so we can just set roles to None
            for unit in self.factory.units(step):
                if not (unit.type == 'LIGHT' and unit.role and unit.role.NAME == 'relocate'):
                    unit.role = None

        return valid

    def get_new_role(self, step, unit):
        if unit.type == 'HEAVY':
            new_role = (
                None
                or RoleMiner.from_cell(step, unit, self.ore_cell)
                or RoleMiner.from_resource_route(step, unit, ore=True, dist_lim=self.MAX_ORE_DIST)
                or super().get_new_role(step, unit)
            )
        else:
            new_role = (
                None
                or RoleTransporter.from_new_unit(step, unit, max_dist=self.MAX_ORE_DIST)
                or RoleRelocate.from_forge(step, unit)
                or RoleMiner.from_resource_route(step, unit, ice=True, dist_lim=3, max_count=1)
                or super().get_new_role(step, unit)
            )
        return new_role

    def do_build(self, step):
        return self._do_build(step)

    def do_water(self, step):
        return self._do_water(step)
