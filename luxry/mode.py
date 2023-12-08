import sys

from .role_miner import RoleMiner
from .util import C, log, prandom, serialize_obj, deserialize_obj


class Mode:
    def __init__(self, step, factory):
        self.factory = factory

        # Most recent step where set_mode was called.
        self.set_mode_step = None

    def set_mode(self, step):
        assert self.set_mode_step is None or self.set_mode_step < step
        self.set_mode_step = step

    def unset_mode(self, step):
        return self.set_mode_step == step

    def serialize(self):
        raise NotImplementedError()

    def is_valid(self, step):
        raise NotImplementedError()

    def get_transition_role(self, step, unit):
        raise NotImplementedError()

    def get_new_role(self, step, unit):
        raise NotImplementedError()

    def do_build(self, step):
        raise NotImplementedError()

    def do_water(self, step):
        raise NotImplementedError()

    @staticmethod
    def serialize_obj(obj):
        return serialize_obj(obj)

    @staticmethod
    def deserialize_obj(board, data):
        return deserialize_obj(board, data)

    # TODO: Probably want this to be situational, or based on factory mode, or a ratio, etc
    def _do_build(self, step):
        i = step - self.factory.board.step

        # TODO build light if ANY factory is below LIGHT_LIM?

        # TODO: increase if opp's light:heavy ratio is very high?
        light_lim = C.LIGHT_LIM + (step // 100)

        light_count = len([u for u in self.factory.units(step)
                           if u.type == 'LIGHT' and (not u.role or u.role.NAME != 'relocate')])
        heavy_count = len([u for u in self.factory.units(step)
                           if (u.type == 'HEAVY'
                               and (not u.role
                                    or (u.role.NAME != 'relocate' and u.role.NAME != 'generator')))])

        # TODO evaluating different conditional(s)
        # TODO: need to make this agree with ore_digs in RoleMiner::transition and RoleMiner::is_valid
        enough_heavies = ((heavy_count >= 2 and light_count < 0.5 * light_lim)
                          or (heavy_count >= 3 and light_count < 0.75 * light_lim))
        #enough_heavies = (heavy_count >= 2 and light_count < light_lim - 2)

        # Build heavy
        if (not enough_heavies
            and (self.factory.metal[i]
                 + self.factory.ore[i] // self.factory.board.env_cfg.ORE_METAL_RATIO
                 >= self.factory.board.env_cfg.ROBOTS["HEAVY"].METAL_COST)):
            #and RoleMiner._power_ok(step, None, self.factory, steps_threshold=50)):
            if self.factory.can_build_heavy(step):
                return self.factory.do_build_heavy(step)
        else:
            # Build light
            if self.factory.can_build_light(step):
                if (light_count < light_lim
                    or (light_count < light_lim + 4 and step >= 350 and step % 2 == 0)
                    or (step >= 750 and step % 2 == 0)):
                    return self.factory.do_build_light(step)

    def _do_water(self, step):
        board = self.factory.board
        i = step - board.step

        # Has to be re-called after dig actions are determined to get accurate water cost.
        self.factory.calculate_lichen_count(step)

        if step == 999:
            return self.factory.do_water(step)

        water_cost = self.factory.water_cost(step)
        if water_cost == 0:
            # Would do nothing, exit early
            return

        # No need to water if we're not growing anywhere new
        if (step < C.END_PHASE
            and not self.factory.lichen_flat_boundary_cells
            and min(c.lichen[i] for c in self.factory.lichen_growth_cells) >= 10):
            if i == 0:
                log(f'Skip watering {self.factory}')
            return

        # TODO: skip watering sometimes if factory power is very high?
        #       don't want to limit future overall lichen though

        ice_miners = [u for u in self.factory.units(step)
                      if u.role and u.role.NAME == 'miner' and u.role.resource_cell.ice]
        light_ice_count = len([u for u in ice_miners if u.type == 'LIGHT'])
        heavy_ice_count = len(ice_miners) - light_ice_count

        water = self.factory.water[i]
        water_with_ice = water + self.factory.ice[i] // board.env_cfg.ICE_WATER_RATIO
        never_water_threshold = 50
        always_water_threshold = 200 # 150?
        always_always_water_threshold = 300

        # Go for broke at the end.
        broke_water_threshold = (  1                 * (1 + water_cost)
                                 + (1000 - step - 2) * (1 + water_cost + 1)
                                 + 1                 * 0)
        endgame_water = water
        if (step >= C.ICE_MINE_RUSH
            and water >= (1 + water_cost)
            and water_with_ice >= (1 + water_cost) + 3 * (1 + water_cost + 1)):
            endgame_water = water_with_ice
            for unit in self.factory.units(step):
                if (unit.role
                    and unit.role.NAME == 'miner'
                    and unit.role.resource_cell.ice
                    and unit.cell(step).man_dist_factory(self.factory) <= 1):
                    endgame_water += unit.ice[i] // board.env_cfg.ICE_WATER_RATIO
        if endgame_water >= broke_water_threshold:
            if self.factory.can_water(step):
                return self.factory.do_water(step)

        # If paying for water would put us below our safety baseline, don't water.
        if water - (1 + water_cost) < never_water_threshold:
            return

        # Always water above some threshold, this ensures we don't accidently over-store
        if water - (1 + water_cost) >= always_water_threshold:
            if self.factory.can_water(step):
                # If above always_water, but water income is zero/low, only water every other turn
                # No need to grow if we have no income, but should try to hold steady
                if (heavy_ice_count > 0
                    or step % 2 == 0
                    or water - (1 + water_cost) >= always_always_water_threshold):
                    return self.factory.do_water(step)

        water_income = self.factory.get_water_income(step)
        percent_do_water = (water_income - 1) / water_cost
        if prandom(step, percent_do_water):
            if self.factory.can_water(step):
                return self.factory.do_water(step)
