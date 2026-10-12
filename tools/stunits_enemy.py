"""Unit layer, part of tools/stunits.py: The enemy carrier's close-range combat (states 10 $1B2D2 and 11 $1B49E, via the enemy_tactical/enemy_engaged hooks) and supply transfers by the supply drone ($1F316, object 16; supply_drone_destroyed).

The mixin is combined into stunits.UnitState ahead of stunits.UnitCore, so methods defined
here replace the core's stubs and hooks. Use only `import stsim` / `import stunits` at module
level (stunits imports this module while it is still loading; read its constants at run time).

For tools/check_units.py, this module may define:
  ROUTINES  {"name": (original address, "method name")}: checked on every capture
  SCENARIOS [function(mem, rnd)]: perturbations run pass by pass against the original
  CHECKS    [function(files) -> bool]: any other checks (e.g. commands called with registers)
"""
import stsim  # noqa: F401
import stunits  # noqa: F401


class EnemyMixin:
    pass


ROUTINES = {}
SCENARIOS = []
CHECKS = []
