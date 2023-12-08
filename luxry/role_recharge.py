import math
import sys

from .cell import Cell
from .role import Role
from .role_miner import RoleMiner
from .util import C, Resource, log, profileit


class RoleRecharge(Role):
    '''Return to factory to recharge and be re-assigned'''
    NAME = 'recharge'

    def __init__(self, step, unit, factory):
        super().__init__(step, unit)
        self.factory = factory
        self.goal = factory

    def __repr__(self):
        return f'Recharge[{self.factory}*]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        return RoleRecharge(step, unit, factory)
 
    @classmethod
    @profileit
    def from_transition_low_power_unit(cls, step, unit):
        if not unit.low_power:
            return

        board = unit.board
        i = step - board.step
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        # Was RoleRecharge, and made it to the factory
        if unit.role is None and cur_cell.factory():
            return

        # TODO: should we ignore some/all attackers/sidekicks?
        if (unit.role
            and (unit.role.NAME == 'recharge'
                 or unit.role.NAME == 'protector'
                 or unit.role.NAME == 'transporter'
                 or unit.role.NAME == 'water_transporter'
                 or unit.role.NAME == 'relocate'
                 or unit.role.NAME == 'blockade'
                 or (unit.role.NAME == 'attacker'
                     and unit.role.low_power_target
                     and unit.role.target_unit.water[i] >= 5)
                 or unit.role.NAME == 'generator'
                 or (unit.role.NAME == 'miner'
                     and unit.role.resource_cell.man_dist_factory(factory) <= RoleMiner.FORGE_DIST)
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'pillager'
                     and (step >= C.END_PHASE
                          or unit.role.one_way)))):
            return

        return RoleRecharge(step, unit, factory)

    @classmethod
    @profileit
    def from_transition_low_water_factory(cls, step, unit):
        i = step - unit.board.step
        if unit.ice[i] + unit.water[i] == 0:
            return

        board = unit.board
        cur_cell = unit.cell(step)
        factory = unit.assigned_factory or cur_cell.nearest_factory(board)

        if (unit.role
            and (unit.role.NAME == 'recharge'
                 or unit.role.NAME == 'water_transporter'
                 or (unit.role.NAME == 'miner'
                     and unit.role.resource_cell.man_dist_factory(factory) <= RoleMiner.FORGE_DIST)
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'pillager' and step >= C.END_PHASE
                     and cur_cell.man_dist_factory(factory) > 10))):
            return

        # Same check as in RoleMiner::update_goal
        if (factory.water[i]
            + factory.ice[i] // board.env_cfg.ICE_WATER_RATIO
            < 10 + cur_cell.man_dist_factory(factory)):
            return RoleRecharge(step, unit, factory)

    def is_valid(self, step):
        i = step - self.unit.board.step
        return (self.factory
                and (self.unit.cell(step).factory() is not self.factory
                     or (self.unit.ice[i] + self.unit.ore[i]
                         + self.unit.water[i] + self.unit.metal[i] > 0)))

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)

    def goal_cell(self, step):
        cur_cell = self.unit.cell(step)

        # Temporarily override goal cell if on factory center
        if (step == 1
            and self.unit.type == 'HEAVY'
            and cur_cell is self.get_factory().cell()
            and self.get_factory().mode
            and self.get_factory().mode.NAME == 'ice_conflict'):
            return cur_cell.neighbor_toward(self.get_factory().mode.opp_factory.cell())

        # Temporarily override goal cell if on factory center
        # Just move toward the center of the board (make sure not to pathfind onto this/opp factory)
        # Lights will naturally get "pushed" off by newly created units so no worry there
        if self.unit.type == 'HEAVY' and cur_cell is self.get_factory().cell():
            mid_cell = self.unit.board.cell(24, 24)
            for cell, _ in mid_cell.radius_cells(max_radius=self.unit.board.size):
                if not cell.factory():
                    return cell

        # Goal is a factory
        return self.goal.cell()

    def update_goal(self, step):
        return

    def do_move(self, step):
        return self._do_move(step)

    def do_dig(self, step):
        return

    def do_pickup(self, step):
        return

    def _do_ice_vuln_power_transfer(self, step):
        # Early exit if we are not a light assigned to an ice conflict factory
        if (self.unit.type != 'LIGHT'
            or not self.factory.mode
            or not self.factory.mode.NAME in ('ice_conflict', 'forge')):
            return

        # Early exit if we are not factory-bound and at factory
        cur_cell = self.unit.cell(step)
        if self.goal is not self.factory or cur_cell.factory() is not self.factory:
            return

        board = self.unit.board
        i = step - board.step

        # Transfer excess power to factory, rounding down to nearest 10
        amount = self.unit.power[i] - 10
        amount = (amount // 10) * 10
        if amount > 0:
            if (self.unit.power[i]
                >= self.unit.transfer_cost(step, cur_cell, Resource.POWER, amount)):
                return self.unit.do_transfer(step, cur_cell, Resource.POWER, amount)

    def do_transfer(self, step):
        return (self._do_transfer_resource_to_factory(step)
                or self._do_ice_vuln_power_transfer(step))

    def get_factory(self):
        return self.factory
