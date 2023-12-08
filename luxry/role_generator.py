import math
import sys

from .cell import Cell
from .role import Role
from .util import C, Resource, log, prandom


class RoleGenerator(Role):
    '''Return to factory to generator and be re-assigned'''
    NAME = 'generator'

    def __init__(self, step, unit, factory, station_cell, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.station_cell = station_cell
        self.goal = goal or factory

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        sgoal = '*' if self.goal is self.station_cell else ''
        return f'Generator[{self.factory}{fgoal} -> {self.station_cell}{sgoal}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.station_cell),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        station_cell = cls.deserialize_obj(unit.board, role_data[2])
        goal = cls.deserialize_obj(unit.board, role_data[3])
        return RoleGenerator(step, unit, factory, station_cell, goal=goal)

    @classmethod
    def from_post_forge_heavy(cls, step, unit, max_count=1):
        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        factory_metal = (factory.metal[i] + unit.metal[i]
                         + (factory.ore[i] + unit.ore[i]) // board.env_cfg.ORE_METAL_RATIO)
        if factory_metal < 100:
            #if i == 0 and unit.id == 8 and step == 84:
            #    log(f'INVALID A {factory.metal[i]} {factory.ore[i]} {factory_metal}')
            return

        generators = [u for u in factory.units(step)
                      if (u.type == 'HEAVY'
                          and u.role
                          and u.role.NAME in ('generator', 'transporter', 'protector'))]
        if 1 + len(generators) > max_count:
            #if i == 0 and unit.id == 8 and step == 84:
            #    log(f'INVALID B')
            return

        best_cell, best_score = None, -C.UNREACHABLE
        for cell, _ in factory.radius_cells(1):
            score = 100 - cell.rubble[i]
            if cell.ore or cell.ice or cell.assigned_unit(step):
                continue
            for neighbor in cell.neighbors():
                if neighbor.ice:
                    score -= 20
                if neighbor.ore:
                    score -= 10
            if score > best_score:
                best_cell, best_score = cell, score
        if best_cell:
            return RoleGenerator(step, unit, factory, best_cell)
        #if i == 0 and unit.id == 8 and step == 84:
        #    log(f'INVALID C')

    def is_valid(self, step):
        i = step - self.unit.board.step
        return (self.factory
                and self.factory.power[i] < 6000)

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)
        self.station_cell.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            self.station_cell.unset_assignment(step, self.unit)

    def goal_cell(self, step):
        cur_cell = self.unit.cell(step)

        # Temporarily override goal cell if on factory center
        if cur_cell is self.get_factory().cell():
            return self.station_cell

        if isinstance(self.goal, Cell):
            return self.goal

        # Goal is a factory
        return self.goal.cell()

    def _desired_power(self, step):
        desired_power = 100
        day_idx = step % 50
        if day_idx < 30:
            desired_power += 4 * day_idx
        else:
            desired_power += 120 - 6 * (day_idx - 30)
        return desired_power

    def update_goal(self, step):
        assert self.goal

        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        unit_power = self.unit.power[i]

        threat_nearby = False
        for neighbor in [self.station_cell] + self.station_cell.neighbors():
            neighbor_unit = neighbor.unit(board.step)
            if (neighbor_unit
                and neighbor_unit.type == 'HEAVY'
                and neighbor_unit.player_id == board.opp.id):
                threat_nearby = True

        if self.goal is self.station_cell:
            if (unit_power < 80
                or threat_nearby):
                self.goal = self.factory
        elif self.goal is self.factory:
            unit_resource = max(self.unit.ice[i], self.unit.ore[i])
            power_threshold = (
                self.unit.cfg.ACTION_QUEUE_POWER_COST
                + board.naive_cost(step, self.unit, cur_cell, self.station_cell)
                + self._desired_power(step)
                + 40)
            if (unit_resource == 0
                and unit_power >= power_threshold
                and not threat_nearby):
                self.goal = self.station_cell
        else:
            assert False

    def do_move(self, step):
        return self._do_move(step)

    def do_dig(self, step):
        i = step - self.unit.board.step
        cur_cell = self.unit.cell(step)

        if (self.goal is self.station_cell
            and cur_cell is self.station_cell
            and cur_cell.rubble[i] > 0
            and cur_cell.rubble[i] <= 20):
            if self.unit.power[i] >= self.unit.dig_cost(step):
                return self.unit.do_dig(step)

    def do_pickup(self, step):
        return

    def _do_excess_power_transfer(self, step):
        # Early exit if we are not at station
        cur_cell = self.unit.cell(step)
        if (self.goal is not self.station_cell
            or cur_cell is not self.station_cell):
            return

        board = self.unit.board
        i = step - board.step

        desired_power = self._desired_power(step)
        if self.unit.power[i] > desired_power + 100:
            amount = self.unit.power[i] - desired_power - 40
        elif self.unit.power[i] >= desired_power - 20:
            amount = 6
        else:
            return

        # Transfer excess power to factory
        if amount > 0:
            transfer_cell = cur_cell.neighbor_toward(self.factory.cell())
            if (self.unit.power[i]
                >= self.unit.transfer_cost(step, transfer_cell, Resource.POWER, amount)):
                return self.unit.do_transfer(step, transfer_cell, Resource.POWER, amount)

    def do_transfer(self, step):
        return (self._do_transfer_resource_to_factory(step)
                or self._do_excess_power_transfer(step))

    def get_factory(self):
        return self.factory
