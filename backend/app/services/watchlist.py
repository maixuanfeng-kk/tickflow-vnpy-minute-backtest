"""自选股服务(§6.1)。

存储:`data/user_data/watchlist.parquet`,字段 symbol + added_at + note。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import polars as pl

from app.config import settings
from app.services.watchlist_pools import WatchlistPoolStore
from app.tickflow.capabilities import Cap, CapabilitySet
from app.tickflow.client import get_client
from app.tickflow.rate_limits import chunked, resolve_limit

logger = logging.getLogger(__name__)


def _path() -> Path:
    p = settings.data_dir / "user_data" / "watchlist.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _store() -> WatchlistPoolStore:
    store = WatchlistPoolStore(settings.data_dir)
    store.migrate_legacy("2026-01")
    return store


def list_pools() -> list[dict]:
    return _store().list_pools()


def create_pool(month: str) -> dict:
    return _store().create(month)


def get_pool(pool_key: str) -> dict | None:
    return _store().get_pool(pool_key)


def legacy_migration_status(month: str = "2026-01") -> dict:
    """返回旧自选文件到指定月份池的迁移状态，供前端明确展示。"""
    store = WatchlistPoolStore(settings.data_dir)
    legacy = settings.data_dir / "user_data" / "watchlist.parquet"
    legacy_count = pl.read_parquet(legacy).height if legacy.exists() else 0
    target_count = len(store.list_members(f"month:{month}"))
    return {
        "legacy_count": legacy_count,
        "target_month": month,
        "target_count": target_count,
        "migrated": store.get_pool(f"month:{month}") is not None,
    }


def migrate_legacy_watchlist(month: str = "2026-01") -> dict:
    """手动执行一次旧自选迁移，保留原始 Parquet 文件。"""
    store = WatchlistPoolStore(settings.data_dir)
    migrated_count = store.migrate_legacy(month)
    return {
        "migrated_count": migrated_count,
        "pool": store.get_pool(f"month:{month}"),
    }


def list_symbols(pool_key: str | None = None, *, aggregate: bool = False) -> list[dict]:
    store = _store()
    return store.aggregate() if aggregate else store.list_members(pool_key)


def add(symbol: str, note: str = "", pool_key: str | None = None) -> list[dict]:
    store = _store()
    resolved = pool_key or store.default_pool_key()
    return store.add(resolved, symbol, note)


def remove(symbol: str, pool_key: str | None = None) -> list[dict]:
    store = _store()
    resolved = pool_key or store.default_pool_key()
    return store.remove(resolved, symbol)


def move_to_top(symbol: str, pool_key: str | None = None) -> list[dict]:
    store = _store()
    resolved = pool_key or store.default_pool_key()
    return store.move_to_top(resolved, symbol)


def clear(pool_key: str | None = None) -> int:
    """清空自选列表。返回移除的数量。"""
    store = _store()
    resolved = pool_key or store.default_pool_key()
    return store.clear(resolved)


def fetch_quotes(symbols: list[str], capset: CapabilitySet, timeout_s: float = 8.0) -> list[dict]:
    """拉取实时行情。

    优先用 quote.batch;否则降级为 quote.by_symbol 单股请求。
    timeout_s: 单批次请求超时(秒)，防止 API 卡死阻塞整个请求。
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    if not symbols:
        return []

    tf = get_client()
    quotes: list[dict] = []

    # 走 batch
    if capset.has(Cap.QUOTE_BATCH):
        batch_size = resolve_limit(capset, Cap.QUOTE_BATCH, default_batch=50).batch
    elif capset.has(Cap.QUOTE_BY_SYMBOL):
        batch_size = resolve_limit(capset, Cap.QUOTE_BY_SYMBOL, default_batch=5).batch
    else:
        # 无任何实时行情能力(none/free 档走 free-api 服务器,不提供实时行情)
        # 提前返回空,避免发起注定失败的请求
        return []

    chunks = chunked(symbols, batch_size)

    # 用线程池为每个批次加超时保护
    pool = ThreadPoolExecutor(max_workers=1)
    for chunk in chunks:
        try:
            future = pool.submit(tf.quotes.get, symbols=chunk, as_dataframe=True)
            raw = future.result(timeout=timeout_s)
            if raw is None or len(raw) == 0:
                continue
            df = pl.from_pandas(raw)
            rename_map = {
                "last_price": "price",
                "ext.change_pct": "pct",
                "ext.name": "name",
            }
            df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})
            quotes.extend(df.to_dicts())
        except FuturesTimeout:
            logger.warning("quote fetch timeout (%.1fs) for %d symbols", timeout_s, len(chunk))
            break  # 超时后不再尝试后续批次
        except Exception as e:  # noqa: BLE001
            logger.warning("quote fetch failed for %d symbols: %s", len(chunk), e)
    pool.shutdown(wait=False)

    return quotes
