import sys

from .cell import Cell
from .role import Role
from .util import C, Resource, log, profileit


# Basically just a power_transporter (specifically to heavy miners)
class RoleTransporter(Role):
    '''Move power/resources from A to B'''
    NAME = 'transporter'

    # TODO: Currently we assume resource is power
    # TODO: Currently we assume source is a factory
    # TODO: Currently we assume destination is a RoleMiner unit
    # TODO: Currently we determine amount later
    # TODO: Rename Miner(?)PowerTransporter
    def __init__(self, step, unit, factory_cell, destination, goal=None):
        super().__init__(step, unit)
        self.factory_cell = factory_cell
        self.destination = destination
        self.goal = goal

        if self.goal is None:
            if not destination:
                # Temporarily in invalid state
                self.goal = factory_cell
            else:
                cur_cell = unit.cell(step)
                destination_cell = destination.cell(step)
                factory_dist = cur_cell.man_dist(factory_cell)
                destination_dist = cur_cell.man_dist(destination_cell)
                self.goal = destination if destination_dist < factory_dist else factory_cell

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory_cell else ''
        dgoal = '*' if self.goal is self.destination else ''
        return f'Transporter[{self.get_factory()}{fgoal} -> {self.destination}{dgoal}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory_cell),
                self.serialize_obj(self.destination),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory_cell = cls.deserialize_obj(unit.board, role_data[1])
        destination = cls.deserialize_obj(unit.board, role_data[2])
        goal = cls.deserialize_obj(unit.board, role_data[3])
        return RoleTransporter(step, unit, factory_cell, destination, goal=goal)

    @classmethod
    @profileit
    def from_transition_from_protector(cls, step, unit):
        board = unit.board
        i = step - board.step

        if (i != 0
            or not unit.type == 'HEAVY'
            or not unit.role
            or not unit.role.NAME == 'protector'):
            return

        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)
        miner_unit = unit.role.miner_unit
        if (miner_unit
            and miner_unit.role
            and miner_unit.role.NAME == 'miner'
            and not unit.role._get_threat_units(miner_unit, history_len=15)):
            factory_cell = unit.role.factory_cell
            cls._handle_displaced_unit(step, factory_cell)
            return RoleTransporter(step, unit, factory_cell, miner_unit)

    @classmethod
    @profileit
    def from_new_unit(cls, step, unit, max_dist=1):
        board = unit.board
        i = step - board.step

        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)

        # Check heavy miners assigned to this factory
        miner_units = [u for u in factory.units(step)
                       if (u.role
                           and u.role.NAME == 'miner'
                           and u.type == 'HEAVY')]
        for miner_unit in miner_units:
            if miner_unit.transporters[i]:
                continue

            # Don't transport too far, unless they are the only heavy miner
            destination_cell = miner_unit.role.resource_cell
            if len(miner_units) > 1 and destination_cell.man_dist_factory(factory) > max_dist:
                continue

            max_dist = destination_cell.man_dist_factory(factory)
            best_factory_cell, min_man_dist = None, C.UNREACHABLE
            for factory_cell in factory.cells():
                if not factory_cell.assigned_unit(step):
                    man_dist = factory_cell.man_dist(destination_cell)
                    if man_dist <= max_dist and man_dist < min_man_dist:
                        best_factory_cell, min_man_dist = factory_cell, man_dist
            if best_factory_cell:
                return RoleTransporter(step, unit, best_factory_cell, miner_unit)

    def is_valid(self, step):
        return (self.get_factory()
                and self.destination
                and self.destination.role
                and self.destination.role.NAME == 'miner')

    def set_role(self, step):
        super().set_role(step)
        self.get_factory().set_unit(step, self.unit)
        self.destination.set_transporter(step, self.unit)
        self.factory_cell.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            # TODO are these iterations necessary?
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            for unit in self.unit.board.player.units():
                unit.unset_transporter(step, self.unit)
            self.factory_cell.unset_assignment(step, self.unit)

    def goal_cell(self, step):
        i = step - self.unit.board.step

        # Temporarily override goal cell if on factory center
        if self.unit.cell(step) is self.get_factory().cell():
            return self.destination.role.resource_cell

        if self.goal is self.factory_cell:
            return self.goal

        # Goal is a unit
        assert self.goal is self.destination
        cur_cell = self.unit.cell(step)

        # If we're next to the assigned unit's resource cell, just stay put.
        if cur_cell.man_dist(self.destination.role.resource_cell) == 1:
            return cur_cell

        nearest_cell, min_man_dist = cur_cell, C.UNREACHABLE
        for cell in self.destination.role.resource_cell.neighbors():
            if cell.assigned_unit(step) and cell.assigned_unit(step).id != self.unit.id:
                continue
            if cell.factory() and cell.factory().player_id != self.unit.player_id:
                continue
            man_dist = self.factory_cell.man_dist(cell)
            man_dist -= 0.001 * cell.rubble[i]  # tie break w/ rubble
            if cell is self.factory_cell:  # Make sure unit doesn't leave factory unnecessarily
                man_dist -= 3
            if man_dist < min_man_dist:
                nearest_cell, min_man_dist = cell, man_dist
        if i == 0 and nearest_cell is cur_cell:
            log(f'Warning: transporter{self.unit.id} cannot find a cell near {self.destination}')
        return nearest_cell

    def update_goal(self, step):
        assert self.goal
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        unit_power = self.unit.power[i]
        unit_resource = max(self.unit.ice[i], self.unit.ore[i])

        if self.goal is self.destination:
            # TODO: improve e.g. factor in daylight power gen, rubble
            #power_baseline = 5 * self.unit.cfg.MOVE_COST 
            #power_threshold = (power_baseline
            #                   + self.unit.cfg.ACTION_QUEUE_POWER_COST
            #                   + self.unit.cfg.MOVE_COST * cur_cell.man_dist_factory(self.factory))
            #power_threshold = self.destination.cfg.DIG_COST // 2
            #power_threshold = 10 * self.unit.cfg.MOVE_COST
            power_threshold = (self.unit.cfg.ACTION_QUEUE_POWER_COST
                               + self.destination.cfg.DIG_COST // 2
                               + (2 * self.unit.cfg.MOVE_COST
                                  * cur_cell.man_dist_factory(self.get_factory())))
            if self.unit.type == 'HEAVY':
                power_threshold += self.destination.cfg.DIG_COST
            resource_threshold = 4 * self.unit.cfg.CARGO_SPACE // 5
            if (unit_power < power_threshold
                or unit_resource >= resource_threshold):
                self.goal = self.factory_cell
        elif self.goal is self.factory_cell:
            # TODO add movement cost (if any)
            if (unit_power >= (2 * self.destination.cfg.DIG_COST
                               - self.destination.power_gain(step+1)
                               - self.destination.power_gain(step+2))
                and unit_resource == 0):
                self.goal = self.destination
        else:
            assert False

    def do_move(self, step):
        return self._do_move(step)

    def do_dig(self, step):
        return

    def do_pickup(self, step):
        i = step - self.unit.board.step
        delivery_dist = self.destination.role.resource_cell.man_dist_factory(self.get_factory())
        if delivery_dist == 1:
            # Only pickup if dist-1 destination unit is running somewhat low
            digs_remaining = self.destination.power[i] // self.destination.cfg.DIG_COST
            power_gain = self.destination.power_gain(step, end_step=step+digs_remaining)
            digs_remaining = (self.destination.power[i] + power_gain) // self.destination.cfg.DIG_COST
            # Note: it could take a couple turns to pick up power necessary
            if digs_remaining >= 8:  # Next turn: at least 7 digs remaining
                return
        return self._do_power_pickup(step, max_amount=4*self.destination.cfg.DIG_COST)

    def _do_excess_power_transfer(self, step):
        i = step - self.unit.board.step
        cur_cell = self.unit.cell(step)
        # Heavy
        # dist1
        # at factory cell
        # power over 1500
        # factory power under 500
        if (self.unit.type == 'HEAVY'
            and cur_cell is self.factory_cell
            and self.unit.power[i] >= 1500
            and self.get_factory().power[i] < 500
            and self.get_factory().mode
            and self.get_factory().mode.NAME != 'ice_conflict'
            and self.destination.role.resource_cell.man_dist(self.factory_cell) == 1
            and (self.destination.cell(step) is not self.destination.role.resource_cell
                 or self.destination.power[i] >= 100)):
            amount = self.unit.power[i] - 700
            amount = (amount // 10) * 10
            if amount > 0:
                if (self.unit.power[i]
                    >= self.unit.transfer_cost(step, cur_cell, Resource.POWER, amount)):
                    if i == 0:
                        log(f'{self.unit} transporter transfer excess power')
                    return self.unit.do_transfer(step, cur_cell, Resource.POWER, amount)

    def do_transfer(self, step):
        i = step - self.unit.board.step
        cur_cell = self.unit.cell(step)
        goal_cell = self.goal_cell(step)

        excess_power_transfer = self._do_excess_power_transfer(step)
        if excess_power_transfer:
            return excess_power_transfer

        # Transfer power to unit
        if (self.goal is self.destination
            and self.unit.power[i] > 8 * self.unit.cfg.MOVE_COST):
            if self.destination.x[i+1] is None:
                log(f'step{step} {self.unit} {self} {cur_cell} transferring to '
                    f'{self.destination} {self.destination.role} {self.destination._lie_step}')
            assert self.destination.x[i+1] is not None  # Should only be transporting after moves
            transfer_cell = self.destination.cell(step+1)
            man_dist = cur_cell.man_dist(transfer_cell)
            if man_dist == 1 and not transfer_cell.factory():
                amount = (self.destination.cfg.BATTERY_CAPACITY
                          - self.destination.power[i]
                          - self.destination.power_gain(step))
                #power_to_keep = 5 * self.unit.cfg.MOVE_COST
                dist = self.destination.role.resource_cell.man_dist_factory(self.get_factory())
                power_gain = sum(self.unit.power_gain(s) for s in range(step, step+dist))
                power_to_keep = (self.unit.cfg.ACTION_QUEUE_POWER_COST
                                 + 2 * self.unit.cfg.MOVE_COST * dist
                                 - power_gain)
                amount = min(amount, self.unit.power[i] - power_to_keep)
                if amount > 0:
                    if (self.unit.power[i]
                        >= self.unit.transfer_cost(step, transfer_cell, Resource.POWER, amount)):
                        return self.unit.do_transfer(step, transfer_cell, Resource.POWER, amount)

        return self._do_transfer_resource_to_factory(step)

    def get_factory(self):
        return self.factory_cell.factory()
