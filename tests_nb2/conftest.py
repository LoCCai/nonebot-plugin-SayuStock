"""Test fixtures for the NoneBot2 branch (no external market calls)."""

from __future__ import annotations

import os
import sys
import tempfile
from types import ModuleType
from pathlib import Path

os.environ.setdefault("SAYUSTOCK_DATA_DIR", tempfile.mkdtemp(prefix="sayustock-tests-"))
os.environ.setdefault("SAYUSTOCK_LOG_LEVEL", "CRITICAL")

import pytest  # noqa: E402

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))


def _ensure_pkg_shell(name: str, path: Path) -> None:
    """Register the outer repo directory without executing its __init__.py."""
    existing = sys.modules.get(name)
    if existing is not None and str(path) in {str(item) for item in getattr(existing, "__path__", [])}:
        return
    module = ModuleType(name)
    module.__path__ = [str(path)]  # type: ignore[attr-defined]
    module.__package__ = name
    module.__file__ = str(path / "__init__.py")
    sys.modules[name] = module


_ensure_pkg_shell("SayuStock", _PLUGIN_ROOT / "SayuStock")


def _init_nonebot() -> None:
    import nonebot

    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init(driver="~fastapi")


# Must run before test modules import the plugin (matcher registration needs a driver).
_init_nonebot()


@pytest.fixture(scope="session", autouse=True)
def _nonebot_session():
    yield


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(os.environ["SAYUSTOCK_DATA_DIR"])


@pytest.fixture
def fake_market():
    from tests_nb2.fake_market import FakeMarket
    from SayuStock.utils.market import set_market

    market = FakeMarket()
    set_market(market)
    yield market
    set_market(None)
