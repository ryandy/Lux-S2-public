import sys

from .cell import Cell
from .role import Role
from .util import C, log, prandom_shuffle


class RoleSidekick(Role):
    '''Help attacker pursue an opp unit'''
    NAME = 'sidekick'

    def __init__(self, step, unit, factory, attacker_unit, target_unit, goal=None):
        super().__init__(step, unit)
        self.factory = factory
        self.attacker_unit = attacker_unit
        self.target_unit = target_unit
        self.goal = goal or target_unit

    def __repr__(self):
        fgoal = '*' if self.goal is self.factory else ''
        tgoal = '*' if self.goal is self.target_unit else ''
        return f'Sidekick[{self.factory}{fgoal} -> {self.target_unit}{tgoal} w/ {self.attacker_unit}]'

    def serialize(self):
        return (self.NAME,
                self.serialize_obj(self.factory),
                self.serialize_obj(self.attacker_unit),
                self.serialize_obj(self.target_unit),
                self.serialize_obj(self.goal),
                )

    @classmethod
    def from_serial(cls, step, unit, role_data):
        _ = step
        factory = cls.deserialize_obj(unit.board, role_data[1])
        attacker_unit = cls.deserialize_obj(unit.board, role_data[2])
        target_unit = cls.deserialize_obj(unit.board, role_data[3])
        goal = cls.deserialize_obj(unit.board, role_data[4])
        return RoleSidekick(step, unit, factory, attacker_unit, target_unit, goal=goal)

    @classmethod
    def in_position(cls, step, sidekick_unit, attacker_unit, target_unit):
        board = sidekick_unit.board
        i = step - board.step
        if i > 0:
            return False

        # TODO: Should verify that both units have enough power to at least force opp unit into
        #       low_power without also hitting low_power.

        # Verify sidekick unit has more power than target. Don't chase units with too much power.
        # Attacker does not need more power because it only has to deny the no-move each step.
        if (target_unit.power[0] >= target_unit.cfg.BATTERY_CAPACITY // 2
            or target_unit.power[0] >= attacker_unit.power[0]
            or target_unit.power[0] >= sidekick_unit.power[0] - 10):
            return False

        # Verify adjacencies.
        scell, acell, tcell = sidekick_unit.cell(step),attacker_unit.cell(step),target_unit.cell(step)
        if (tcell.man_dist_factory(tcell.nearest_factory(board, player_id=target_unit.player_id)) <= 1
            or scell.man_dist(acell) > 1
            or acell.man_dist(tcell) > 1):
            return False

        # Verify attacker is able to make necessary move.
        for neighbor in tcell.neighbors():
            opp_unit = neighbor.unit(step)
            if opp_unit and opp_unit.player_id == target_unit.player_id:
                if opp_unit.type == 'HEAVY' and attacker_unit.type == 'LIGHT':
                    return False
                if (opp_unit.type == attacker_unit.type
                    and opp_unit.power[0] > attacker_unit.power[0]):
                    return False

        # Verify sidekick is able to make a necessary move.
        a_move_available = False
        dx, dy = tcell.x - scell.x, tcell.y - scell.y
        dx, dy = (dx // abs(dx) if dx else 0), (dy // abs(dy) if dy else 0)
        for move_cell in [scell.neighbor(dx, 0), scell.neighbor(0, dy)]:
            if (move_cell is scell
                or (move_cell.factory() and move_cell.factory().player_id != sidekick_unit.player_id)):
                continue
            this_move_available = True
            for neighbor in move_cell.neighbors():
                opp_unit = neighbor.unit(step)
                if opp_unit and opp_unit.player_id == target_unit.player_id:
                    if opp_unit.type == 'HEAVY' and sidekick_unit.type == 'LIGHT':
                        this_move_available = False
                    elif (opp_unit.type == sidekick_unit.type
                          and opp_unit.power[0] >= sidekick_unit.power[0]):
                        this_move_available = False
            if this_move_available:
                a_move_available = True
                break
        if not a_move_available:
            return False

        return True

    def is_valid(self, step):
        # TODO also verify that target unit can be pushed away from opp factories?
        i = step - self.unit.board.step
        return (i > 0
                or (self.factory  # TODO: does factory really matter?
                    and self.target_unit
                    and self.attacker_unit
                    and self.attacker_unit.role
                    and self.attacker_unit.role.NAME == 'attacker'
                    and self.attacker_unit.role.sidekick_unit is self.unit
                    and self.in_position(step, self.unit, self.attacker_unit, self.target_unit)))

    def set_role(self, step):
        super().set_role(step)
        self.factory.set_unit(step, self.unit)

    def unset_role(self, step):
        if super().unset_role(step):
            for factory in self.unit.board.player.factories():
                factory.unset_unit(step, self.unit)

    # TODO: if target is along board edge, prefer moving parallel to that edge
    #       that will result in forcing into corner rather than giving target unit an out
    #       Even if opp risks it and "breaks out" they will still be stuck in sidekick configuration
    # TODO: specifically push away from nearest/last factory (rather than all)
    # TODO: maybe should return an int value rather than just yes/no
    #       further from factory lines is better
    def _is_good_push(self, step, dx, dy):
        if dx == 0 and dy == 0:
            return False
        assert not (dx and dy)
        opp_cell = self.target_unit.cell(step)
        for factory in self.unit.board.opp.factories():
            if (dx > 0 and factory.x + 1 >= opp_cell.x
                or dx < 0 and factory.x - 1 <= opp_cell.x
                or dy > 0 and factory.y + 1 >= opp_cell.y
                or dy < 0 and factory.y - 1 <= opp_cell.y):
                return False
        return True

    def goal_cell(self, step):
        scell = self.unit.cell(step)
        if step != self.unit.board.step:
            return scell

        acell, tcell = self.attacker_unit.cell(step), self.target_unit.cell(step)
        dx, dy = tcell.x - scell.x, tcell.y - scell.y
        dx, dy = (dx // abs(dx) if dx else 0), (dy // abs(dy) if dy else 0)

        move_cells = []
        for move_cell in [scell.neighbor(dx, 0), scell.neighbor(0, dy)]:
            if (move_cell is scell
                or (move_cell.factory() and move_cell.factory().player_id != self.unit.player_id)):
                continue
            move_is_safe = True
            for neighbor in move_cell.neighbors():
                opp_unit = neighbor.unit(step)
                if opp_unit and opp_unit.player_id == self.target_unit.player_id:
                    if ((opp_unit.type == 'HEAVY' and self.unit.type == 'LIGHT')
                        or (opp_unit.type == self.unit.type
                            and opp_unit.power[0] >= self.unit.power[0])):
                        move_is_safe = False
            if move_is_safe:
                move_cells.append(move_cell)

        assert move_cells  # Shouldn't happen due to is_valid check
        if not move_cells:
            log(f'SHOULD NOT HAPPEN {self.unit} {[scell.neighbor(dx, 0), scell.neighbor(0, dy)]}')
            log(f'{self.is_valid(step)}')
            return scell

        if len(move_cells) == 1:
            return move_cells[0]

        # Prefer moves that push opps away from factories if there are 2 safe options
        prandom_shuffle(step, move_cells)
        for move_cell in move_cells:
            # e.g. If I want to push in the x direction, move in the y direction
            move_dx, move_dy = move_cell.x - scell.x, move_cell.y - scell.y
            if ((move_dx and self._is_good_push(step, 0, dy))
                or (move_dy and self._is_good_push(step, dx, 0))):
                return move_cell

        return move_cells[0]

    def update_goal(self, step):
        return

    def do_move(self, step):
        return self._do_move(step)

    def do_dig(self, step):
        return

    def do_pickup(self, step):
        return

    def do_transfer(self, step):
        return

    def get_factory(self):
        return self.factory
