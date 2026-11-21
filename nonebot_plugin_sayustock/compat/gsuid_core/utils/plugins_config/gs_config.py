"""JSON-backed ``StringConfig`` replacement used by SayuStock's config module."""

from __future__ import annotations

import json
from typing import Any
from pathlib import Path
from dataclasses import dataclass

from ...logger import logger

_FALLBACKS: dict[str, Any] = {
    "market_api_chain": ["东方财富", "腾讯财经", "新浪财经"],
    "market_api_chain_quote": ["东方财富", "腾讯财经", "新浪财经"],
    "market_api_chain_kline": ["东方财富", "腾讯财经", "新浪财经"],
    "market_api_chain_board": ["东方财富", "新浪财经"],
    "market_api_chain_market": ["东方财富", "新浪财经"],
    "market_api_chain_exclusive": ["东方财富"],
    "market_api_chain_ipo": ["东方财富"],
    "market_api_chain_etf_quote": ["腾讯财经", "新浪财经"],
    "mapcloud_viewport": 1600,
    "mapcloud_scale": 1,
    "mapcloud_refresh_minutes": 10,
    "stock_cache_retention_days": 7,
    "xueqiu_user_agent": "",
    "holdings_analysis_unlimited_users": [],
    "news_push_hourly_groups": [],
    "news_push_trading_session_groups": [],
    "news_push_daily_groups": [],
}


@dataclass(slots=True)
class _Value:
    data: Any


class StringConfig:
    """A deliberately small subset of GsCore's ``StringConfig`` contract."""

    def __init__(
        self,
        name: str,
        path: Path | str,
        default: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.path = Path(path)
        self.default = default or {}
        self._values: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        raw: dict[str, Any] = {}
        if self.path.is_file():
            try:
                parsed = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    raw = parsed
            except (OSError, ValueError) as error:
                logger.warning("[SayuStock] 读取配置失败 {}: {}", self.path, error)
        self._values = dict(raw)

    def _fallback(self, key: str) -> Any:
        if key in self._values:
            return self._values[key]
        item = self.default.get(key)
        if item is not None and hasattr(item, "data"):
            return item.data
        return _FALLBACKS.get(key, "")

    def get_config(self, key: str) -> _Value:
        return _Value(self._fallback(key))

    def set_config(self, key: str, value: Any) -> bool:
        self._values[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self._values, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return True
        except OSError as error:
            logger.warning("[SayuStock] 保存配置失败 {}: {}", self.path, error)
            return False


class _StaticConfig:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get_config(self, key: str) -> _Value:
        return _Value(self._data.get(key, ""))


pic_gen_config = _StaticConfig({"pic_quality": 100, "pic_compress": False})
sp_config = _StaticConfig({"enable": False})

__all__ = ["StringConfig", "pic_gen_config", "sp_config"]
