import sys

from .cell import Cell
from .role import Role
from .util import C, Resource, log, profileit

'''
Uses:
 - ModeIceConflict survival
 - ModeOreOnly survival
 - endgame water balancing
'''


# TODO: do more than 50 water if doing endgame water balancing
class RoleWaterTransporter(Role):
    '''Move water from factory to factory'''
    NAME = 'water_transporter'

    def __init__(self, step, unit, factory, target_factory, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.target_factory = target_factory
        self.goal = goal or factory

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        tgoal = '*' if self.goal is self.target_factory else ''
        return f'WaterTransporter[{self.factory}{fgoal} -> {self.target_factory}{tgoal}]'

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
        return RoleWaterTransporter(step, unit, factory, target_factory, goal=goal)

    @classmethod
    @profileit
    def from_transition_ice_conflict_factory(cls, step, unit):
        board = unit.board
        i = step - board.step
        if unit.type != 'LIGHT':
            return

        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)
        if not factory.mode or factory.mode.NAME != 'ice_conflict':
            return

        if (unit.role
            and (unit.role.NAME == 'blockade'
                 or unit.role.NAME == 'water_transporter')):
            return

        return cls.from_ice_conflict_factory(step, unit, factory, water_threshold=125)

    @classmethod
    @profileit
    def from_ice_conflict_factory(cls, step, unit, factory, water_threshold=130):
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)

        # Wait until water dips below 130.
        if factory.water[i] >= water_threshold:
            return

        # Only 2 at a time
        existing_water_transporters = [u for u in factory.units(step)
                                       if isinstance(u.role, RoleWaterTransporter)]
        existing_targets = [u.role.target_factory for u in existing_water_transporters]
        max_count = 2 if C.AFTER_DEADLINE else 1
        if len(existing_water_transporters) >= max_count:
            return

        best_factory, best_score = None, -C.UNREACHABLE
        for other_factory in board.player.factories():
            if (other_factory is factory
                or (other_factory.mode and other_factory.mode.NAME == 'ice_conflict')):
                continue
            dist = factory.cell().man_dist_factory(other_factory)
            water = other_factory.water[i]
            water_income = other_factory.get_water_income(step)
            score = 20 * water_income - dist
            if 2 * dist + 50 > factory.water[i]:
                score -= (2 * dist + 50 - factory.water[i]) * 3
            if 2 * dist > factory.water[i]:
                score -= 1000
            if other_factory in existing_targets:
                score -= 100
            if score > best_score:
                best_factory, best_score = other_factory, score
        if best_factory:
            return RoleWaterTransporter(step, unit, factory, best_factory)

    def is_valid(self, step):
        if not self.factory or not self.target_factory:
            return False

        if self.target_factory.mode and self.target_factory.mode.NAME == 'ice_conflict':
            return False

        # After returning to water-needy factory and dropping off water, check if still needed
        # TODO: If this role is used in other circumstances, this will need updating.
        i = step - self.unit.board.step
        if (self.goal is self.factory
            and self.unit.ice[i] == 0
            and self.unit.water[i] == 0
            and ((self.factory.mode and self.factory.mode.NAME != 'ice_conflict')
                 or self.factory.water[i] >= 130)):
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
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        unit_power = self.unit.power[i]

        power_threshold = 2 * board.naive_cost(
            step, self.unit, self.factory.cell(), self.target_factory.cell())
        power_threshold = min(power_threshold, 9 * self.unit.cfg.BATTERY_CAPACITY // 10)
        water_threshold = int(1.5 * self.factory.cell().man_dist_factory(self.target_factory))
        water_threshold = max(10, water_threshold)
        water_threshold = min(100, water_threshold)

        unit_water = self.unit.water[i] + self.unit.ice[i] // board.env_cfg.ICE_WATER_RATIO
        if self.goal is self.target_factory:
            # We need to have picked up the water and have enough power for the journey
            if unit_water >= water_threshold and self.unit.power[i] >= power_threshold:
                self.goal = self.factory
        elif self.goal is self.factory:
            if unit_water == 0 and self.unit.power[i] >= power_threshold:
                self.goal = self.target_factory
        else:
            assert False

    def do_move(self, step):
        return self._do_move(step)

    def do_dig(self, step):
        return

    def _do_pickup_water(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        cur_factory = cur_cell.factory()

        # Wait until on non-center factory cell
        unit_water = self.unit.water[i] + self.unit.ice[i] // board.env_cfg.ICE_WATER_RATIO
        water_threshold = int(1.5 * self.factory.cell().man_dist_factory(self.target_factory))
        water_threshold = max(10, water_threshold)
        water_threshold = min(100, water_threshold)
        if (not cur_factory
            or cur_factory is not self.target_factory
            or cur_cell.factory_center
            or unit_water >= water_threshold):
            return

        # Try to pickup the same amount as the dist travelled
        # Try ice first, then water
        amount = water_threshold - unit_water
        ice_amount = min(amount * board.env_cfg.ICE_WATER_RATIO, cur_factory.ice[i])
        if (C.AFTER_DEADLINE
            and 0 < ice_amount <= 100
            and ice_amount == amount * board.env_cfg.ICE_WATER_RATIO
            and self.unit.water[i] == 0):
            if self.unit.power[i] >= self.unit.pickup_cost(step, Resource.ICE, ice_amount):
                if i == 0:
                    log(f'WT {self.unit} picking up {ice_amount} ice')
                return self.unit.do_pickup(step, Resource.ICE, ice_amount)

        water_amount = min(amount, cur_factory.water[i] - 30)
        if water_amount > 0:
            if self.unit.power[i] >= self.unit.pickup_cost(step, Resource.WATER, water_amount):
                return self.unit.do_pickup(step, Resource.WATER, water_amount)
        else:
            return self.unit.do_no_move(step)

    def do_pickup(self, step):
        board = self.unit.board
        i = step - board.step

        do_pickup_water = self._do_pickup_water(step)
        if do_pickup_water:
            return do_pickup_water

        # Get power from target factory if that is still our goal
        alternate_factory = self.target_factory if self.goal is self.target_factory else None
        return self._do_power_pickup(step, alternate_factory=alternate_factory)

    def do_transfer(self, step):
        return self._do_transfer_resource_to_factory(step)

    def get_factory(self):
        return self.factory
