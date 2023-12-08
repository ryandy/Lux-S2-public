import sys

from .mode_forge import ModeForge
from .role_antagonizer import RoleAntagonizer
from .role_attacker import RoleAttacker
from .role_blockade import RoleBlockade
from .role_cow import RoleCow
from .role_generator import RoleGenerator
from .role_miner import RoleMiner
from .role_pillager import RolePillager
from .role_protector import RoleProtector
from .role_recharge import RoleRecharge
from .role_relocate import RoleRelocate
from .role_sidekick import RoleSidekick
from .role_transporter import RoleTransporter
from .role_water_transporter import RoleWaterTransporter
from .util import C, Action, Direction, FactoryAction, Resource, log, prandom


class EntityGroup:
    def __init__(self, step, step_idx, entities):
        self.step = step
        self.step_idx = step_idx
        self.entities = entities

    def __getattr__(self, attr):
        if attr not in self.__dict__:
            def func(*args, **kwargs):
                count = 0
                for e in self.entities:
                    if e.last_action_step < self.step:
                        e.action = getattr(e, attr)(self.step, *args, **kwargs)
                        if e.action is not None:
                            e.last_action_step = self.step
                            count += 1
                return count
            return func
        return super().__getattr__(attr)

    def finalize(self):
        for e in self.entities:
            if e.action is None:
                e.action = [Action.MOVE, Direction.CENTER, 0, 0, 0, 1]
            elif e.action == [Action.MOVE, Direction.CENTER, 0, 0, 0, 1]:
                # Check to see if any no-moves can become small life-saving power transfers
                if e.role:
                    action_spec = (e.role._do_idle_transfer_power_to_low_power_unit(self.step)
                                   or e.role._do_idle_dig_repair(self.step)
                                   or e.role._do_idle_dig_repair(self.step))
                    if action_spec:
                        e.action = action_spec

            if e.type == 'FACTORY':
                if self.step_idx == 0 and e.action != FactoryAction.NONE:
                    e.new_action = e.action
            else:
                e.new_action_queue[self.step_idx] = e.action

