from datetime import date

import polars as pl
import pytest

from app.data_providers.normalizer import normalize_daily
from app.services.kline_sync import _normalize_daily
from app.services.local_adj_factor import LocalAdjFactorBuilder, LocalAdjustmentDataError
from app.vnpy_backtest.signal_prices import MinuteSignalPriceProjector


def _write_daily(data_dir, rows: dict) -> None:
    path = data_dir / "kline_daily" / "date=2026-01-05"
    path.mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(path / "part.parquet")


def test_standard_daily_normalizers_retain_pre_close() -> None:
    source = pl.DataFrame({
        "symbol": ["600000.SH"],
        "date": [date(2026, 1, 5)],
        "open": [10.0],
        "high": [10.2],
        "low": [9.8],
        "close": [10.1],
        "pre_close": [9.9],
        "volume": [1_000.0],
        "amount": [10_000.0],
    })

    assert normalize_daily(source)["pre_close"].to_list() == [9.9]
    assert _normalize_daily(source)["pre_close"].to_list() == [9.9]


def test_builds_event_factors_from_standard_pre_close_and_projects_qfq(tmp_path) -> None:
    _write_daily(tmp_path, {
        "symbol": ["600000.SH", "600000.SH", "600000.SH"],
        "date": [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)],
        "close": [10.0, 9.2, 9.5],
        "pre_close": [10.0, 9.0, 9.2],
    })

    summary = LocalAdjFactorBuilder(tmp_path).run()
    assert summary.event_rows == 1
    factors = pl.read_parquet(tmp_path / "adj_factor" / "all.parquet")
    assert factors["ex_factor"].to_list() == pytest.approx([1.0, 10 / 9, 1.0])

    projector = MinuteSignalPriceProjector.load(tmp_path, ["600000.SH"], date(2026, 1, 7), "qfq")
    assert projector.scale("600000.SH", date(2026, 1, 5), date(2026, 1, 7)) == pytest.approx(0.9)
    assert projector.scale("600000.SH", date(2026, 1, 7), date(2026, 1, 7)) == 1.0
    assert projector.has_event_while_held("600000.SH", date(2026, 1, 5), date(2026, 1, 7))


def test_factor_builder_rejects_missing_pre_close_after_first_day(tmp_path) -> None:
    _write_daily(tmp_path, {
        "symbol": ["600000.SH", "600000.SH"],
        "date": [date(2026, 1, 5), date(2026, 1, 6)],
        "close": [10.0, 10.2],
        "pre_close": [10.0, None],
    })
    with pytest.raises(LocalAdjustmentDataError, match="pre_close"):
        LocalAdjFactorBuilder(tmp_path).run()


def test_qfq_projector_requires_coverage_but_raw_does_not(tmp_path) -> None:
    raw = MinuteSignalPriceProjector.load(tmp_path, ["600000.SH"], date(2026, 1, 5), "raw")
    assert raw.scale("600000.SH", date(2026, 1, 5), date(2026, 1, 5)) == 1.0
    with pytest.raises(LocalAdjustmentDataError, match="复权因子"):
        MinuteSignalPriceProjector.load(tmp_path, ["600000.SH"], date(2026, 1, 5), "qfq")
