"""Unit layer, part of tools/stunits.py: Mantas and Walruses out of the hangar: flight ($1CDA2), driving ($1DCFC), the Manta lift ($1EAA0) and dock sequences, loadouts ($1ED46), firing ($1F9E8, $1FB6E), destruction ($19188, $191F2), and the hangar commands (launch, prepare, refuel, replace).

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


class VehicleMixin:
    pass


ROUTINES = {}
SCENARIOS = []
CHECKS = []
