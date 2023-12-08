import sys

from .mode_default import ModeDefault
from .mode_forge import ModeForge
from .mode_ice_conflict import ModeIceConflict


def mode_from_serial(step, factory, mode_data):
    if mode_data[0] == ModeDefault.NAME:
        return ModeDefault.from_serial(step, factory, mode_data)
    if mode_data[0] == ModeForge.NAME:
        return ModeForge.from_serial(step, factory, mode_data)
    if mode_data[0] == ModeIceConflict.NAME:
        return ModeIceConflict.from_serial(step, factory, mode_data)
    assert False
