import sys

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


def role_from_serial(step, unit, role_data):
    if role_data[0] == RoleAntagonizer.NAME:
        return RoleAntagonizer.from_serial(step, unit, role_data)
    if role_data[0] == RoleAttacker.NAME:
        return RoleAttacker.from_serial(step, unit, role_data)
    if role_data[0] == RoleBlockade.NAME:
        return RoleBlockade.from_serial(step, unit, role_data)
    if role_data[0] == RoleCow.NAME:
        return RoleCow.from_serial(step, unit, role_data)
    if role_data[0] == RoleGenerator.NAME:
        return RoleGenerator.from_serial(step, unit, role_data)
    if role_data[0] == RoleMiner.NAME:
        return RoleMiner.from_serial(step, unit, role_data)
    if role_data[0] == RolePillager.NAME:
        return RolePillager.from_serial(step, unit, role_data)
    if role_data[0] == RoleProtector.NAME:
        return RoleProtector.from_serial(step, unit, role_data)
    if role_data[0] == RoleRecharge.NAME:
        return RoleRecharge.from_serial(step, unit, role_data)
    if role_data[0] == RoleRelocate.NAME:
        return RoleRelocate.from_serial(step, unit, role_data)
    if role_data[0] == RoleSidekick.NAME:
        return RoleSidekick.from_serial(step, unit, role_data)
    if role_data[0] == RoleTransporter.NAME:
        return RoleTransporter.from_serial(step, unit, role_data)
    if role_data[0] == RoleWaterTransporter.NAME:
        return RoleWaterTransporter.from_serial(step, unit, role_data)
    assert False
