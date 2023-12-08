import sys

from .cell import Cell
from .role import Role
from .util import C, Action, Direction, Resource, log, prandom, profileit


class RoleProtector(Role):
    '''Protect heavy miners while they dig'''
    NAME = 'protector'

    def __init__(self, step, unit, miner_unit, factory_cell, last_strike=-C.UNREACHABLE, goal=None):
        super().__init__(step, unit)
        self.miner_unit = miner_unit
        self.factory_cell = factory_cell
        self.last_strike = last_strike
        self.goal = goal or factory_cell.factory()

        # Not persisted:
        self.should_strike_ret = None
        self.threat_count = 0

    def __repr__(self):
        fgoal = '*' if self.goal is self.get_factory() else ''
        mgoal = '*' if self.goal is self.factory_cell else ''
        return f'Protector[{self.get_factory()}{fgoal} -> {self.miner_unit}{mgoal}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.miner_unit),
                self.serialize_obj(self.factory_cell),
                self.serialize_obj(self.last_strike),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        miner_unit = cls.deserialize_obj(unit.board, role_data[1])
        factory_cell = cls.deserialize_obj(unit.board, role_data[2])
        last_strike = cls.deserialize_obj(unit.board, role_data[3])
        goal = cls.deserialize_obj(unit.board, role_data[4])
        return RoleProtector(step, unit, miner_unit, factory_cell, last_strike=last_strike, goal=goal)

    @classmethod
    @profileit
    def from_transition_from_transporter(cls, step, unit):
        board = unit.board
        i = step - board.step

        if (not i == 0
            or not unit.type == 'HEAVY'
            or not unit.role
            or not unit.role.NAME == 'transporter'):
            return

        # Transition when opp threat is dist2 away
        # that way the protector can pick power up this turn and be ready to threaten next
        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)
        miner_unit = unit.role.destination
        if (miner_unit.role.resource_cell.ice
            and miner_unit.role.resource_cell.man_dist_factory(factory) == 1
            and miner_unit.cell(step).man_dist(miner_unit.role.resource_cell) < 2
            and cls._get_threat_units(miner_unit, history_len=1, max_radius=2)):
            miner_unit.unset_role_for_transporters(step)
            factory_cell = miner_unit.role.resource_cell.neighbor_toward(factory.cell())
            cls._handle_displaced_unit(step, factory_cell)
            return RoleProtector(step, unit, miner_unit, factory_cell)

    @classmethod
    @profileit
    def from_transition_protect_ice_miner(cls, step, unit):
        board = unit.board
        i = step - board.step

        if (i != 0
            or unit.type == 'LIGHT'):
            return

        if (unit.role
            and ((unit.role.NAME == 'recharge' and not unit.cell(step).factory())
                 or (unit.role.NAME == 'cow' and unit.role.repair)
                 or (unit.role.NAME == 'attacker' and unit.role.low_power_target)
                 or (unit.role.NAME == 'antagonizer'
                     and unit.role.can_destroy_factory(step))
                 or (unit.role.NAME == 'miner'
                     and unit.role.resource_cell.ice
                     and not unit.is_antagonized(step))
                 or unit.role.NAME == 'protector')):
            return

        factory = unit.assigned_factory or unit.cell(step).nearest_factory(board)
        for miner_unit in factory.units(step):
            if (miner_unit is unit
                or miner_unit.protectors[i]
                or not miner_unit.type == 'HEAVY'
                or not miner_unit.role
                or not miner_unit.role.NAME == 'miner'
                or not miner_unit.role.resource_cell.ice
                or not miner_unit.role.resource_cell.man_dist_factory(factory) == 1
                or not miner_unit.cell(step).man_dist(miner_unit.role.resource_cell) < 2
                or not miner_unit.is_antagonized(step)):
                continue
            miner_unit.unset_role_for_transporters(step)
            factory_cell = miner_unit.role.resource_cell.neighbor_toward(factory.cell())
            cls._handle_displaced_unit(step, factory_cell)
            return RoleProtector(step, unit, miner_unit, factory_cell)

    def is_valid(self, step):
        return (self.get_factory()
                and self.miner_unit
                and self.miner_unit.role
                and self.miner_unit.role.NAME == 'miner'
                and self.miner_unit.role.resource_cell.man_dist(self.factory_cell) == 1
                # TODO remove below when re-adding transporter transition
                and self._get_threat_units(self.miner_unit, history_len=10))

    def set_role(self, step):
        super().set_role(step)
        self.get_factory().set_unit(step, self.unit)
        self.miner_unit.set_protector(step, self.unit)
        self.factory_cell.set_assignment(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)
            for unit in self.unit.board.player.units():
                unit.unset_protector(step, self.unit)
            self.factory_cell.unset_assignment(step, self.unit)

    def goal_cell(self, step):
        if self.goal is self.factory_cell:
            return self.goal

        # Goal is a factory
        return self.goal.cell()

    def update_goal(self, step):
        assert self.goal
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        unit_power = self.unit.power[i]

        threat_units = self._get_threat_units(self.miner_unit, max_radius=3)
        threat_power = max(u.power[0] for u in threat_units) if threat_units else 0

        if self.goal is self.factory_cell:
            if unit_power <= threat_power:
                self.goal = self.get_factory()
        elif self.goal is self.get_factory():
            if ((unit_power > threat_power)
                or (unit_power == threat_power == 3000)):
                self.goal = self.factory_cell
        else:
            assert False

    def in_position(self, step):
        # We consider the units to be in position even if the miner is off by 1. This is so that they
        # can perform a protected move to get back to the resource cell.
        return (self.unit.cell(step) is self.factory_cell
                and self.miner_unit.cell(step).man_dist(self.miner_unit.role.resource_cell) <= 1)

    def is_protecting(self, step):
        board = self.unit.board
        i = step - board.step
        assert i == 0

        if self.goal is not self.factory_cell:
            return False

        if not self.in_position(step):
            return False

        if (self.miner_unit.power[i]
            < (self.miner_unit.cfg.DIG_COST + self.miner_unit.cfg.ACTION_QUEUE_POWER_COST)):
            return False

        threat_units = self._get_threat_units(self.miner_unit, history_len=1, max_radius=1)
        threat_power = max(u.power[0] for u in threat_units) if threat_units else 0
        return self.unit.power[0] - self.unit.cfg.ACTION_QUEUE_POWER_COST > threat_power

    def threat_exists_now(self, step):
        return self._get_threat_units(self.miner_unit, history_len=1, max_radius=1)

    def _should_strike(self, step):
        board = self.unit.board
        i = step - board.step
        assert i == 0

        if self.should_strike_ret is not None:
            return self.should_strike_ret

        self.should_strike_ret = False
        threats = self.threat_exists_now(step)
        resource_cell = self.miner_unit.role.resource_cell
        if threats and self.is_protecting(step):
            # Possible that only threat is standing on resource cell and miner is off by 1
            # In this case we can just let the miner move onto the resource cell
            # This is 100% safe, not even a "protected move"
            strike_chance = 0.6 if C.FLG else 0.4
            if (len(threats) == 1
                and threats[0].cell(step) is resource_cell
                and self.miner_unit.cell(step).man_dist(resource_cell) == 1):
                pass
            elif self.last_strike <= step - 10 or prandom(step, strike_chance):
                self.last_strike = step
                self.should_strike_ret = True

        return self.should_strike_ret

    @classmethod
    def _get_threat_units(cls, miner_unit, history_len=3, max_radius=2):
        return super()._get_threat_units(
            miner_unit.role.resource_cell,
            history_len=history_len,
            max_radius=max_radius,
            heavy=True)

    def do_move(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        goal_cell = self.goal_cell(step)

        # Move to get into proper factory / factory_cell position if necessary
        need_to_move = ((cur_cell is not goal_cell)
                        or (goal_cell.factory_center
                            and cur_cell.factory() is not goal_cell.factory()))
        if need_to_move:
            move_cost, move_direction, threatening_units = self.unit.move_cost(step, goal_cell)
            if self.unit.power[i] >= move_cost:
                return self.unit.do_move(
                    step, move_direction, move_cost=move_cost, threatening_units=threatening_units)
            return

        # We are at factory_cell
        # For step idx0, either strike out at resource_cell, or sit tight and pickup/transfer
        # For all future steps, we will claim that we _plan_ on striking resource cell
        if ((i > 0 and self.in_position(step))
            or (i == 0 and self._should_strike(step))):

            # Enqueue two full threats, then lie with rest of AQ
            # Want constant threats for flg
            if (i > 0
                and self.threat_count >= 2
                and not C.FLG):
                #log(f'LIES! protector {self.unit} @ step{step}')
                self.unit.set_lie_step(step)
                return

            resource_cell = self.miner_unit.role.resource_cell
            move_cost, move_direction, threatening_units = self.unit.move_cost(step, resource_cell)
            if self.unit.power[i] >= move_cost:
                if i == 0:
                    log(f'{self.unit} protector strike')
                if i > 0:
                    self.threat_count += 1
                return self.unit.do_move(
                    step, move_direction, move_cost=move_cost, threatening_units=threatening_units)

        # If in position on idx0 and we are not striking, picking up, or transferring, stand still
        if i == 0 and self.in_position(step):
            return self.unit.do_no_move(step)

    def do_dig(self, step):
        return

    def do_pickup(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)

        if self.goal is self.get_factory():
            return self._do_power_pickup(step)

        # Only actually do pickup on step idx0
        if i > 0:
            return

        # Only pickup at factory or factory cell, depending on goal
        if not ((self.goal is self.factory_cell and cur_cell is self.factory_cell)
                or (self.goal is self.get_factory() and cur_cell.factory() is self.goal)):
            return

        # No pickups at idx0 if we should be striking
        if self._should_strike(step):
            return

        protector_power = self.unit.power[i]
        miner_power = self.miner_unit.power[i]
        threat_units = self._get_threat_units(self.miner_unit, max_radius=3)
        threat_power = max(u.power[0] for u in threat_units) if threat_units else 0

        max_miner_power = max(threat_power + 100,
                              20 * (self.miner_unit.cfg.DIG_COST
                                + self.miner_unit.cfg.ACTION_QUEUE_POWER_COST))
        max_miner_power = min(max_miner_power, self.miner_unit.cfg.BATTERY_CAPACITY)
        power_for_miner = max(0, max_miner_power - miner_power)
        power_for_protector = max(0, threat_power + 100 - protector_power)
        if power_for_protector == 0:
            protector_surplus = protector_power - (threat_power + 100)
            power_for_miner = max(0, power_for_miner - protector_surplus)
        power_needed = power_for_miner + power_for_protector

        # Round up to nearest 100
        power_needed = ((power_needed + 99) // 100) * 100

        if power_needed:
            return self._do_power_pickup(step, max_amount=power_needed)

    def do_transfer(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)

        if self.goal is self.get_factory():
            return self._do_transfer_resource_to_factory(step)

        # Only actually do transfer on step idx0
        if i > 0:
            return

        # Only transfer from assigned factory cell
        if cur_cell is not self.factory_cell:
            return

        # No transfers at idx0 if we should be striking
        if self._should_strike(step):
            return

        # Need miner unit to already have move figured out, and end up at resource cell
        if (self.miner_unit.x[i+1] is None
            or self.miner_unit.cell(step+1) is not self.miner_unit.role.resource_cell):
            return

        protector_power = self.unit.power[i]
        miner_power = self.miner_unit.power[i]
        threat_units = self._get_threat_units(self.miner_unit, max_radius=3)
        threat_power = max(u.power[0] for u in threat_units) if threat_units else 0

        # Verify miner unit needs power in the near future.
        digs_remaining = (miner_power
                          // (self.miner_unit.cfg.DIG_COST
                              + self.miner_unit.cfg.ACTION_QUEUE_POWER_COST))
        power_gain = self.miner_unit.power_gain(step, end_step=step+digs_remaining)
        digs_remaining = ((miner_power + power_gain)
                          // (self.miner_unit.cfg.DIG_COST
                              + self.miner_unit.cfg.ACTION_QUEUE_POWER_COST))
        if digs_remaining > 10:  # TODO: using 20 elsewhere, is that ok?
            return

        # Verify protector has enough power to give away some while maintaining advantage.
        power_to_keep = threat_power + 100
        if protector_power <= power_to_keep:
            return

        amount = (self.miner_unit.cfg.BATTERY_CAPACITY
                  - miner_power
                  - self.miner_unit.power_gain(step))
        amount = min(amount, protector_power - power_to_keep)

        # Miners don't need that much power.. call it 20 digs or so
        max_miner_power = max(threat_power + 100,
                              20 * (self.miner_unit.cfg.DIG_COST
                                    + self.miner_unit.cfg.ACTION_QUEUE_POWER_COST))
        max_miner_power = min(max_miner_power, self.miner_unit.cfg.BATTERY_CAPACITY)
        max_amount = max_miner_power - miner_power
        amount = min(amount, max_amount)

        # Round down - we don't want tiny transfers to prevent us from picking up power when necessary.
        amount = (amount // 100) * 100

        if amount > 0:
            transfer_cell = self.miner_unit.role.resource_cell
            if (self.unit.power[i]
                >= self.unit.transfer_cost(step, transfer_cell, Resource.POWER, amount)):
                return self.unit.do_transfer(step, transfer_cell, Resource.POWER, amount)

    def get_factory(self):
        return self.factory_cell.factory()
