from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from app.vnpy_backtest.stock_pool_data import monthly_stock_pools
from app.vnpy_backtest.strategies.base import DailyReference, PortfolioContext
from app.vnpy_backtest.strategies.etf_159915_stock_pool import (
    Etf159915StockPoolStrategy,
    select_stock_candidates,
)


def _bar(symbol: str, close: float, open_price: float, volume: float) -> BarData:
    exchange = Exchange.SSE if symbol.startswith(("6", "68")) else Exchange.SZSE
    moment = datetime(2026, 5, 6, 9, 31)
    return BarData(
        gateway_name="TEST", symbol=symbol.split(".")[0], exchange=exchange,
        datetime=moment, interval=Interval.MINUTE, volume=volume, turnover=close * volume,
        open_price=open_price, high_price=close, low_price=open_price, close_price=close,
    )


def _reference(previous_close: float, prior_close: float, previous_volume: float) -> DailyReference:
    return DailyReference(
        previous_close=previous_close,
        closes=(prior_close, previous_close),
        previous_cumulative_volumes={time(9, 31): previous_volume},
    )


def test_candidate_filter_uses_etf_relative_return_and_volume_ratio() -> None:
    now = datetime(2026, 5, 6, 9, 31)
    bars = {
        "000001.SZ": _bar("000001.SZ", close=10.29, open_price=10.10, volume=150),
        "600000.SH": _bar("600000.SH", close=10.20, open_price=10.10, volume=140),
    }
    references = {
        "000001.SZ": _reference(10.0, 9.8, 100),
        "600000.SH": _reference(10.0, 9.8, 100),
    }

    candidates = select_stock_candidates(
        bars, references, etf_return=0.01, now=now,
        cumulative_volumes={"000001.SZ": 150, "600000.SH": 140},
        pool_symbols={"000001.SZ", "600000.SH"},
    )

    assert [item.symbol for item in candidates] == ["000001.SZ", "600000.SH"]
    assert candidates[0].volume_ratio == 1.5
    assert candidates[1].volume_ratio == 1.4


def test_candidate_filter_rejects_equal_etf_return_and_price_below_open() -> None:
    now = datetime(2026, 5, 6, 9, 31)
    bars = {
        "000001.SZ": _bar("000001.SZ", close=10.10, open_price=10.20, volume=150),
        "600000.SH": _bar("600000.SH", close=10.14, open_price=10.10, volume=150),
    }
    references = {
        "000001.SZ": _reference(10.0, 9.8, 100),
        "600000.SH": _reference(10.0, 9.8, 100),
    }

    candidates = select_stock_candidates(
        bars, references, etf_return=0.015, now=now,
        cumulative_volumes={"000001.SZ": 150, "600000.SH": 150},
        pool_symbols={"000001.SZ", "600000.SH"},
    )

    assert [item.symbol for item in candidates] == []


def test_strategy_emits_ranked_equal_pool_buys_on_etf_buy_event() -> None:
    now = datetime(2026, 5, 6, 9, 31)
    bars = {
        "000001.SZ": _bar("000001.SZ", close=10.29, open_price=10.10, volume=150),
        "600000.SH": _bar("600000.SH", close=10.20, open_price=10.10, volume=140),
    }
    references = {
        "000001.SZ": _reference(10.0, 9.8, 100),
        "600000.SH": _reference(10.0, 9.8, 100),
    }
    strategy = Etf159915StockPoolStrategy({
        "etf_events": {now.isoformat(sep=" "): {"direction": "buy", "etf_return": 0.01, "reason": "4_1_1"}},
        "monthly_pools": {"2026-05": {"effective_date": "2026-05-06", "symbols": list(bars)}},
        "max_positions": 10,
    })

    intents = strategy.on_minute(bars, PortfolioContext(now, 10_000_000, 0, {}, references))

    assert [intent.symbol for intent in intents] == ["000001.SZ", "600000.SH"]
    assert all(intent.direction.value == "多" for intent in intents)
    assert intents[0].diagnostic["volume_ratio"] == 1.5


def test_authoritative_may_pool_has_217_members() -> None:
    pools = monthly_stock_pools(Path(__file__).parents[3] / "data")
    assert len(pools["2026-05"]["symbols"]) == 217
