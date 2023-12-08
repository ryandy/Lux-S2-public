import sys
import time
import traceback

from .agent_act import agent_act
from .agent_early_setup import agent_early_setup
from .strategy import Strategy
from .util import log


class Agent():
    def __init__(self, player, env_cfg):
        self.player = player
        self.env_cfg = env_cfg
        self.strategy = Strategy()

        self.step = None # set by agent_fn
        self.factories_per_team = None  # used by early_setup
        self.factories_left = None  # used by early_setup
        self.metal_left = None  # used by early_setup
        self.water_left = None  # used by early_setup
        self.place_first = None  # used by early_setup

        self.is_validation_match = False  # used by agent_act

        self.forge_spawn_cell_ids = None  # used by agent_early_setup
        self.top_spawn_position_is_forge = None  # used by agent_early_setup
        self.top_spawn_position_is_superforge = None  # used by agent_early_setup

        self.ice_vuln_factories = set()

        self._0score = {}

        self.start_time = time.time()

    def early_setup(self, step, obs, remainingOverageTime):
        try:
            return agent_early_setup(self, step, obs, remainingOverageTime)
        except Exception as e:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            lines = traceback.format_exc().splitlines()
            # Print traceback in reverse order so that the important lines do not get truncated.
            log('\n'.join(reversed(lines)))
            raise(e)

    def act(self, step, obs, remainingOverageTime):
        step -= (2 * self.factories_per_team + 1)  # engine (or main) bug?
        try:
            return agent_act(self, step, obs, remainingOverageTime)
        except Exception as e:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            lines = traceback.format_exc().splitlines()
            # Print traceback in reverse order so that the important lines do not get truncated.
            log('\n'.join(reversed(lines)))
            raise(e)
