import datetime
import random
import socket
import sys
import time


# Knobs and dials
class C:
    TESTING = False
    PROD = not socket.gethostname().startswith('Ryans')
    SILENT_PROD = False

    NOW = datetime.datetime.now(datetime.timezone.utc)
    F1 = not PROD or (NOW.day == -1 and NOW.hour == 0)
    F2 = not PROD

    # technically 49 minutes before the deadline
    DEADLINE = datetime.datetime(2023, 4, 24, 23, 10, tzinfo=datetime.timezone.utc)
    AFTER_DEADLINE = (not PROD) or (DEADLINE < NOW)

    TIME_PER_INVOCATION = 3.0
    FUTURE_LEN = (51 if AFTER_DEADLINE else 50) if PROD else 15

    END_PHASE =     880
    ICE_MINE_RUSH = 970
    LIGHT_LIM =      12

    # index 0 = center, 1 = up, 2 = right, 3 = down, 4 = left
    MOVE_DELTAS = [[0, 0], [0, -1], [1, 0], [0, 1], [-1, 0]]
    UNREACHABLE = 1000000 if PROD else 1000000000  # Exaggerate potential slowdowns while debugging

    TIGGA = False #not PROD
    SIESTA = False #not PROD
    HARM = False #not PROD
    FLG = False #not PROD
    PHILIPP = False #not PROD


if C.TESTING:
    C.PROD = True
    C.SILENT_PROD = True
    C.AFTER_DEADLINE = True
    C.FUTURE_LEN = 10


class Direction:
    MIN = 0
    CENTER = 0
    NORTH = 1
    EAST = 2
    SOUTH = 3
    WEST = 4
    MAX = 4


class FactoryAction:
    LIGHT = 0
    HEAVY = 1
    WATER = 2
    NONE = 3


