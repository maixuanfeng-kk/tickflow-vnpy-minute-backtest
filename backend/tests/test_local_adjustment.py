from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.pricing.adjustment import AdjustmentDataError, load_local_factors, project_to_reference
from app.services.local_adj_factor_xbx import LocalXbxAdjFactorBuilder
from app.vnpy_backtest.local_data import bars_from_minute_frame
from app.vnpy_backtest.portfolio import DailyContextBuilder
from app.vnpy_backtest.service import VnpyMinuteBacktestService
from app.vnpy_backtest.signal_prices import MinuteSignalPriceProjector


def _write_xbx_daily(data_dir) -> None:
    root = data_dir / "kline_daily_xbx"
    rows = [
        {"symbol": "600000.SH", "date": date(2026, 4, 29), "close": 10.0, "pre_close": 10.0},
        # 10 -> 9 is an ex-rights reference change, not a -10% market move.
        {"symbol": "600000.SH", "date": date(2026, 4, 30), "close": 9.0, "pre_close": 9.0},
        {"symbol": "600000.SH", "date": date(2026, 5, 6), "close": 9.5, "pre_close": 9.0},
    ]
    for row in rows:
        output = root / f"date={row['date'].isoformat()}" / "part.parquet"
        output.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame([row]).write_parquet(output)


def test_local_factor_builder_derives_event_and_qfq_projection(tmp_path) -> None:
    _write_xbx_daily(tmp_path)
    summary = LocalXbxAdjFactorBuilder(tmp_path).run()

    assert summary.rows_written == 3
    assert summary.event_rows == 1
    factors = load_local_factors(tmp_path, symbols=["600000.SH"])
    by_day = {row["trade_date"]: row for row in factors.to_dicts()}
    assert by_day[date(2026, 4, 29)]["cum_factor"] == 1.0
    assert by_day[date(2026, 4, 30)]["ex_factor"] == pytest.approx(10 / 9)
    assert by_day[date(2026, 5, 6)]["cum_factor"] == pytest.approx(10 / 9)

    raw = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH", "600000.SH"],
        "date": [date(2026, 4, 29), date(2026, 4, 30), date(2026, 5, 6)],
        "close": [10.0, 9.0, 9.5], "high": [10.2, 9.1, 9.6],
    })
    adjusted = project_to_reference(raw, factors, date(2026, 5, 6), price_columns=("close", "high"))
    assert adjusted["signal_close"].to_list() == pytest.approx([9.0, 9.0, 9.5])
    # The reference day is never rescaled.
    assert adjusted["signal_high"][-1] == 9.6


def test_missing_factor_never_silently_uses_raw(tmp_path) -> None:
    _write_xbx_daily(tmp_path)
    LocalXbxAdjFactorBuilder(tmp_path).run()
    factors = load_local_factors(tmp_path, symbols=["600000.SH"], end=date(2026, 4, 30))
    raw = pl.DataFrame({"symbol": ["600000.SH"], "date": [date(2026, 4, 29)], "close": [10.0]})
    with pytest.raises(AdjustmentDataError, match="参考日"):
        project_to_reference(raw, factors, date(2026, 5, 6), price_columns=("close",))


def test_minute_signal_projection_keeps_current_day_raw(tmp_path) -> None:
    _write_xbx_daily(tmp_path)
    LocalXbxAdjFactorBuilder(tmp_path).run()
    projector = MinuteSignalPriceProjector.load(
        tmp_path, ["600000.SH"], date(2026, 4, 29), date(2026, 5, 6), "qfq",
    )

    assert projector.scale("600000.SH", date(2026, 5, 6), date(2026, 5, 6)) == 1.0
    assert projector.scale("600000.SH", date(2026, 4, 29), date(2026, 5, 6)) == pytest.approx(0.9)
    assert projector.has_event_while_held("600000.SH", date(2026, 4, 29), date(2026, 5, 6)) is True


def test_suspended_symbol_does_not_need_a_factor_for_the_no_bar_day() -> None:
    symbol = "601615.SH"
    projector = MinuteSignalPriceProjector("qfq", {(symbol, date(2026, 1, 12)): 1.0}, {})
    bars = bars_from_minute_frame(symbol, pl.DataFrame({
        "datetime": [__import__("datetime").datetime(2026, 1, 12, 9, 31)],
        "open": [10.0], "high": [10.1], "low": [9.9], "close": [10.0],
        "volume": [100.0], "amount": [1000.0],
    }))
    context = DailyContextBuilder(projector)
    context.add_day({symbol: bars})

    # 1/13 has no minute bar for this stock, so it is excluded before qfq
    # references are projected.  No missing-factor exception is appropriate.
    assert context.references(date(2026, 1, 13), symbols=set()) == {}


def test_zero_volume_minute_bars_are_treated_as_a_suspension() -> None:
    symbol = "688143.SH"
    projector = MinuteSignalPriceProjector("qfq", {(symbol, date(2026, 6, 16)): 1.0}, {})
    prior_bars = bars_from_minute_frame(symbol, pl.DataFrame({
        "datetime": [__import__("datetime").datetime(2026, 6, 16, 9, 31)],
        "open": [10.0], "high": [10.1], "low": [9.9], "close": [10.0],
        "volume": [100.0], "amount": [1000.0],
    }))
    suspended_bars = bars_from_minute_frame(symbol, pl.DataFrame({
        "datetime": [__import__("datetime").datetime(2026, 6, 17, 9, 31)],
        "open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0],
        "volume": [0.0], "amount": [0.0],
    }))
    context = DailyContextBuilder(projector)
    context.add_day({symbol: prior_bars})

    active = VnpyMinuteBacktestService._active_bars_by_symbol({symbol: suspended_bars})

    assert active == {}
    assert context.references(date(2026, 6, 17), symbols=active) == {}


def test_suspension_period_is_recorded_only_when_bounded_by_trading() -> None:
    symbol = "601615.SH"
    days = [date(2026, 1, 12), date(2026, 1, 13), date(2026, 1, 14), date(2026, 1, 23)]
    periods = VnpyMinuteBacktestService._suspension_periods(
        (symbol,), days, {days[0]: {symbol}, days[-1]: {symbol}}, days[1], days[-1],
    )

    assert periods == [{
        "symbol": symbol, "status": "suspended", "start_date": "2026-01-13",
        "end_date": "2026-01-14", "trading_days": 2,
    }]
