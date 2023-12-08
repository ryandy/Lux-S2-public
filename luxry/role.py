import math
import sys

from .cell import Cell
from .util import C, Resource, log, serialize_obj, deserialize_obj


class Role:
    def __init__(self, step, unit):
        self.unit = unit
        self.goal = None # Cell, Unit, or Factory

        # Most recent step where set_role was called.
        self.set_role_step = None

        # Whenever we're creating a new role for a unit, we should make sure all transporters
        # assigned to this unit from a prior role are released.
        unit.unset_role_for_protectors(step)
        unit.unset_role_for_transporters(step)

    @staticmethod
    def serialize_obj(obj):
        return serialize_obj(obj)

    @staticmethod
    def deserialize_obj(board, data):
        return deserialize_obj(board, data)

    def get_factory(self):
        raise NotImplementedError()

    def goal_cell(self, step):
        raise NotImplementedError()

    def set_role(self, step):
        assert self.set_role_step is None or self.set_role_step < step
        self.set_role_step = step

    def unset_role(self, step):
        self.unit.unset_role_for_transporters(step)
        return self.set_role_step == step

    @classmethod
    def _dest_is_safe(cls, step, unit, cell):
        if unit.type == 'LIGHT':
            for neighbor in [cell] + cell.neighbors():
                for j in range(unit.board.step, unit.board.step - 2, -1):
                    uid = neighbor.unit_history[j]
                    if uid is None or uid not in unit.board.units:
                        continue
                    opp_unit = unit.board.units[uid]
                    if opp_unit.player_id != unit.board.opp.id:
                        continue
                    if opp_unit.type == 'HEAVY':
                        return False
        return True

    @classmethod
    def _get_threat_units(cls, cell, history_len=3, max_radius=2, heavy=False, light=False):
        board = cell.board

        opp_units = []
        for radius_cell, _ in cell.radius_cells(min_radius=0, max_radius=max_radius):
            for j in range(board.step, max(-1, board.step - history_len), -1):
                uid = radius_cell.unit_history[j]
                if uid in board.units:
                    unit = board.units[uid]
                    if not ((heavy and unit.type == 'HEAVY') or (light and unit.type == 'LIGHT')):
                        continue
                    if unit.player_id == board.player.id:
                        continue
                    opp_units.append(unit)
        return opp_units

    @classmethod
    def _handle_displaced_unit(cls, step, cell):
        displaced_unit = cell.assigned_unit(step)
        if displaced_unit:
            displaced_unit.unset_role(step)

    def _goal_is_factory(self):
        factory = self.get_factory()
        return (self.goal is factory
                or (isinstance(self.goal, Cell) and self.goal.factory_id == factory.id))

    def _do_move(self, step, goal_cell=None):
        i = step - self.unit.board.step
        cur_cell = self.unit.cell(step)
        goal_cell = goal_cell or self.goal_cell(step)

        if self.NAME == 'blockade' and self._force_direction and self._force_direction[0] == step:
            goal_cell = self._force_direction[1]
            if i == 0:
                log(f'{self.unit} force direction {self._force_direction[1]}')

        need_to_move = ((cur_cell is not goal_cell)
                        or (goal_cell.factory_center
                            and cur_cell.factory() is not goal_cell.factory()))
        if need_to_move:
            move_cost, move_direction, threatening_units = self.unit.move_cost(step, goal_cell)
            if self.unit.power[i] >= move_cost:
                return self.unit.do_move(
                    step, move_direction, move_cost=move_cost, threatening_units=threatening_units)

    def _do_transfer_resource_to_factory(self, step):
        i = step - self.unit.board.step

        # Early exit if we are not factory-bound
        if not self._goal_is_factory():
            return

        # Early exit if we don't have resources
        if self.unit.ice[i] + self.unit.ore[i] + self.unit.water[i] + self.unit.metal[i] == 0:
            return

        # Transfer resources to factory
        cur_cell = self.unit.cell(step)
        factory = self.get_factory()
        dist = cur_cell.man_dist_factory(factory)
        if dist <= 1:
            resource_transfer_cell = cur_cell.neighbor_toward(factory.cell())
            resources = [(Resource.ICE, self.unit.ice[i]),
                         (Resource.ORE, self.unit.ore[i]),
                         (Resource.WATER, self.unit.water[i]),
                         (Resource.METAL, self.unit.metal[i])]
            resources.sort(key=lambda x: x[1], reverse=True)
            # Round up to 1000: we want to transfer all, and that is the max
            resource, amount = resources[0]
            amount = 1234 if C.AFTER_DEADLINE else 1000
            if (self.unit.power[i]
                >= self.unit.transfer_cost(step, resource_transfer_cell, resource, amount)):
                return self.unit.do_transfer(step, resource_transfer_cell, resource, amount)

    # Neighbor unit must have already locked in move
    def _do_idle_transfer_power_to_low_power_unit(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        resource = Resource.POWER

        if (self.unit.low_power
            #or self.NAME == 'recharge'
            or self.NAME == 'water_transporter'
            or self.NAME == 'blockade'):
            return

        for neighbor in cur_cell.neighbors():
            # Cannot transfer to unit on factory cell
            if neighbor.factory():
                continue
            other_unit = neighbor.unit(step + 1)
            if not other_unit or other_unit.player_id != board.player.id:
                continue
            factory = other_unit.assigned_factory or neighbor.nearest_factory(board)
            factory_dist = neighbor.man_dist_factory(factory)

            if other_unit.power[i] < factory_dist * other_unit.cfg.MOVE_COST:
                amount = (factory_dist * other_unit.cfg.MOVE_COST
                          + math.floor(cur_cell.rubble[i] * other_unit.cfg.RUBBLE_MOVEMENT_COST)
                          - other_unit.power[i])
                power_threshold = min(self.unit.cfg.BATTERY_CAPACITY // 3 + amount,
                                      300 + amount)
                if amount <= 10 and self.unit.power[i] >= 150:
                    power_threshold = self.unit.power[i]
                if self.unit.power[i] >= power_threshold:
                    if self.unit.power[i] >= self.unit.transfer_cost(step, neighbor, resource, amount):
                        if i == 0:
                            log(f'{self.unit} give emergency power to {other_unit}')
                        return self.unit.do_transfer(step, neighbor, resource, amount)

    def _do_idle_dig_repair(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)

        if (self.unit.low_power
            #or self.NAME == 'recharge'
            or self.NAME == 'water_transporter'
            or self.NAME == 'blockade'):
            return

        if ((self.unit.cfg.DIG_RUBBLE_REMOVED - 1
             <= cur_cell.rubble[i]
             <= self.unit.cfg.DIG_RUBBLE_REMOVED)
            and any(((c.lichen[i] and c.lichen_strain[i] in board.player.strains)
                     or (c.factory() and c.factory().player_id == board.player.id))
                    for c in cur_cell.neighbors())):
            if self.unit.power[i] >= 6 * self.unit.cfg.MOVE_COST + self.unit.dig_cost(step):
                if i == 0:
                    log(f'{self.unit} do idle dig repair {cur_cell}')
                return self.unit.do_dig(step)

    def _do_idle_dig_attack(self, step):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)

        if (self.unit.low_power
            or self.NAME == 'recharge'
            or self.NAME == 'water_transporter'
            or self.NAME == 'blockade'):
            return

        if (cur_cell.lichen[i]
            #and cur_cell.lichen_strain[i] in board.opp.strains
            and cur_cell.lichen_strain[i] in board.factories
            and (cur_cell.lichen_bottleneck
                 or cur_cell.lichen[i] <= self.unit.cfg.DIG_LICHEN_REMOVED
                 or cur_cell in board.factories[cur_cell.lichen_strain[i]].lichen_frontier_cells)):
            if self.unit.power[i] >= 6 * self.unit.cfg.MOVE_COST + self.unit.dig_cost(step):
                if i == 0:
                    log(f'{self.unit} do idle dig attack {cur_cell}')
                return self.unit.do_dig(step)

    def _do_power_pickup(self, step, alternate_factory=None, max_amount=None):
        board = self.unit.board
        i = step - board.step
        cur_cell = self.unit.cell(step)
        cur_factory = cur_cell.factory()
        assigned_factory = alternate_factory or self.get_factory()

        # Wait until on non-center factory cell
        if (not cur_factory
            or (not self._goal_is_factory() and not alternate_factory)
            or cur_cell.factory_center):
            return

        # Can only stop at different factory if it has more power
        if (cur_factory is not assigned_factory
            #and cur_factory.power[i] < assigned_factory.power[i]
            ):
            return

        factory_power = cur_factory.power[i] - cur_factory.power_reserved(step)
        amount = (self.unit.cfg.BATTERY_CAPACITY
                  - self.unit.power[i]
                  - self.unit.power_gain(step))
        if max_amount is not None:
            amount = min(amount, max_amount)

        # This is not quite right. Probably better to not try this for now.
        # We know we'll spend AQ cost when the queue runs out and needs refreshing.
        # There are other instances where we could predict we'd need to pay AQ cost, but they're
        # a bit harder to check for (e.g. a pickup this turn deviates from queued plan)
        #if i == len(self.unit.action_queue):
        #    amount += self.unit.cfg.ACTION_QUEUE_POWER_COST

        desired_amount = amount
        amount = min(amount, factory_power)

        if amount > 0:
            if self.unit.power[i] >= self.unit.pickup_cost(step, Resource.POWER, amount):
                return self.unit.do_pickup(step, Resource.POWER, amount)
        if desired_amount > 0:
            # Wait for power if currently at correct factory.
            if cur_factory is assigned_factory:
                return self.unit.do_no_move(step)
