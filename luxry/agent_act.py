import sys
import time

from .board import Board
from .entity import EntityGroup
from .util import C, Action, log, log_init


def agent_act(agent, board_step, obs, remainingOverageTime):
    start_time = time.time()
    log_init(board_step, int(agent.player[-1]))
    board = Board.from_obs(obs, agent.player, board_step, agent.env_cfg, agent.strategy)
    board_summary = board.summary(board_step)

    if board_step == 0:
        log(f'Now: {C.NOW}')
        log(f'Deadline: {C.DEADLINE}')
        log(f'After deadline? {C.AFTER_DEADLINE}')

    if not C.PROD and board_step == 0:
        log(f'{board.to_string(board_step)}')

    # Check to see if this is a validation match
    if C.PROD and board_step == 2 and not C.AFTER_DEADLINE:
        validate_count = 0
        for unit in board.opp.units():
            if Action.validate_unit(unit):
                validate_count += 1
        if validate_count == agent.factories_per_team:
            agent.is_validation_match = True
            log(f'VALIDATION')
    if agent.is_validation_match:
        return {}

    if board_step == 2:
        if board.opp_is_tigga(board_step):
            C.TIGGA = True
            log(f'C.T')
        if board.opp_is_siesta(board_step):
            C.SIESTA = True
            log(f'C.S')
        if board.opp_is_harm(board_step):
            C.HARM = True
            log(f'C.H')
        if board.opp_is_flg(board_step):
            C.FLG = True
            log(f'C.F')
        if board.opp_is_philipp(board_step):
            C.PHILIPP = True
            log(f'C.P')

    if board_step == 12 and C.PHILIPP:
        C.PHILIPP = board.opp_is_philipp(board_step)
        if C.PHILIPP:
            log(f'C.P')
        else:
            log(f'NOT C.P')

    # Determine allowed time for this invocation
    if remainingOverageTime >= 10:
        time_per_invocation = 0.99 * C.TIME_PER_INVOCATION
        if board_step >= C.END_PHASE:
            time_per_invocation += ((remainingOverageTime - 15) / (1000 - board_step))
    else:
        time_per_invocation = 0.8 * C.TIME_PER_INVOCATION

    # Main simulation loop
    for step_idx in range(C.FUTURE_LEN):
        step = board.step + step_idx

        # Update for step
        # Want updated lichen_connected_cells here so we have lichen->power info for updating roles
        board.begin_step_simulation(step, agent.strategy)
        board.update_roles_and_goals(step)
        group = EntityGroup(step, step_idx, board.player.units() + board.player.factories())

        # Special cases
        group.do_move_step998()
        group.do_dig_step999()
        group.do_move_step999()
        group.do_pickup_resource_from_exploding_factory()
        group.do_move_win_collision()

        # Heavies
        group.do_protected_miner_transfer(heavy=True)  # Do before protector tries to transfer
        group.do_protected_miner_dig(heavy=True)
        group.do_protected_miner_pickup(heavy=True)
        group.do_protected_miner_move(heavy=True)

        group.do_protector_transfer(heavy=True)
        group.do_protector_pickup(heavy=True)
        group.do_protector_move(heavy=True)

        group.do_miner_transfer(heavy=True)
        # TODO: allow transports to move here, then check if miner wants to transfer resources to it
        group.do_miner_dig(heavy=True)
        group.do_miner_pickup(heavy=True)
        group.do_miner_move(heavy=True)

        group.do_transporter_transfer(heavy=True)
        group.do_transporter_pickup(heavy=True)
        group.do_transporter_move(heavy=True)

        group.do_recharge_transfer(heavy=True, at_factory=False)
        group.do_recharge_move(heavy=True, at_factory=False)

        # ~LIGHT~ blockades and water_transporters
        group.do_blockade_transfer(heavy=False)
        group.do_blockade_pickup(heavy=False)
        group.do_water_transporter_transfer(heavy=False)
        group.do_water_transporter_pickup(heavy=False)
        # 1. water transporter emergency
        # 2. blockade engaged
        # 4. blockade unengaged
        # 3. water transporter
        group.do_water_transporter_move_emergency(heavy=False)
        group.do_blockade_move(heavy=False, primary=True, engaged=True)
        group.do_blockade_move(heavy=False, primary=False, engaged=True)
        group.do_blockade_move(heavy=False, primary=True, engaged=False)
        group.do_blockade_move(heavy=False, primary=False, engaged=False)
        group.do_water_transporter_move(heavy=False)

        group.do_recharge_transfer(heavy=True, at_factory=True)
        group.do_recharge_move(heavy=True, at_factory=True)

        # Take precedence over light transporters to ensure we have power to pump out new light
        # units (many of which will relocate) while we are still technically a forge.
        # Otherwise we may accidentally save up metal until after the forge invalidates then we
        # end up building more than we can support locally.
        group.do_forge_build()

        # ~LIGHT~ transporters
        group.do_transporter_transfer(heavy=False)
        group.do_transporter_pickup_for_ice(heavy=False)
        group.do_transporter_pickup(heavy=False)
        group.do_transporter_move(heavy=False)  # Check for miner->transporter transfers after move?

        group.do_attacker_transfer(heavy=True)
        group.do_attacker_pickup(heavy=True)
        group.do_attacker_move(heavy=True)
        group.do_sidekick_move(heavy=True)

        group.do_relocate_transfer(heavy=True)
        group.do_relocate_pickup(heavy=True)
        group.do_relocate_move(heavy=True)

        group.do_pillager_dig(heavy=True)
        group.do_pillager_transfer(heavy=True)
        group.do_pillager_pickup(heavy=True)
        group.do_pillager_move(heavy=True)

        group.do_antagonizer_transfer(heavy=True)
        group.do_antagonizer_pickup(heavy=True)
        group.do_antagonizer_dig(heavy=True)  # Rare
        group.do_antagonizer_move(heavy=True)

        group.do_cow_dig(heavy=True)
        group.do_cow_transfer(heavy=True)
        group.do_cow_pickup(heavy=True)
        group.do_cow_move(heavy=True)

        group.do_generator_dig(heavy=True)  # Rare
        group.do_generator_transfer(heavy=True)
        group.do_generator_move(heavy=True)

        #group.do_no_move_dig_repair(heavy=True)
        group.do_no_move(heavy=True)

        # Factory build
        group.do_factory_end_phase_water()
        group.do_factory_build()

        # Lights
        group.do_miner_transfer(heavy=False)
        group.do_miner_dig(heavy=False)
        group.do_miner_pickup(heavy=False)
        group.do_miner_move(heavy=False)

        group.do_relocate_transfer(heavy=False)
        group.do_relocate_pickup(heavy=False)
        group.do_relocate_move(heavy=False)

        group.do_attacker_transfer(heavy=False)
        group.do_attacker_pickup(heavy=False)
        group.do_attacker_move(heavy=False)
        group.do_sidekick_move(heavy=False)

        group.do_recharge_transfer(heavy=False)
        group.do_recharge_move(heavy=False)

        group.do_pillager_dig(heavy=False)
        group.do_pillager_transfer(heavy=False)
        group.do_pillager_pickup(heavy=False)
        group.do_pillager_move(heavy=False)

        group.do_antagonizer_transfer(heavy=False)
        group.do_antagonizer_pickup(heavy=False)
        group.do_antagonizer_move(heavy=False)

        group.do_cow_dig(heavy=False)
        group.do_cow_transfer(heavy=False)
        group.do_cow_pickup(heavy=False)
        group.do_cow_move(heavy=False)

        #group.do_no_move_dig_repair(heavy=False)
        group.do_no_move(heavy=False)

        # Want updated lichen_growth_cells here so we have the watering price (affected by digging)
        # Factory water
        group.do_factory_water()
        group.do_factory_none()

        # Want updated lichen_connected_cells here so we can calculate lichen->power for next step
        group.finalize()
        board.end_step_simulation(step, agent.strategy)

        # Exit early if not enough time to finish another loop.
        elapsed_time = time.time() - start_time
        time_per_step = elapsed_time / (step_idx + 1)
        if elapsed_time + time_per_step > time_per_invocation:
            break
        if step >= 999:
            break

    actions = board.get_new_actions(verbose=(not C.PROD))
    elapsed_time = time.time() - start_time
    if C.PROD or board_step % 20 == 0:
        log(f'sim{step_idx+1}, '
            f'{board_summary}, '
            f'{round(elapsed_time, 2)}s {round(remainingOverageTime, 2)}o')
    if C.TESTING and board_step == 999:
        print(f'sim{step_idx+1}, '
              f'{agent.factories_per_team}{agent.place_first} {board_summary}, '
              f'{round(elapsed_time, 2)}s {round(remainingOverageTime, 2)}o', file=sys.stderr)
        print('\n'*7, file=sys.stderr)

    return actions
