"""Unit layer, part of tools/stunits.py: The carrier's own weapons: the laser turret on its screen ($1EDFC), surface-to-surface missiles ($13598), decoy flares, the recon drone and the defence-drone launch, as commands the UI would issue.

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


class CarrierMixin:
    pass


ROUTINES = {}
SCENARIOS = []
CHECKS = []
