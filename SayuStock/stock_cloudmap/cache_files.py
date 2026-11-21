"""Expire only the original market-provider cache files."""

import datetime
from datetime import timedelta

from gsuid_core.logger import logger

from ..utils.resource_path import DATA_PATH
from ..utils.sector_resolve import INDUSTRY_MEMBER_CACHE_MINUTES
from ..stock_config.stock_config import STOCK_CONFIG


def prune_market_cache() -> None:
    retention_days = int(STOCK_CONFIG.get_config("stock_cache_retention_days").data)
    expired_before = datetime.datetime.now() - timedelta(days=retention_days)
    logger.info(f"[SayuStock] 开始执行[清理{retention_days}天前缓存数据]")
    member_expired_before = datetime.datetime.now() - timedelta(minutes=INDUSTRY_MEMBER_CACHE_MINUTES)
    for cache_file in DATA_PATH.iterdir():
        if not cache_file.is_file():
            continue
        file_mod_time = datetime.datetime.fromtimestamp(cache_file.stat().st_mtime)
        # 行业成分名单单独按 30 天过期，避免被通用 7 天清理误删
        if cache_file.name.startswith("industry-members_"):
            if file_mod_time < member_expired_before:
                cache_file.unlink(missing_ok=True)
            continue
        if file_mod_time < expired_before:
            cache_file.unlink(missing_ok=True)

    logger.success("[SayuStock] [清理过期缓存数据] 执行完成！")
