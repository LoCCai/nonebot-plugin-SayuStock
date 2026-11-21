"""Test fixtures for the NoneBot2 branch (no external market calls)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("SAYUSTOCK_DATA_DIR", tempfile.mkdtemp(prefix="sayustock-tests-"))
os.environ.setdefault("SAYUSTOCK_LOG_LEVEL", "CRITICAL")

import pytest  # noqa: E402


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
