import math
import sys
import time

from .cell import Cell
from .role import Role
from .util import C, Resource, log, profileit


class RoleMiner(Role):
    '''Mining resources'''
    NAME = 'miner'

    FORGE_DIST = 5  # TODO A/B test

    def __init__(self, step, unit, factory, resource_cell, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.resource_cell = resource_cell
        # TODO better choice here
        self.goal = goal or factory

    def __repr__(self):
        resource = 'Ice' if self.resource_cell.ice else 'Ore'
        fgoal = '*' if self.goal is self.factory else ''
        rgoal = '*' if self.goal is self.resource_cell else ''
        return f'{resource}Miner[{self.factory}{fgoal} -> {self.resource_cell}{rgoal}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.resource_cell),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        resource_cell = cls.deserialize_obj(unit.board, role_data[2])
        goal = cls.deserialize_obj(unit.board, role_data[3])
        return RoleMiner(step, unit, factory, resource_cell, goal=goal)

    @classmethod
    @profileit
    def from_cell(cls, step, unit, resource_cell):
        if not resource_cell:
            return

        assert resource_cell.ice or resource_cell.ore
        board = unit.board
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        if (not resource_cell.assigned_unit(step)
            or (unit.type == 'HEAVY' and resource_cell.assigned_unit(step).type == 'LIGHT')):
            cls._handle_displaced_unit(step, resource_cell)
            return RoleMiner(step, unit, factory, resource_cell)

    @classmethod
    @profileit
    def _factory_needs_water(cls, step, factory, step_count, skip_unit=None):
        board = factory.board
        i = step - board.step

        if step + step_count > 1100:
            step_count -= int(0.5 * (step + step_count - 1100))

        # If factory can survive ~250+ turns as is, no need to add ice miner
        factory_water = factory.water[i] + factory.ice[i] // board.env_cfg.ICE_WATER_RATIO
        if factory_water >= 300:
            water_income_without_unit = factory.get_water_income(step, skip_unit=skip_unit)
            water_profit = water_income_without_unit - factory.water_cost(step) - 1
            if factory_water + step_count * water_profit > 0:
                return False
        return True

    # TODO: don't mine adjacent to antagonizer
    # TODO: don't mine adjacent to opp mine (of same/greater weight)
    @classmethod
    @profileit
    def from_resource_route(cls, step, unit, ice=False, ore=False, dist_lim=None, max_count=100):
        assert ice or ore
        assert not (ice and ore)
        board = unit.board
        i = step - board.step

        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)

        # Limit the ratio of miners at a factory
        factory_miners = [u for u in factory.units(step) if u.role and u.role.NAME == 'miner']
        # TODO: not sure how important this check is anymore
        if unit.type == 'LIGHT' and (len(factory_miners) > len(factory.units(step)) // 2):
            return

        # Don't start ice mining if we don't need water
        if ice and not cls._factory_needs_water(step, factory, 200):
            return

        # Check max_count for like miners at this factory
        if max_count < 100:
            like_miners = [u for u in factory_miners
                           if (((ice and u.role.resource_cell.ice)
                                or (ore and u.role.resource_cell.ore))
                               and u.type == unit.type)]
            if 1 + len(like_miners) > max_count:
                return

        resource_cells = [(r[-1], len(r)-1) for r in factory.resource_routes
                          if ((ice and r[-1].ice) or (ore and r[-1].ore))]
        best_cell, min_cost, min_dist = None, C.UNREACHABLE, C.UNREACHABLE
        for resource_cell, man_dist in resource_cells:
            if (man_dist > dist_lim
                or man_dist > min_dist + 2):
                break
            if not cls._dest_is_safe(step, unit, resource_cell):
                continue

            assigned_unit = resource_cell.assigned_unit(step)
            if (not assigned_unit
                or (unit.type == 'HEAVY' and assigned_unit.type == 'LIGHT')
                or (unit.type == assigned_unit.type
                    and unit.assigned_factory is not None
                    and resource_cell.assigned_factory is not None
                    and resource_cell.assigned_factory is unit.assigned_factory
                    and resource_cell.assigned_factory is not assigned_unit.assigned_factory)):
                cost, dist, _ = board.dist(
                    step, factory.cells(), unit,
                    dest_cell=resource_cell,
                    avoid_cond=lambda s,c: (c.assigned_unit(s) or c.factory()),
                    dist_lim=dist_lim)

                if dist == C.UNREACHABLE:
                    pass
                elif dist < min_dist:
                    best_cell, min_cost, min_dist = resource_cell, cost, dist
                elif dist == min_dist:
                    if ((best_cell.is_contested() and not resource_cell.is_contested())
                        or (best_cell.is_contested() == resource_cell.is_contested()
                            and cost < min_cost)):
                        best_cell, min_cost = resource_cell, cost
                else:
                    break

        if best_cell:
            cls._handle_displaced_unit(step, best_cell)
            return RoleMiner(step, unit, factory, best_cell)

    @classmethod
    @profileit
    def from_transition_heavy_to_uncontested_ice(cls, step, unit):
        board = unit.board
        i = step - board.step

        # TODO only for lonely heavies?
        # Changing cell might be better than using a protector. Only for factories w/ <= 2 heavies?
        if (i != 0
            or unit.type != 'HEAVY'
            or not unit.role
            or unit.role.NAME != 'miner'
            or not unit.role.resource_cell.ice
            or not unit.role.resource_cell.is_contested()
            or not unit.is_antagonized(step)):
            return

        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)
        ice_routes = [r for r in factory.resource_routes if r[-1].ice]
        ice_cells = [r[-1] for r in ice_routes if len(r) - 1 <= 12]
        for ice_cell in ice_cells:
            ice_cell_dist = ice_cell.man_dist_factory(factory)
            nearest_opp_factory = ice_cell.nearest_factory(board, player_id=board.opp.id)
            ice_cell_opp_dist = ice_cell.man_dist_factory(nearest_opp_factory)
            if (ice_cell_dist <= 8
                and ice_cell_dist < ice_cell_opp_dist
                and (not ice_cell.assigned_unit(step)
                     or ice_cell.assigned_unit(step).type == 'LIGHT')
                and not ice_cell.is_contested()):
                log(f'{unit} change ice cell from {unit.role.resource_cell} to uncontested {ice_cell}')
                cls._handle_displaced_unit(step, ice_cell)
                return RoleMiner(step, unit, factory, ice_cell)

    @classmethod
    @profileit
    def _power_ok(cls, step, unit, factory, steps_threshold=100):
        i = step - factory.board.step
        power_usage = factory.power_usage(step, skip_unit=unit) + (0.8 * 60 + 0.1 * 20 - 6)
        power_gain = factory._power_gain
        factory_power = factory.power[i] + (unit.power[i] if unit else 0)

        power_steps_remaining = 1000
        if power_usage > power_gain:
            power_steps_remaining = factory_power / (power_usage - power_gain)
        #if i == 0 and steps_threshold == 100:
        #    log(f'{factory} power_ok(): {factory_power}p + {power_gain}p/t - {power_usage}p/t '
        #        f'-> {power_steps_remaining}t')
        return power_steps_remaining >= steps_threshold

    @classmethod
    @profileit
    def _ore_digs(cls, step, unit, factory):
        board = unit.board
        i = step - board.step

        factory_ore = factory.ore[i] + factory.metal[i] * board.env_cfg.ORE_METAL_RATIO
        factory_metal = factory.metal[i] + factory.ore[i] // board.env_cfg.ORE_METAL_RATIO
        light_lim = C.LIGHT_LIM + (step // 100)
        # TODO: include lights relocating to here?
        light_count = len([u for u in factory.units(step)
                           if u.type == 'LIGHT' and (not u.role or u.role.NAME != 'relocate')])
        future_lights = factory_metal // board.env_cfg.ROBOTS['LIGHT'].METAL_COST
        future_lights = max(0, min(future_lights, light_lim - light_count))
        extra_ore = (future_lights
                     * board.env_cfg.ROBOTS['LIGHT'].METAL_COST
                     * board.env_cfg.ORE_METAL_RATIO
                     + (factory_ore % board.env_cfg.ORE_METAL_RATIO))
        if light_count + future_lights <= 3 * light_lim / 4:
            ore_digs = math.ceil((200 - extra_ore) / 20)
        else:
            ore_digs = math.ceil((500 - extra_ore) / 20)
        return ore_digs

    @classmethod
    @profileit
    def from_transition_heavy_to_ore(cls, step, unit):
        board = unit.board
        i = step - board.step

        if (step < 25
            or step >= C.END_PHASE - 15
            or unit.type == 'LIGHT'):
            return

        unit_is_exempt = lambda u: (
            u.role
            and ((u.role.NAME == 'recharge' and not u.cell(step).factory())
                 or (u.role.NAME == 'cow' and u.role.repair)
                 or (u.role.NAME == 'attacker' and not u.role.defender)
                 or u.role.NAME == 'sidekick'
                 or u.role.NAME == 'protector'
                 or u.role.NAME == 'generator'
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (u.role.NAME == 'miner' and u.role.resource_cell.ore)
                 or (u.role.NAME == 'miner' and u.protectors[i])
                 or u.role.NAME == 'relocate'))

        if unit_is_exempt(unit):
            #if step == unit.board.step and unit.assigned_factory and unit.assigned_factory.id == 6:
            #    log(f'{unit} transition_to_ore fail A')
            return

        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)
        if (factory.mode and factory.mode.NAME == 'ice_conflict'
            and (unit.role is None
                 or unit.role.NAME in ('antagonizer', 'miner', 'recharge'))):
            #if i == 0 and factory.id == 6:
            #    log(f'{unit} transition_to_ore fail B')
            return

        # One at a time
        heavy_units = [u for u in factory.units(step) if u.type == 'HEAVY']
        any_ore_miners = any(u.role and u.role.NAME == 'miner' and u.role.resource_cell.ore
                             for u in heavy_units)
        if any_ore_miners:
            #if i == 0 and factory.id == 6:
            #    log(f'{unit} transition_to_ore fail C')
            return

        light_count = len([u for u in factory.units(step)
                           if u.type == 'LIGHT' and (not u.role or u.role.NAME != 'relocate')])

        # Assume the unit could use at least half of the factory's current power
        factory_power = factory.power[i] - factory.power_reserved(step)
        if len(heavy_units) == 1:
            unit_power = unit.power[i] + factory_power
        else:
            unit_power = unit.power[i] + (factory_power // 2)

        ore_digs = cls._ore_digs(step, unit, factory)
        if ore_digs <= 0:
            #if i == 0 and factory.id == 6:
            #    log(f'{unit} transition_to_ore fail D')
            return

        power_threshold = 3 * unit.cfg.MOVE_COST + ore_digs * unit.cfg.DIG_COST
        if unit_power < power_threshold:
            #if i == 0 and factory.id == 6:
            #    log(f'{unit} transition_to_ore fail E')
            return

        # Verify an acceptable route exists.
        ore_routes = [r for r in factory.resource_routes if r[-1].ore]
        ore_route, best_score = None, -C.UNREACHABLE
        for r in ore_routes:
            rdist = len(r) - 1
            if rdist > 20:
                break

            c = r[-1]
            if c.assigned_unit(step) and c.assigned_unit(step).type == 'HEAVY':
                continue

            # Want high opp dist
            # Really don't want opp_dist=1
            # Want low rdist
            opp_dist = c.nearest_factory_dist(board, player_id=board.opp.id)
            if rdist == 1:
                score = -rdist + 5 + 0.001 * opp_dist
            elif opp_dist == 1:
                score = -rdist + opp_dist - 4
            else:
                score = -rdist + min(4, opp_dist) + 0.001 * opp_dist

            if score > best_score:
                ore_route, best_score = r, score

        if not ore_route:
            #if i == 0 and factory.id == 6:
            #    log(f'{unit} transition_to_ore fail F')
            return

        # If (ignoring unit) there are no heavy ice miners, verify that we have enough water to last
        other_heavy_ice_miners = [u for u in heavy_units
                                  if (u.role
                                      and u.role.NAME == 'miner'
                                      and u.role.resource_cell.ice
                                      and u is not unit)]
        if not other_heavy_ice_miners:
            # Need a safe amount of water
            water_transporters = [u for u in board.player.units()
                                  if (u.role
                                      and u.role.NAME == 'water_transporter'
                                      and u.role.target_factory is factory)]
            factory_water = (factory.water[i]
                             + (factory.ice[i] + unit.ice[i]) // board.env_cfg.ICE_WATER_RATIO)
            water_threshold = (2 * len(ore_route)
                               + ore_digs
                               + 20
                               + 50 * len(water_transporters))
            if factory.cell().nearest_factory_dist(board, player_id=board.opp.id) < 20:
                water_threshold += 40
            if factory_water < water_threshold:
                #if i == 0 and factory.id == 6:
                #    log(f'{unit} transition_to_ore fail G')
                return

        # Only ok to redirect an ice miner if that's the only choice
        if unit.role and unit.role.NAME == 'miner':  # heavy ice miner
            all_heavy_ice = all(
                u.role
                and ((u.role.NAME == 'miner' and u.role.resource_cell.ice)
                     or (u.role.NAME == 'antagonizer' and u.role.target_cell.ice)
                     or unit_is_exempt(u))
                for u in heavy_units)
            if not all_heavy_ice:
                #if i == 0 and factory.id == 6:
                #    log(f'{unit} transition_to_ore fail H')
                return

        # Factory must have high power or high power income
        if not cls._power_ok(step, unit, factory):
            return

        ore_cell = ore_route[-1]
        assert ore_cell.ore
        rubble_to = sum(c.rubble[i] for c in ore_route)
        rubble_from = rubble_to - ore_cell.rubble[i]
        rubble_digs = math.ceil(ore_cell.rubble[i] / unit.cfg.DIG_RUBBLE_REMOVED)
        route_dist = len(ore_route) - 1
        total_steps = 2 * route_dist + rubble_digs + ore_digs
        power_gain = unit.power_gain(step, end_step=step+total_steps)
        extra_buffer = (
            unit.cfg.ACTION_QUEUE_POWER_COST
            + unit.cfg.DIG_COST
            + 2 * unit.cfg.MOVE_COST * route_dist)
        power_needed = (
            2 * unit.cfg.ACTION_QUEUE_POWER_COST
            + (rubble_to + rubble_from) * unit.cfg.RUBBLE_MOVEMENT_COST
            + (2 * route_dist) * unit.cfg.MOVE_COST
            + (rubble_digs + ore_digs) * unit.cfg.DIG_COST
            + extra_buffer
            - power_gain)
        if unit_power >= power_needed:
            cost_to, _, _ = board.dist(
                step, unit.cell(step), unit, dest_cell=ore_route[-1],
                avoid_cond=lambda _,c: c.factory() and c.factory().player_id != unit.player_id)
            cost_from, _, _ = board.dist(
                step, ore_route[-1], unit, dest_cell=ore_route[0],
                avoid_cond=lambda _,c: c.factory() and c.factory().player_id != unit.player_id)
            actual_power_needed = (
                2 * unit.cfg.ACTION_QUEUE_POWER_COST
                + cost_to
                + cost_from
                + (rubble_digs + ore_digs) * unit.cfg.DIG_COST
                + extra_buffer
                - power_gain)
            if unit_power >= actual_power_needed:
                # Handle potential unit being displaced.
                cls._handle_displaced_unit(step, ore_cell)
                return RoleMiner(step, unit, factory, ore_cell)
            else:
                #if i == 0 and factory.id == 6:
                #    log(f'{unit} transition_to_ore fail K')
                pass
        else:
            #if i == 0 and factory.id == 6:
            #    log(f'{unit} transition_to_ore fail J')
            pass

    @classmethod
    @profileit
    def from_transition_active_ice_miner_to_closer_cell(cls, step, unit):
        if (not unit.type == 'HEAVY'
            or not unit.role
            or not unit.role.NAME == 'miner'
            or not unit.role.resource_cell.ice):
            return

        factory = unit.role.factory
        resource_dist = unit.role.resource_cell.man_dist_factory(factory)
        if resource_dist == 1:
            return

        currently_contested = unit.role.resource_cell.is_contested()
        resource_cells = [r[-1] for r in factory.resource_routes if r[-1].ice and (len(r) - 1) == 1]
        best_cell, min_dist = None, C.UNREACHABLE
        for resource_cell in resource_cells:
            if not currently_contested and resource_cell.is_contested():
                continue
            assigned_unit = resource_cell.assigned_unit(step)
            if (not assigned_unit
                or (assigned_unit.type == 'LIGHT')
                or (unit.type == assigned_unit.type
                    and unit.assigned_factory is not None
                    and resource_cell.assigned_factory is not None
                    and resource_cell.assigned_factory is unit.assigned_factory
                    and resource_cell.assigned_factory is not assigned_unit.assigned_factory)):
                if 1 < min_dist:
                    best_cell, min_dist = resource_cell, 1
                elif 1 == min_dist:
                    if best_cell.is_contested() and not resource_cell.is_contested():
                        best_cell = resource_cell
                else:
                    break

        if best_cell:
            if step == unit.board.step:
                log(f'{unit} ice miner transition {unit.role.resource_cell} -> {best_cell}')
            cls._handle_displaced_unit(step, best_cell)
            return RoleMiner(step, unit, factory, best_cell)

    def is_valid(self, step):
        board = self.unit.board
        i = step - board.step

        # Check that factory still exists
        if self.factory is None:
            return False

        # Check that lights are not threatened by heavies around resource_cell
        if not self._dest_is_safe(step, self.unit, self.resource_cell):
            return False

        # Stop mining ice if factory has sufficient water
        if (self.resource_cell.ice
            and not self._factory_needs_water(step, self.factory, 250, skip_unit=self.unit)):
            if i == 0:
                log(f'{self.unit} {self} invalidating due to high water')
            return False

        # Stop mining ore if factory cannot support more units
        resource_dist = self.resource_cell.man_dist_factory(self.factory)
        if self.unit.type == 'HEAVY' and self.resource_cell.ore and self.unit.ore[i] == 0:

            #light_lim = C.LIGHT_LIM + (step // 100)
            #light_count = len([u for u in self.factory.units(step)
            #                   if u.type == 'LIGHT' and (not u.role or u.role.NAME != 'relocate')])

            if (not (self.factory.mode and self.factory.mode.NAME == 'forge')
                and not (self._power_ok(step, self.unit, self.factory, steps_threshold=50))):
                # TODO: special treatment for dist1 ore?
                #       maybe just if there are at least 3 heavies here?
                #or (resource_dist == 1
                #    and self._power_ok(step, self.unit, self.factory, steps_threshold=25)))):
                #         or (resource_dist == 1 and light_count < light_lim))):
                return False

        # Stop mining ore if this is factory's only heavy and factory is low on water.
        heavy_units = [u for u in self.factory.units(step)
                       if (u.type == 'HEAVY'
                           and (not u.role
                                or u.role.NAME != 'generator'))]
        if self.unit.type == 'HEAVY' and self.resource_cell.ore:
            if len(heavy_units) == 1:
                ice_routes = [r for r in self.factory.resource_routes if r[-1].ice]
                ice_dist = len(ice_routes[0]) - 1 if ice_routes else 0
                move_dist = (self.unit.cell(step).man_dist(self.resource_cell)
                             + self.resource_cell.man_dist_factory(self.factory)
                             + 2 * ice_dist)

                if self.factory.water[i] < move_dist + 20:
                    return False
                if (self.factory.cell().nearest_factory_dist(board, player_id=board.opp.id) < 20
                    and self.factory.water[i] < move_dist + 60):
                    return False

                # No sense getting antagonized here - we may need to switch to defensive ice conflict
                if (self.factory.water[i] < move_dist + 100
                    and self.unit.cell(step).man_dist(self.resource_cell) < 2
                    and self.unit.is_antagonized(board.step)):
                    return False
            # This is the right idea, but if factory power is low or being antagonized, calculation
            # of water income is way off due to lack of efficiency.
            #if self.factory.water[i] < move_dist + 15:
            #    water_income = self.factory.get_water_income(step)
            #    if water_income < 1:
            #        water_loss = abs(water_income - 1)
            #        if self.factory.water[i] < water_loss * (move_dist + 15):
            #            return False

        return True

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)
        self.resource_cell.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            self.resource_cell.unset_assignment(step, self.unit)

    def goal_cell(self, step):
        # Temporarily override goal cell if on factory center
        if self.unit.cell(step) is self.get_factory().cell():
            return self.resource_cell

        if isinstance(self.goal, Cell):
            return self.goal

        # Goal is a factory
        # TODO: Using factory.cell() can be inefficient
        #       e.g.   X X X
        #              X X X
        #            O X X X
        # O can move up or right and be equally dist2 from the center
        # We really don't want to move up here.
        # Therefore for heavies: find the nearest non-heavy-assigned cell
        if self.unit.type == 'HEAVY':
            cur_cell = self.unit.cell(step)
            best_cell, min_man_dist = None, C.UNREACHABLE
            for cell in self.goal.cells():
                man_dist = cur_cell.man_dist(cell)
                assigned_unit = cell.assigned_unit(step)
                if (man_dist < min_man_dist
                    and (not assigned_unit
                         or assigned_unit.type == 'LIGHT'
                         or (assigned_unit.role
                             and assigned_unit.role.NAME == 'transporter'
                             and assigned_unit.role.destination is self.unit))):
                    best_cell, min_man_dist = cell, man_dist
            if best_cell:
                return best_cell

        return self.goal.cell()

    def update_goal(self, step):
        assert self.goal
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        unit_power = self.unit.power[i]
        unit_resource = max(self.unit.ice[i], self.unit.ore[i])
        resource_dist = self.resource_cell.man_dist_factory(self.factory)

        if self.goal is self.resource_cell:
            # 1 AQ + 1 dig + 2 * (move to factory)
            # TODO: improve e.g. factor in daylight power gen, rubble
            # TODO: make sure we finish mining for heavy ore if possible

            heavies = [u for u in self.factory.units(step) if u.type == 'HEAVY']
            lights_relocating_here = len([u for u in board.player.units()
                                          if (u.role
                                              and u.role.NAME == 'relocate'
                                              and u.role.target_factory is self.factory)])
            light_count = (lights_relocating_here
                           + len([u for u in self.factory.units(step)
                                  if (u.type == 'LIGHT'
                                      and (not u.role or u.role.NAME != 'relocate'))]))
            light_lim = C.LIGHT_LIM + (step // 100)

            power_threshold = 0  # Handled by RoleRecharge
            if resource_dist <= self.FORGE_DIST:
                power_threshold = (
                    self.unit.cfg.ACTION_QUEUE_POWER_COST
                    + board.naive_cost(step, self.unit, cur_cell, self.resource_cell)
                    + self.unit.cfg.DIG_COST
                    + board.naive_cost(
                        step, self.unit, self.resource_cell, self.factory.cell(), is_factory=True))
            resource_threshold = (3 * self.unit.cfg.CARGO_SPACE // 4
                                  if step < 200
                                  else self.unit.cfg.CARGO_SPACE)
            if (unit_power < power_threshold) or (unit_resource >= resource_threshold):
                #if i == 0 and self.unit.id == 13:
                #    log(f'Return to factory A')
                self.goal = self.factory

            # Head back a little "early" if an opp is nearby
            elif (i == 0
                  and self.unit.low_power
                  and self.unit.threatened_by_opp(step, cur_cell)[0]):
                #if i == 0 and self.unit.id == 13:
                #    log(f'Return to factory B')
                self.goal = self.factory

            # Go back to factory early if we now have materials to build a heavy
            elif (self.unit.ore[i]
                  and (self.factory.metal[i]
                       + self.factory.ore[i] // board.env_cfg.ORE_METAL_RATIO
                       < board.env_cfg.ROBOTS["HEAVY"].METAL_COST)
                  and (self.factory.metal[i]
                       + (self.factory.ore[i] + self.unit.ore[i]) // board.env_cfg.ORE_METAL_RATIO
                       >= board.env_cfg.ROBOTS["HEAVY"].METAL_COST)):
                #if i == 0 and self.unit.id == 13:
                #    log(f'Return to factory C')
                self.goal = self.factory

            # Go back to factory early if we now have materials to build 4 lights for a forge
            elif (self.unit.ore[i]
                  and ((self.factory.mode and self.factory.mode.NAME == 'forge')
                       or (light_count <= light_lim - 4 and resource_dist <= self.FORGE_DIST))
                  and (self.factory.metal[i]
                       + self.factory.ore[i] // board.env_cfg.ORE_METAL_RATIO
                       < 4 * board.env_cfg.ROBOTS["LIGHT"].METAL_COST)
                  and (self.factory.metal[i]
                       + (self.factory.ore[i] + self.unit.ore[i]) // board.env_cfg.ORE_METAL_RATIO
                       >= 4 * board.env_cfg.ROBOTS["LIGHT"].METAL_COST)):
                #if i == 0 and self.unit.id == 13:
                #    log(f'Return to factory D')
                self.goal = self.factory

            # Go back to factory early if factory is low on water
            # TODO: and there is no heavy ice miner
            elif (self.unit.ice[i]
                  and (self.factory.water[i]
                       + self.factory.ice[i] // board.env_cfg.ICE_WATER_RATIO
                       < 10 + cur_cell.man_dist_factory(self.factory))):
                #if i == 0 and self.unit.id == 13:
                #    log(f'Return to factory E')
                self.goal = self.factory

            # Go back to factory early if we have water and game is almost over
            elif (step + resource_dist >= C.ICE_MINE_RUSH
                  and self.unit.ice[i] >= 4 * self.unit.cfg.DIG_RESOURCE_GAIN):
                #if i == 0 and self.unit.id == 13:
                #    log(f'Return to factory F')
                self.goal = self.factory

            # Deliver ice to factory early if we are an early-game solo heavy ice miner
            elif (step < 200
                  and step % 3 == 0
                  and self.resource_cell.ice
                  and self.unit.ice[i]
                  and self.unit.type == 'HEAVY'
                  and len(heavies) == 1
                  and self.unit.transporters[i]
                  and self.factory.power[i] < 2000
                  and (self.factory.water[i] + self.unit.ice[i] // 4) > 250
                  and self.factory.mode
                  and self.factory.mode.NAME != 'ice_conflict'):
                self.goal = self.factory

            # Deliver ice to factory early if factory is too low on water to water lichen
            elif (self.resource_cell.ice
                  and resource_dist == 1
                  and self.unit.ice[i] >= 100
                  and self.unit.type == 'HEAVY'
                  and self.unit.transporters[i]
                  and (self.factory.water[i] + self.factory.ice[i] // 4) < 55
                  and self.factory.mode
                  and self.factory.mode.NAME != 'ice_conflict'):
                self.goal = self.factory

            # ~~
            # Deliver ice to factory early if water transporter is here and could pick up
            # ice instead of water
            if (self.goal is not self.factory
                and self.resource_cell.ice
                and resource_dist == 1
                and self.unit.type == 'HEAVY'
                and self.factory.ice[i] < 100
                and self.unit.ice[i] >= 100):
                water_transporter = None
                for cell in self.factory.cells():
                    wt = cell.unit(step)
                    if (wt
                        and wt.role
                        and wt.role.NAME == 'water_transporter'
                        and wt.role.target_factory is self.factory
                        and wt.ice[i] == 0
                        and wt.water[i] == 0
                        and int(1.5 * wt.role.factory.cell().man_dist_factory(self.factory)) <= 25):
                        if i == 0:
                            log(f'{self.unit} deliver ice early for WT {wt}')
                        self.goal = self.factory

        elif self.goal is self.factory:
            if self._is_patient(step):
                power_threshold = 100
            elif self.factory.power[i] >= 5000:
                # Make sure we go fill up while we're at/near factory if it has lots of power
                power_threshold = 2900
            else:
                power_threshold = (
                    self.unit.cfg.ACTION_QUEUE_POWER_COST
                    + board.naive_cost(step, self.unit, cur_cell, self.resource_cell)
                    + 6 * self.unit.cfg.DIG_COST
                    + board.naive_cost(
                        step, self.unit, self.resource_cell, self.factory.cell(), is_factory=True))
                if self.resource_cell.ore:
                    if resource_dist <= self.FORGE_DIST:
                        ore_digs = self._ore_digs(step, self.unit, self.factory)
                    else:
                        ore_digs = 25
                    power_threshold += (ore_digs - 4) * self.unit.cfg.DIG_COST  # Total of 25+2 digs
                    power_threshold = min(power_threshold, self.unit.cfg.BATTERY_CAPACITY)
            if (unit_power >= power_threshold
                and unit_resource == 0):
                self.goal = self.resource_cell

        else:
            assert False

    def _is_patient(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        transporter = board.units[self.unit.transporters[i][0]] if self.unit.transporters[i] else None
        resource_dist = self.resource_cell.man_dist_factory(self.factory)

        if (self.goal is self.factory
            and self.unit.type == 'HEAVY'
            and transporter
            and resource_dist == 1
            and cur_cell is self.resource_cell
            and transporter.cell(step) is transporter.role.factory_cell
            and transporter.role.factory_cell.man_dist(cur_cell) == 1
            #and self.factory.power[i] < 400
            ):
            threats_exist = False
            for neighbor in cur_cell.neighbors():
                opp_unit = neighbor.unit(step)
                if (opp_unit
                    and opp_unit.type == 'HEAVY'
                    and opp_unit.player_id != self.unit.player_id):
                    threats_exist = True
            if not threats_exist:
                return True
        return False

    def do_move(self, step):
        board = self.unit.board
        i = step - board.step

        # Make sure we lock in a move before a transporter may try to transfer power here
        if self.unit._lie_step is not None and step >= self.unit._lie_step:
            return self.unit.do_no_move(step)

        # Don't move if we can just wait on our transporter
        if self._is_patient(step):
            if i == 0:
                log(f'{self.unit} patient miner: no move')
            return self.unit.do_no_move(step)

        # If a protected miner, set up AQ lies
        # Enqueue a full threat, then lie with rest of AQ (when the protector is doing its 2nd threat)
        if (i > 0
            and self.unit.cell(step) is self.resource_cell
            and self.unit.protectors[i]):
            protector_id = self.unit.protectors[i][0]
            protector = board.units[protector_id]
            if protector.role.threat_count >= 2:
                #log(f'LIES! miner {self.unit} @ step{step}')
                self.unit.set_lie_step(step)
                return self.unit.do_no_move(step)

        # Important for miner to lock in move so that transporter knows where to transfer
        return self._do_move(step) or self.unit.do_no_move(step)

    def do_dig(self, step):
        i = step - self.unit.board.step
        cur_cell = self.unit.cell(step)
        goal_cell = self.goal_cell(step)

        if self.goal is self.resource_cell and cur_cell is goal_cell:
            if self.unit.power[i] >= self.unit.dig_cost(step):
                return self.unit.do_dig(step)

    def do_pickup(self, step):
        return self._do_power_pickup(step)

    def _do_excess_power_transfer(self, step):
        i = step - self.unit.board.step
        cur_cell = self.unit.cell(step)
        # Heavy
        # dist1
        # with transporter
        # at resource cell
        # power over 1500
        # factory power under 500
        # not ice conflict
        if (self.unit.type == 'HEAVY'
            and self.unit.transporters[i]
            and cur_cell is self.resource_cell
            and self.unit.power[i] >= 1500
            and self.factory.power[i] < 500
            and self.factory.mode
            and self.factory.mode.NAME != 'ice_conflict'
            and self.resource_cell.man_dist_factory(self.factory) == 1):
            amount = self.unit.power[i] - 600
            amount = (amount // 10) * 10
            if amount > 0:
                transfer_cell = cur_cell.neighbor_toward(self.factory.cell())
                if (self.unit.power[i]
                    >= self.unit.transfer_cost(step, transfer_cell, Resource.POWER, amount)):
                    if i == 0:
                        log(f'{self.unit} miner transfer excess power')
                    return self.unit.do_transfer(step, transfer_cell, Resource.POWER, amount)

    def do_transfer(self, step):
        # TODO: should heavy transfer ore in small increments to adjacent factory/transporter
        #       if 1) we already have another heavy at this factory and 2) we have enough ore
        #       to build another light
        return  self._do_transfer_resource_to_factory(step) or self._do_excess_power_transfer(step)

    def get_factory(self):
        return self.factory
