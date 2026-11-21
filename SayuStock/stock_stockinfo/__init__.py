"""Quote and comparison data services."""

from importlib.util import find_spec

if find_spec("gsuid_core.sv") is not None:
    from . import _gscore_commands  # noqa: F401
