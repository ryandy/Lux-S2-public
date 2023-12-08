import sys

from .cell import Cell
from .role import Role
from .util import C, log, profileit


# TODO: relocate lights away from factories that have overbuilt
class RoleRelocate(Role):
    '''Reposition unit to new factory'''
    NAME = 'relocate'

    def __init__(self, step, unit, factory, target_factory, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.target_factory = target_factory
        self.goal = goal or factory

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        tgoal = '*' if self.goal is self.target_factory else ''
        return f'Relocate[{self.factory}{fgoal} -> {self.target_factory}{tgoal}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.target_factory),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        target_factory = cls.deserialize_obj(unit.board, role_data[2])
        goal = cls.deserialize_obj(unit.board, role_data[3])
        return RoleRelocate(step, unit, factory, target_factory, goal=goal)

    @classmethod
    @profileit
    def from_forge(cls, step, unit):
        if unit.type != 'LIGHT':
            return

        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        if not factory.mode or factory.mode.NAME != 'forge':
            return

        like_units = [u for u in factory.units(step) if u.type == unit.type]
        if len(like_units) <= 4:
            return

        best_factory, min_dist = None, C.UNREACHABLE
        relocate_units = [u for u in board.player.units() if u.role and u.role.NAME == 'relocate']
        for player_factory in board.player.factories():
            if player_factory is factory:
                continue
            factory_mode = (player_factory.mode and player_factory.mode.NAME)
            if factory_mode == 'forge':
                continue
            factory_like_units = ([u for u in player_factory.units(step) if u.type == unit.type]
                                  + [u for u in relocate_units
                                     if u.role.target_factory is player_factory])
            light_lim = 8 if factory_mode == 'ice_conflict' else C.LIGHT_LIM
            if len(factory_like_units) >= light_lim:
                continue
            dist = factory.cell().man_dist(player_factory.cell())
            if dist < min_dist:
                best_factory, min_dist = player_factory, dist
        if best_factory:
            #if i == 0:
            #    log(f'Relocate FROM FORGE! from: {factory}')
            #    log(f'Relocate FROM FORGE!   to: {best_factory}')
            return RoleRelocate(step, unit, factory, best_factory)

    # TODO: from_unit_surplus too?
    @classmethod
    @profileit
    def from_power_surplus(cls, step, unit):
        # If heavy: no other heavy relocate for this factory
        #           this factory has at least 3 heavies
        # If light: no more than ~3 others
        #           this factory has at least ~7 lights

        # If current factory has < ~200 power
        #    and power_usage > power_gain

        # For each other player factory:
        #   if factory power >= 5000
        #      or power_gain > power_usage + (15 if heavy else 1.5)

        board = unit.board
        i = step - board.step

        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        factory_power = factory.power[i] - factory.power_reserved(step)
        factory_power_income = factory._power_gain - factory._power_usage
        # TODO: also OK if light and like_units >= LIM + 2?
        if factory_power >= 3000 or factory_power_income > 0:
            return

        unit_threshold, relocate_lim = (3, 1) if unit.type == 'HEAVY' else (C.LIGHT_LIM-2, 2)
        like_units = [u for u in factory.units(step) if u.type == unit.type]
        if len(like_units) < unit_threshold:
            return
        likes_relocating = [u for u in like_units if u.role and u.role.NAME == 'relocate']
        if len(likes_relocating) >= relocate_lim:
            return

        best_factory, min_dist = None, C.UNREACHABLE
        for player_factory in board.player.factories():
            if player_factory is factory:
                continue
            # TODO: also consider number of like units at target factory?
            #       also consider in-flight relocate units heading to target factory?
            f_power = player_factory.power[i] - player_factory.power_reserved(step)
            f_power_income = player_factory._power_gain - player_factory._power_usage
            if f_power < 4000 or f_power_income < 20:
                continue
            dist = factory.cell().man_dist(player_factory.cell())
            if dist < min_dist:
                best_factory, min_dist = player_factory, dist
        if best_factory:
            if i == 0:
                log(f'Relocate {unit} from {factory} to {best_factory} (power)')
            return RoleRelocate(step, unit, factory, best_factory)

    @classmethod
    @profileit
    def from_idle(cls, step, unit):
        '''Move idle unit to a factory with some power and no idle units'''
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        unit_threshold, relocate_lim = (3, 1) if unit.type == 'HEAVY' else (C.LIGHT_LIM-2, 2)
        like_units = [u for u in factory.units(step) if u.type == unit.type]
        likes_relocating = [u for u in like_units if u.role and u.role.NAME == 'relocate']
        if len(likes_relocating) >= relocate_lim:
            return

        best_factory, min_dist = None, C.UNREACHABLE
        for player_factory in board.player.factories():
            if player_factory is factory:
                continue

            # Relocating units count as idle for the giving factory at this stage of the game
            # It means there is nothing better to do
            likes_idle = [u for u in player_factory.units(step)
                          if (u.type == unit.type
                              and u.role
                              and ((u.role.NAME == 'recharge' and u.cell(step).factory())
                                   or u.role.NAME == 'relocate'))]
            if likes_idle:
                continue
            f_power = player_factory.power[i] - player_factory.power_reserved(step)
            f_power_income = player_factory._power_gain - player_factory._power_usage
            if f_power < 4000 and f_power_income < 20:  # Either one being high is good enough
                continue
            dist = factory.cell().man_dist(player_factory.cell())
            if dist < min_dist:
                best_factory, min_dist = player_factory, dist
        if best_factory:
            if i == 0:
                log(f'Relocate {unit} from {factory} to {best_factory} (idle)')
            return RoleRelocate(step, unit, factory, best_factory)

    @classmethod
    @profileit
    def from_transition_assist_ice_conflict(cls, step, unit):
        if unit.type == 'LIGHT':
            return

        if (unit.role
            and ((unit.role.NAME == 'recharge' and not unit.cell(step).factory())
                 or (unit.role.NAME == 'cow' and unit.role.repair)
                 or (unit.role.NAME == 'attacker' and unit.role.low_power_target)
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'relocate'
                     and unit.role.target_factory.mode
                     and unit.role.target_factory.mode.NAME == 'ice_conflict')
                 or unit.role.NAME == 'protector')):
            return

        board = unit.board
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)
        other_heavies = [u for u in factory.units(step)
                         if (u.type == 'HEAVY'
                             and u is not unit)]
        other_heavy_ice_miners = [u for u in other_heavies
                                  if (u.role
                                      and u.role.NAME == 'miner'
                                      and u.role.resource_cell.ice)]
        if not other_heavy_ice_miners:
            return

        if (len(other_heavies) <= 1
            and factory.cell().nearest_factory_dist(board, player_id=board.opp.id) < 10):
            return

        return cls.from_assist_ice_conflict(step, unit)

    @classmethod
    @profileit
    def from_assist_ice_conflict(cls, step, unit):
        '''Move heavy to defensive ice conflict factory or factory with 0 heavies'''
        if step < 10:
            return

        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)
        if factory.mode and factory.mode.NAME == 'ice_conflict':
            return

        best_factory, min_dist = None, C.UNREACHABLE
        for player_factory in board.player.factories():
            if player_factory is factory:
                continue

            # want to relocate up to 1 heavy to a defensive ice conflict
            # want to relocate up to 1 heavy to any factory
            # want to relocate up to 4 lights to any ice conflict

            likes = ([u for u in player_factory.units(step) if u.type == unit.type]
                     + [u for u in board.player.units()
                        if (u.type == unit.type
                            and u.role
                            and u.role.NAME == 'relocate'
                            and u.role.target_factory is player_factory)])

            is_ice_conflict = player_factory.mode and player_factory.mode.NAME == 'ice_conflict'
            is_defensive = is_ice_conflict and player_factory.mode.defensive
            if ((unit.type == 'HEAVY' and len(likes) == 0)
                or (unit.type == 'HEAVY' and is_defensive and len(likes) < 2)
                or (unit.type == 'LIGHT' and is_ice_conflict and len(likes) < 4)):
                dist = factory.cell().man_dist(player_factory.cell())
                if dist < min_dist:
                    best_factory, min_dist = player_factory, dist
        if best_factory:
            if i == 0:
                log(f'Relocate {unit} from {factory} to {best_factory} (assist)')
            return RoleRelocate(step, unit, factory, best_factory)

    # TODO if originally relocating due to a non-defensive ice conflict and it is no longer
    #      in mode ice_conflict, invalidate (defensive ones may still need help, maybe just track
    #      the target factory either way and if it's gone then we can invalidate)
    #      probably depends on current location, if less than halfway there, can invalidate
    def is_valid(self, step):
        if not self.target_factory:
            return False

        # If source factory exploded, just skip ahead and update assignment + invalidate
        if not self.factory:
            self.unit.assigned_factory = self.target_factory
            return False

        # After reaching target factory, update factory assignment and invalidate this role.
        cur_cell = self.unit.cell(step)
        if (self.goal is self.target_factory
            and cur_cell.man_dist_factory(self.target_factory) <= 1):
            self.unit.assigned_factory = self.target_factory
            return False

        return True

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)

    def goal_cell(self, step):
        board = self.unit.board
        i = step - board.step

        # Temporarily override goal cell if on factory center
        if self.unit.cell(step) is self.get_factory().cell():
            return self.target_factory.cell()

        # Goal is a factory
        return self.goal.cell()

    def update_goal(self, step):
        assert self.goal

        # One-way ticket
        if self.goal is self.target_factory:
            return

        board = self.unit.board
        i = step - board.step

        if self.goal is self.factory:
            cur_cell = self.unit.cell(step)
            power_threshold = 2 * board.naive_cost(
                step, self.unit, cur_cell, self.target_factory.cell())
            power_threshold = min(power_threshold, 9 * self.unit.cfg.BATTERY_CAPACITY // 10)
            if self.unit.power[i] >= power_threshold:
                self.goal = self.target_factory
        else:
            assert False

    def do_move(self, step):
        return self._do_move(step)

    def do_dig(self, step):
        return

    def do_pickup(self, step):
        # power_threshold copied from update_goal
        # Don't take too much, this is especially important for early-game units from forges,
        # as we don't want to slow down the power flow to the heavy miner.
        # But also potentially important all game, as relocating units tend to be departing
        # from low-power factories.
        board = self.unit.board
        cur_cell = self.unit.cell(step)
        power_threshold = 2 * board.naive_cost(
            step, self.unit, cur_cell, self.target_factory.cell())
        max_amount = (power_threshold
                      if power_threshold <= 9 * self.unit.cfg.BATTERY_CAPACITY // 10
                      else None)
        return self._do_power_pickup(step, max_amount=max_amount)

    def do_transfer(self, step):
        return self._do_transfer_resource_to_factory(step)

    def get_factory(self):
        return self.factory