class Action:
    MOVE = 0
    TRANSFER = 1
    PICKUP = 2
    DIG = 3
    SELF_DESTRUCT = 4
    RECHARGE = 5

    @staticmethod
    def equal(action_spec1, action_spec2):
        if action_spec1 is None and action_spec2 is None:
            return True
        if action_spec1 is None or action_spec2 is None:
            return False
        action1, direction1, resource1, amount1, repeat1, n1 = list(action_spec1)
        action2, direction2, resource2, amount2, repeat2, n2 = list(action_spec2)
        if action1 != action2:
            return False
        if action1 == Action.MOVE:
            return direction1 == direction2
        if action1 == Action.TRANSFER:
            return direction1 == direction2 and resource1 == resource2 and amount1 == amount2
        if action1 == Action.PICKUP:
            return resource1 == resource2 and amount1 == amount2
        if action1 == Action.DIG:
            return True
        if action1 == Action.RECHARGE:
            return amount1 == amount2
        if action1 == Action.SELF_DESTRUCT:
            return True
        assert False

    @staticmethod
    def expand_queue(unit, action_queue):
        action_queue = list(action_queue)
        new_action_queue = []
        idx = 0
        while idx < len(action_queue):
            action_spec = action_queue[idx]
            idx += 1
            if action_spec is None:
                break

            action_spec = list(action_spec)
            action, direction, resource, amount, repeat, n = action_spec
            # Override n in the case of recharge actions
            # This can be inaccurate because we do not track opp power usage over time
            # Will become more accurate as the recharge action moves to the front of the queue
            if action == Action.RECHARGE:
                n = max(n, unit.steps_until_power(amount))
            for j in range(n, 0, -1):
                new_action_spec = (action, direction, resource, amount, repeat, j)
                new_action_queue.append(new_action_spec)
                if len(new_action_queue) >= 20:
                    return new_action_queue
            if repeat:
                # last item in queue is repeating: simplify things by reducing the value to 1
                if idx == len(action_queue) - 1:
                    repeat = 1
                action_queue.append([action, direction, resource, amount, repeat, repeat])

        return new_action_queue


    @staticmethod
    def get_lie_action_queue(unit):
        board = unit.board
        step = unit._lie_step
        cur_cell = unit.cell(step)
        opp_factory = cur_cell.nearest_factory(board, player_id=board.opp.id)
        mid_cell = board.cell((cur_cell.x+3*opp_factory.x)//4, (cur_cell.y+3*opp_factory.y)//4)
        for resource_cell, _ in mid_cell.radius_cells(max_radius=30):
            if (not (resource_cell.ice or resource_cell.ore)
                or (resource_cell.assigned_unit(step)
                    and resource_cell.assigned_unit(step).type == 'HEAVY')
                or prandom(step, 0.5)):
                continue
            #log(f'  {resource_cell}!!')
            actions = []
            prev_cell = cur_cell
            naive_route = board.naive_cost(step, unit, cur_cell, resource_cell, ret_route=True)
            for cell in naive_route[1:]:
                direction = prev_cell.neighbor_to_direction(cell)
                actions.append([Action.MOVE, direction, 0, 0, 0, 1])
                prev_cell = cell
            actions.append([Action.DIG, 0, 0, 0, 0, 1])
            #log(f'  {actions}')
            return actions
        return [[Action.MOVE, Direction.CENTER, 0, 0, 0, 1]]


    @staticmethod
    def compress_queue(unit, action_queue, embed_sig=False):
        if unit._lie_step is not None:
            valid_len = unit._lie_step - unit.board.step
            assert valid_len >= 0
            action_queue = action_queue[:valid_len] + Action.get_lie_action_queue(unit)

        new_action_queue = []
        next_idx = 0
        for i in range(len(action_queue)):
            if i < next_idx:
                continue
            action_spec = action_queue[i]

            # Don't give the opp warning that we are picking up water
            if action_spec[0] == Action.PICKUP and action_spec[2] == Resource.WATER and i > 0:
                break

            # Switch up initial AQ
            if (unit.board.step + i == 2
                and action_spec[0] == Action.PICKUP and action_spec[2] == Resource.POWER
                and action_spec[3] == 550
                and C.AFTER_DEADLINE):
                action_spec[3] = 1234

            for j in range(i + 1, len(action_queue) + 1):
                if j == len(action_queue) or not Action.equal(action_spec, action_queue[j]):
                    break
            new_action_queue.append(action_spec)
            new_action_queue[-1][5] = j - i  # set n
            next_idx = j

        # Make sure we never send more than 20 individual action specs.
        new_action_queue = new_action_queue[:(18 if embed_sig else 20)]

        # Repeat the last 1 or 2 actions if there's a chance it'll make sense
        if len(new_action_queue) >= 1:
            action1, direction1, resource1, amount1, repeat1, n1 = new_action_queue[-1]
            if (action1 == Action.MOVE
                or action1 == Action.DIG
                or (action1 == Action.PICKUP and resource1 == Resource.POWER)
                or (action1 == Action.TRANSFER and resource1 == Resource.POWER)):
                if embed_sig:
                    new_action_queue[-1][5] = 100  # set n=100
                else:
                    new_action_queue[-1][4] = 1  # set repeat=1
            if embed_sig:
                feature_sig = Action.get_feature_sig()
                direction = prandom_choice(
                    unit.x[0] + 48 * unit.y[0] + unit.id,
                    [Direction.NORTH, Direction.EAST, Direction.WEST, Direction.NORTH])
                new_action_queue.append(
                    (Action.MOVE, direction, 0, 0, 0, feature_sig))  # set n=feature_sig
                validation_sig = Action.get_validation_sig(unit)
                new_action_queue.append(
                    (Action.MOVE, Direction.CENTER, 0, 0, 0, validation_sig))  # set n=validation_sig

        if not embed_sig and len(new_action_queue) >= 2 and action1 == Action.MOVE:
            action2, direction2, resource2, amount2, repeat2, n2 = new_action_queue[-2]
            if (action2 == Action.MOVE
                and n2 == 1
                and ((direction1 == Direction.WEST and direction2 == Direction.EAST)
                     or (direction1 == Direction.EAST and direction2 == Direction.WEST)
                     or (direction1 == Direction.NORTH and direction2 == Direction.SOUTH)
                     or (direction1 == Direction.SOUTH and direction2 == Direction.NORTH))):
                new_action_queue[-1][4] = 1  # set repeat=1
                new_action_queue[-2][4] = 1  # set repeat=1

        return new_action_queue

    @staticmethod
    def get_feature_sig():
        return 3 if C.F2 else (2 if C.F1 else 1)

    @staticmethod
    def get_validation_sig(unit):
        board = unit.board
        seed = (26182914379
                + 2433 * unit.id
                + 2989 * unit.player_id
                + 9273 * board.cells[854].rubble[0]
                + 7239 * board.cells[1001].rubble[0]
                + 1837 * board.cells[101].rubble[0]
                + 7183 * board.cells[1788].rubble[0])
        random.seed(seed)
        r = random.random()
        return int(50 + (10000-50) * r)

    @staticmethod
    def validate_unit(unit):
        if not unit._raw_action_queue:
            return False
        sig = Action.get_validation_sig(unit)
        return sig == unit._raw_action_queue[-1][5]


class Resource:
    MIN = 0
    ICE = 0
    ORE = 1
    WATER = 2
    METAL = 3
    POWER = 4
    MAX = 4


def profileit(func):
    if C.PROD:
        return func
    def wrap_func(*args, **kwargs):
        t1 = time.time()
        result = func(*args, **kwargs)
        t2 = time.time()
        elapsed_t = round(1000000 * (t2 - t1))
        if (elapsed_t > 3000
            and not ('flood_fill' in func.__qualname__
                     and 'get_all_ice_vulnerable' in str(args[2]))):
            log(f'Function {func.__qualname__!r}({args[1:]}, {kwargs}) -> {result}; t={elapsed_t}us')
        return result
    return wrap_func


def prandom(seed, percent_chance):
    random.seed(seed)
    r = random.random()
    return r < percent_chance


def prandom_shuffle(seed, l):
    random.seed(seed)
    random.shuffle(l)


def prandom_choice(seed, l):
    random.seed(seed)
    return random.choice(l)


def serialize_obj(obj):
    if isinstance(obj, int):
        return obj
    elif obj is None:
        return None
    return obj.serialize()


def deserialize_obj(board, data):
    if isinstance(data, int):
        return data
    if data is None:
        return None
    if isinstance(data, tuple):  # Cell/Unit/Factory encoded as tuple
        if data[0] == 'c':
            return board.cell(data[1], data[2])
        if data[0] == 'f':
            return board.factories[data[1]] if data[1] in board.factories else None
        if data[0] == 'u':
            return board.units[data[1]] if data[1] in board.units else None
        assert False
    assert False


_log_step = None
_log_player_id = None
def log_init(step, player_id):
    global _log_step
    global _log_player_id
    _log_step = step
    _log_player_id = player_id
    if step == 0:
        log('~~~ LOG INIT ~~~')

def log(s, debug=False):
    # We don't log for player1 during development.
    if not C.PROD and _log_player_id == 1:
        return
    # We don't log debug messages for prod.
    if C.PROD and debug:
        return

    if C.PROD and C.SILENT_PROD:
        pass
    elif C.PROD:
        prefix = f'{_log_step}:'
        print(prefix, s, file=sys.stderr)
    else:
        prefix = f'{_log_step}D: ' if debug else f'{_log_step}: '
        lines = s.split('\n')
        with open('log.txt', 'a') as outfile:
            outfile.write(f'{prefix}{lines[0]}\n')
            for line in lines[1:]:
                outfile.write(' ' * len(prefix) + f'{line}\n')