class Entity:
    def __init__(self):
        self.action = None
        self.last_action_step = -1

        self.assigned_unit_id = [None] + [None] * C.FUTURE_LEN
        self._lie_step = None

    def set_lie_step(self, step):
        if self._lie_step is None:
            self._lie_step = step

    def set_assignment(self, step, unit):
        i = step - self.board.step
        #log(f'{step} {unit.id} {self.x},{self.y}')
        if self.assigned_unit_id[i] is not None:
            log(f'Warning: multiple units with same assignment')
            log(f'{unit.board.to_string(step)}')
            assert False
        self.assigned_unit_id[i] = unit.id

    def unset_assignment(self, step, unit):
        i = step - self.board.step
        #log(f'{step} {self.assigned_unit_id[i]} {self.x},{self.y} (UNSET)')
        if self.assigned_unit_id[i] is None:
            log(f'Warning: unsetting nonexistant assignment')
            assert False
        assert self.assigned_unit_id[i] == unit.id
        self.assigned_unit_id[i] = None

    def assigned_unit(self, step):
        i = step - self.board.step
        return self.board.unit(self.assigned_unit_id[i])

    def do_forge_build(self, step, heavy=None):
        i = step - self.board.step
        if (self.type != 'FACTORY'
            or not isinstance(self.mode, ModeForge)):
            return
        return self.mode.do_build(step)

    def do_factory_build(self, step, heavy=None):
        i = step - self.board.step
        if self.type != 'FACTORY':
            return
        return self.mode.do_build(step)

    def do_factory_water(self, step):
        i = step - self.board.step
        if self.type != 'FACTORY':
            return
        return self.mode.do_water(step)

    def do_factory_end_phase_water(self, step):
        i = step - self.board.step
        if (self.type != 'FACTORY'
            or step < C.END_PHASE):
            return
        return self.mode.do_water(step)

    def do_factory_none(self, step):
        if self.type != 'FACTORY':
            return
        return FactoryAction.NONE

    def do_no_move(self, step, heavy=None):
        i = step - self.board.step
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))):
            return

        move_cost, move_direction, threatening_units = self.move_cost(step, self.cell(step))
        if self.power[i] >= move_cost:
            return self.do_move(
                step, move_direction, move_cost=move_cost, threatening_units=threatening_units)
        else:
            # Sometimes a no-move costs non-zero (AQ cost or avoiding collision)
            # Just insert this as a no-op
            return self.do_move(step, Direction.CENTER, force_no_move=True)

    def do_blockade_move(self, step, heavy=None, primary=None, engaged=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleBlockade)
            or not primary == self.role.is_primary()
            or not engaged == self.role.is_engaged(step)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        return self.role.do_move(step)

    def do_blockade_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleBlockade)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_blockade_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleBlockade)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_miner_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleMiner)):
            #or (self._lie_step is not None and step >= self._lie_step)):  # Need to lock in move
            return
        return self.role.do_move(step)

    def do_miner_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleMiner)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_miner_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleMiner)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_miner_dig(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleMiner)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_dig(step)

    def do_protected_miner_dig(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleMiner)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here

        i = step - self.board.step
        if self.protectors[i]:
            protector_id = self.protectors[i][0]
            protector = self.board.units[protector_id]
            if (i == 0
                and self.role.goal is self.role.resource_cell
                and self.cell(step) is self.role.resource_cell
                and protector.role.is_protecting(step)
                and not protector.role._should_strike(step)):
                return self.role.do_dig(step)

    def do_protected_miner_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleMiner)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here

        i = step - self.board.step
        if self.protectors[i]:
            protector_id = self.protectors[i][0]
            protector = self.board.units[protector_id]
            if (i == 0
                and self.role.goal is self.role.factory
                and self.cell(step).man_dist_factory(self.role.factory) <= 1
                and protector.role.is_protecting(step)
                and not protector.role._should_strike(step)):
                return self.role.do_transfer(step)
            if (self.role.goal is self.role.factory
                and self.cell(step).factory() is self.role.factory):
                return self.role.do_transfer(step)

    def do_protected_miner_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleMiner)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here

        i = step - self.board.step
        if self.protectors[i]:
            return self.role.do_pickup(step)

    def do_protected_miner_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleMiner)
            or (self._lie_step is not None and step >= self._lie_step)):
            return

        i = step - self.board.step
        if self.protectors[i]:
            protector_id = self.protectors[i][0]
            protector = self.board.units[protector_id]
            if (i == 0
                and self.role.goal is self.role.resource_cell
                and self.cell(step).man_dist(self.role.resource_cell) == 1
                and protector.role.is_protecting(step)
                and not protector.role._should_strike(step)):
                log(f'{self} protected miner move1 (go to resource cell)')
                return self.role._do_move(step, goal_cell=self.role.resource_cell)
            if (i == 0
                and self.role.goal is self.role.factory
                and self.cell(step).man_dist(protector.role.factory_cell) == 1):
                log(f'{self} protected miner move2 (go to factory)')
                return self.role._do_move(step, goal_cell=protector.role.factory_cell)

    def do_antagonizer_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleAntagonizer)):
            return
        return self.role.do_move(step)

    def do_antagonizer_dig(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleAntagonizer)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_dig(step)

    def do_antagonizer_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleAntagonizer)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_antagonizer_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleAntagonizer)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_attacker_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleAttacker)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        return self.role.do_move(step)

    def do_attacker_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleAttacker)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_attacker_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleAttacker)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_sidekick_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleSidekick)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        return self.role.do_move(step)

    def do_cow_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleCow)):
            return
        return self.role.do_move(step)

    def do_cow_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleCow)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_cow_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleCow)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_cow_dig(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleCow)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_dig(step)

    def do_pillager_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RolePillager)):
            return
        return self.role.do_move(step)

    def do_pillager_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RolePillager)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_pillager_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RolePillager)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_pillager_dig(self, step, heavy=None):
        i = step - self.board.step
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RolePillager)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            if ((step < C.END_PHASE and self.type == 'LIGHT' and not self.role.one_way)
                or (self.type == 'HEAVY' and (step < 980 or self.power[i] >= 210))):
                return
        return self.role.do_dig(step)

    def do_generator_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleGenerator)):
            return
        return self.role.do_move(step)

    def do_generator_dig(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleGenerator)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_dig(step)

    def do_generator_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleGenerator)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_transporter_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleTransporter)):
            return
        return self.role.do_move(step)

    def do_transporter_pickup_for_ice(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleTransporter)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.role.destination.role.resource_cell.ice:
            return self.role.do_pickup(step)

    def do_transporter_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleTransporter)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_transporter_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleTransporter)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_protector_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleProtector)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        return self.role.do_move(step)

    def do_protector_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleProtector)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_protector_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleProtector)
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_recharge_move(self, step, heavy=None, at_factory=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or ((at_factory is not None) and at_factory != bool(self.cell(step).factory()))
            or not isinstance(self.role, RoleRecharge)):
            return
        return self.role.do_move(step)

    def do_recharge_transfer(self, step, heavy=None, at_factory=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or ((at_factory is not None) and at_factory != bool(self.cell(step).factory()))
            or not isinstance(self.role, RoleRecharge)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_relocate_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleRelocate)):
            return
        return self.role.do_move(step)

    def do_relocate_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleRelocate)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_relocate_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleRelocate)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_water_transporter_move(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleWaterTransporter)):
            return
        return self.role.do_move(step)

    def do_water_transporter_move_emergency(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleWaterTransporter)):
            return

        i = step - self.board.step
        cur_cell = self.cell(step)
        dist = cur_cell.man_dist_factory(self.role.factory)
        if (self.water[i] > 0
            and self.role.factory.water[i] <= dist < self.role.factory.water[i] + 5):
            return self.role.do_move(step)

    def do_water_transporter_pickup(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleWaterTransporter)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        return self.role.do_pickup(step)

    def do_water_transporter_transfer(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or not isinstance(self.role, RoleWaterTransporter)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return
        return self.role.do_transfer(step)

    def do_move_step998(self, step, heavy=None):
        if (step != 998
            or self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or (self._lie_step is not None and step >= self._lie_step)):
            return

        board = self.board
        i = step - board.step
        cur_cell = self.cell(step)

        # If standing on lichen and not enough power to move, destruct now
        destruct_cost = self.cfg.SELF_DESTRUCT_COST if self.type == 'LIGHT' else self.cfg.DIG_COST
        if destruct_cost <= self.power[i] < self.cfg.MOVE_COST + destruct_cost:
            if i == 0:
                log(f'{self} {cur_cell} move998 A')
            return self.do_dig_step999(step, force=True) or self.do_move_step999(step, force=True)

        # If not enough power to destruct, try to initiate a lichen-destroying collision
        if self.power[i] < destruct_cost:
            if i == 0:
                log(f'{self} {cur_cell} move998 B')
            return self.do_move_step999(step, force=True)

        # Find best adjacent cell to try to destroy
        best_cell, best_score = None, -C.UNREACHABLE
        for move_cell in [cur_cell] + cur_cell.neighbors():
            move_direction = cur_cell.neighbor_to_direction(move_cell)
            if (move_cell.lichen[i] > 0
                and move_cell.lichen_strain[i] in board.opp.strains
                and self.power[i] >= self.move_cost(step, move_direction)[0] + destruct_cost):
                score = move_cell.lichen[i]
                if score > best_score:
                    best_cell, best_score = move_cell, score
        if best_cell:
            if best_cell is cur_cell:
                if i == 0:
                    log(f'{self} {cur_cell} move998 C {best_score}')
                return self.do_dig_step999(step, force=True)
            move_direction = cur_cell.neighbor_to_direction(best_cell)
            move_cost, _, threatening_units = self.move_cost(step, move_direction)
            if self.power[i] >= move_cost + destruct_cost:
                if i == 0:
                    log(f'{self} {cur_cell} move998 D {best_score}')
                return self.do_move(
                    step, move_direction, move_cost=move_cost, threatening_units=threatening_units)

    def do_move_step999(self, step, heavy=None, force=False):
        if ((step != 999 and not force)
            or self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or (self._lie_step is not None and step >= self._lie_step)):
            return

        board = self.board
        i = step - board.step
        cur_cell = self.cell(step)

        # TODO lights with dig<=power<destruct may want to not move

        # If not enough power to destruct, find the move that most likely results in a lichen collision
        destruct_cost = self.cfg.SELF_DESTRUCT_COST if self.type == 'LIGHT' else self.cfg.DIG_COST
        if ((self.cfg.MOVE_COST <= self.power[i] < destruct_cost)
            or (self.cfg.MOVE_COST <= self.power[i]
                and (cur_cell.lichen[i] == 0 or cur_cell.lichen_strain[i] in board.player.strains))):
            # Find best collision scenario
            best_cell, best_score = None, 0
            for move_cell in [cur_cell] + cur_cell.neighbors():
                # TODO if lichen is 0 but is a boundary cell
                if move_cell.lichen[i] > 0 and move_cell.lichen_strain[i] in board.opp.strains:
                    if move_cell.unit(step + 1):
                        score = 1234
                    else:
                        score, _ = self.threatened_by_opp(step, move_cell, all_collisions=True)
                    if move_cell.unit(step) and move_cell.unit(step).player_id == board.opp.id:
                        score += 1
                    if score > 0:
                        score += 0.001 * move_cell.lichen[i]
                    if score > best_score:
                        best_cell, best_score = move_cell, score
            if best_cell:
                move_direction = cur_cell.neighbor_to_direction(best_cell)
                move_cost, _, threatening_units = self.move_cost(step, move_direction)
                if self.power[i] >= move_cost:
                    if i == 0:
                        log(f'{self} {cur_cell} move999 A {best_score}')
                    return self.do_move(
                        step, move_direction, move_cost=move_cost, threatening_units=threatening_units)

        if cur_cell.lichen[i] == 0:
            #if i == 0:
            #    log(f'{self} {cur_cell} move999 B')
            move_direction = Direction.CENTER
            move_cost, _, threatening_units = self.move_cost(step, move_direction)
            if self.power[i] >= move_cost:
                return self.do_move(
                    step, move_direction, move_cost=move_cost, threatening_units=threatening_units)

        if cur_cell.lichen_strain[i] in board.player.strains:
            # Identify lowest risk move
            best_cell, best_score = None, 0
            for move_cell in [cur_cell] + cur_cell.neighbors():
                move_direction = cur_cell.neighbor_to_direction(move_cell)
                if self.power[i] >= self.move_cost(step, move_direction)[0]:
                    # Want 0 lichen
                    # Want low lichen
                    # Want low collision risk
                    if (move_cell.lichen[i] == 0
                        or move_cell.lichen_strain[i] not in board.player.strains):
                        score = 1000
                    else:
                        risk = self.threatened_by_opp(step, move_cell, all_collisions=True)[0]
                        risk = max(1, risk)
                        score = 1 / (move_cell.lichen[i] * risk)
                    if score > best_score:
                        best_cell, best_score = move_cell, score
            if best_cell:
                move_direction = cur_cell.neighbor_to_direction(best_cell)
                move_cost, _, threatening_units = self.move_cost(step, move_direction)
                if self.power[i] >= move_cost:
                    if i == 0:
                        log(f'{self} {cur_cell} move999 C {best_score}')
                    return self.do_move(
                        step, move_direction, move_cost=move_cost, threatening_units=threatening_units)

    def do_dig_step999(self, step, heavy=None, force=False):
        if ((step != 999 and not force)
            or self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or (self._lie_step is not None and step >= self._lie_step)):
            return

        board = self.board
        i = step - board.step
        cur_cell = self.cell(step)
        if cur_cell.lichen[i] > 0 and cur_cell.lichen_strain[i] in board.opp.strains:
            # Try to self-destruct. Do a dig as a backup-plan.
            if self.power[i] >= self.selfdestruct_cost(step):
                if i == 0:
                    log(f'{self} {cur_cell} dig999 A')
                return self.do_selfdestruct(step)
            if self.power[i] >= self.dig_cost(step):
                if i == 0:
                    log(f'{self} {cur_cell} dig999 B')
                return self.do_dig(step)

        # TODO do a move step998 if this cell is adjacent to unit?
        # Dig rubble if our lichen may be able to spread here.
        if (cur_cell.rubble[i] > 0
            and cur_cell.rubble[i] <= self.cfg.DIG_RUBBLE_REMOVED
            and any(((c.lichen[i] and c.lichen_strain[i] in board.player.strains)
                     or (c.factory() and c.factory().player_id == board.player.id))
                    for c in cur_cell.neighbors())):
            if self.power[i] >= self.dig_cost(step):
                if i == 0:
                    log(f'{self} {cur_cell} dig999 C')
                return self.do_dig(step)

    def do_pickup_resource_from_exploding_factory(self, step):
        if self.type == 'FACTORY':
            return

        board = self.board
        i = step - board.step
        cur_cell = self.cell(step)
        factory = cur_cell.factory()

        if (not factory
            or not factory.water[i] == 0
            or not factory.ice[i] < 4
            or cur_cell.unit(step + 1)):
            return  # Can't stand still if a different unit is moving here

        for cell in [factory.cell()] + factory.cells() + factory.neighbors():
            unit = cell.unit(step)
            if (unit
                and unit.player_id == board.player.id
                and (unit.water[i] >= 2
                     or unit.ice[i] >= 8)):
                return

        if factory.power[i] > 0:
            amount = self.cfg.BATTERY_CAPACITY - self.power[i]
            if amount > 0 and self.power[i] >= self.pickup_cost(step, Resource.POWER, amount):
                return self.do_pickup(step, Resource.POWER, amount)
        if factory.metal[i] > 0:
            amount = self.cfg.CARGO_SPACE - self.metal[i]
            if amount > 0 and self.power[i] >= self.pickup_cost(step, Resource.METAL, amount):
                return self.do_pickup(step, Resource.METAL, amount)
        if factory.ore[i] > 0:
            amount = self.cfg.CARGO_SPACE - self.ore[i]
            if amount > 0 and self.power[i] >= self.pickup_cost(step, Resource.ORE, amount):
                return self.do_pickup(step, Resource.ORE, amount)

    def do_no_move_dig_repair(self, step, heavy=None):
        if (self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or (self._lie_step is not None and step >= self._lie_step)):
            return
        if self.cell(step).unit(step + 1):
            return  # Can't stand still if a different unit is moving here
        if self.threatened_by_opp(step, self.cell(step))[0]:
            return

        board = self.board
        i = step - board.step
        cur_cell = self.cell(step)

        if (self.cfg.DIG_RUBBLE_REMOVED - 1 <= cur_cell.rubble[i] <= self.cfg.DIG_RUBBLE_REMOVED
            and any(((c.lichen[i] and c.lichen_strain[i] in board.player.strains)
                     or (c.factory() and c.factory().player_id == board.player.id))
                    for c in cur_cell.neighbors())):
            if self.power[i] >= self.dig_cost(step):
                if i == 0:
                    log(f'{self} no_move repair dig {cur_cell}')
                return self.do_dig(step)

    # TODO: pseudo-random 50%?
    #       track if opp is susceptible to this type of attack
    #       I assume eventually this will be unlikely to work and just distracts my units from roles
    def do_move_win_collision(self, step, heavy=None):
        if (step != self.board.step
            or self.type == 'FACTORY'
            or ((heavy is not None) and heavy != (self.type == 'HEAVY'))
            or (self._lie_step is not None and step >= self._lie_step)):
            return

        board = self.board
        i = step - board.step
        cur_cell = self.cell(step)

        # Only make this aggressive off-goal move if there's power to spare
        if self.power[i] < self.cfg.BATTERY_CAPACITY // 3:
            return

        # Some roles may want to skip this move
        if (self.role
            and (self.role.NAME == 'water_transporter'
                 or self.role.NAME == 'blockade'
                 or self.role.NAME == 'generator'
                 or (self.role.NAME == 'recharge' and not cur_cell.factory())
                 or self.role.NAME == 'relocate'
                 or self.role.NAME == 'attacker'
                 or self.role.NAME == 'sidekick'
                 or self.role.NAME == 'protector'
                 or self.role.NAME == 'antagonizer'  # Don't want to mess with a passive ant
                 or (self.role.NAME == 'miner' and self.type == 'HEAVY'))):
            return

        # Only do this ~25% of the time
        if not prandom(step + self.id, 0.25):
            return

        for move_cell in cur_cell.neighbors():
            if move_cell.factory():
                continue
            if move_cell.lichen[i] > 0 and move_cell.lichen_strain[i] in board.player.strains:
                continue
            for neighbor in move_cell.neighbors():
                opp_unit = neighbor.unit(step)
                # TODO: allow no AQ?
                if (not opp_unit
                    or opp_unit.player_id == board.player.id
                    or opp_unit.type != self.type
                    or not opp_unit.action_queue
                    or not opp_unit.action_queue[0]
                    or opp_unit.action_queue[0][0] != Action.MOVE
                    or opp_unit.action_queue[0][1] == Direction.CENTER):
                    continue

                direction = opp_unit.action_queue[0][1]
                opp_move_cell = neighbor.neighbor(*C.MOVE_DELTAS[direction])
                if opp_move_cell is move_cell:
                    # Ignore power gain and move cost, as these would be the same
                    my_power = self.power[i] - self.cfg.ACTION_QUEUE_POWER_COST
                    opp_power = opp_unit.power[i]
                    if my_power > opp_power:
                        if (move_cell.safe_to_move(step, self)
                            and not self.threatened_by_opp(step, move_cell)[0]):
                            move_cost, move_direction, threatening_units = self.move_cost(
                                step, move_cell)
                            if self.power[i] >= move_cost:
                                log(f'do_move_win_collision {self} -> {opp_unit} @ {move_cell}')
                                return self.do_move(
                                    step, move_direction,
                                    move_cost=move_cost, threatening_units=threatening_units)
