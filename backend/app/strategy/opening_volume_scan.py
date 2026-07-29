"""Strategy-page scanner that reuses the vn.py opening-volume signal rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Any, Mapping

import polars as pl
from vnpy.trader.constant import Direction

from app.backtest.opening_volume_shared import rank_opening_volume_candidates
from app.services import watchlist
from app.vnpy_backtest.local_data import bars_from_minute_frame
from app.vnpy_backtest.portfolio import DailyContextBuilder
from app.vnpy_backtest.strategies.base import PortfolioContext
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy


@dataclass(frozen=True)
class OpeningVolumeScanConfig:
    as_of: date
    strategy_params: Mapping[str, Any] = field(default_factory=dict)


class OpeningVolumeScanService:
    """Scan the TickFlow watchlist with the same A/B/C rules as vn.py backtests."""

    def __init__(self, repo) -> None:
        self.repo = repo

    def run(self, config: OpeningVolumeScanConfig) -> dict:
        started = perf_counter()
        symbols = [str(row["symbol"]) for row in watchlist.list_symbols() if row.get("symbol")]
        if not symbols:
            return self._result(config.as_of, [], started)

        strategy = OpeningBreakoutPoolStrategy(config.strategy_params)
        daily_context = DailyContextBuilder()
        candidates: list[dict[str, Any]] = []
        matched: set[str] = set()
        found_target_day = False
        for trading_day, frame in self.repo.iter_minute_days(
            symbols,
            config.as_of - timedelta(days=20),
            config.as_of,
        ):
            bars_by_symbol = self._bars_by_symbol(frame, symbols)
            if not bars_by_symbol:
                continue
            if trading_day < config.as_of:
                daily_context.add_day(bars_by_symbol)
                continue
            if trading_day != config.as_of:
                continue
            found_target_day = True
            references = daily_context.references()
            by_timestamp: dict[datetime, dict] = {}
            for symbol, bars in bars_by_symbol.items():
                for bar in bars:
                    by_timestamp.setdefault(bar.datetime, {})[symbol] = bar
            for timestamp, bars in sorted(by_timestamp.items()):
                context = PortfolioContext(timestamp, 0.0, 0.0, {}, references)
                for intent in strategy.on_minute(bars, context):
                    if intent.direction != Direction.LONG or intent.symbol in matched:
                        continue
                    matched.add(intent.symbol)
                    diagnostic = dict(intent.diagnostic)
                    candidates.append({
                        "symbol": intent.symbol,
                        "date": str(timestamp.date()),
                        "time": (timestamp + timedelta(minutes=1)).strftime("%H:%M"),
                        "close": float(diagnostic.get("signal_price") or 0.0),
                        "volume_ratio": float(diagnostic.get("volume_ratio") or 0.0),
                        "today_return": float(diagnostic.get("today_return") or 0.0),
                        "previous_return": float(diagnostic.get("previous_return") or 0.0),
                        "entry_reason": diagnostic.get("primary_reason"),
                    })
            daily_context.add_day(bars_by_symbol)

        if not found_target_day:
            raise ValueError("所选日期范围内没有本地分钟 K 数据")
        rows = rank_opening_volume_candidates(candidates, mode="volume_ratio")
        return self._result(config.as_of, rows, started)

    @staticmethod
    def _bars_by_symbol(frame: pl.DataFrame, symbols: list[str]) -> dict[str, list]:
        wanted = set(symbols)
        result: dict[str, list] = {}
        for sub in frame.partition_by("symbol", maintain_order=False):
            if not sub.is_empty() and sub["symbol"][0] in wanted:
                symbol = str(sub["symbol"][0])
                result[symbol] = bars_from_minute_frame(symbol, sub)
        return result

    @staticmethod
    def _result(as_of: date, rows: list[dict[str, Any]], started: float) -> dict:
        return {
            "as_of": str(as_of),
            "strategy": "opening_volume_portfolio",
            "rows": rows,
            "total": len(rows),
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        }
