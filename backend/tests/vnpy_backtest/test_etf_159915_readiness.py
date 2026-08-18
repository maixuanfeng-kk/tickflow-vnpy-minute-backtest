from datetime import date, datetime, time, timedelta

import polars as pl

from app.vnpy_backtest.readiness import Etf159915ReadinessService
from app.tickflow.etf_datasets import ETF_DAILY_DATASET, ETF_MINUTE_DATASET


SYMBOL = "159915.SZ"


def _write_daily(
    root,
    days: list[date],
    *,
    include_strategy_fields: bool = True,
    invalid_high: bool = False,
) -> None:
    for trading_day in days:
        path = root / ETF_DAILY_DATASET / f"date={trading_day.isoformat()}"
        path.mkdir(parents=True, exist_ok=True)
        columns = {"symbol": [SYMBOL], "date": [trading_day]}
        if include_strategy_fields:
            columns.update(
                {
                    "open": [2.0],
                    "high": [None if invalid_high else 2.1],
                    "low": [1.9],
                    "close": [2.05],
                    "pre_close": [2.0],
                    "volume": [1_000.0],
                    "amount": [2_050.0],
                }
            )
        pl.DataFrame(columns).write_parquet(path / "part.parquet")


def _minute_times() -> list[time]:
    morning_start = datetime(2025, 1, 1, 9, 30)
    afternoon_start = datetime(2025, 1, 1, 13, 1)
    return [
        (morning_start + timedelta(minutes=offset)).time() for offset in range(121)
    ] + [
        (afternoon_start + timedelta(minutes=offset)).time() for offset in range(120)
    ]


def _write_minute(root, trading_day: date, *, omitted: set[time] | None = None, zero_at: time | None = None) -> None:
    omitted = omitted or set()
    times = [value for value in _minute_times() if value not in omitted]
    path = root / ETF_MINUTE_DATASET / f"date={trading_day.isoformat()}"
    path.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": [SYMBOL] * len(times),
            "datetime": [datetime.combine(trading_day, value) for value in times],
            "open": [2.0] * len(times),
            "high": [2.0] * len(times),
            "low": [2.0] * len(times),
            "close": [2.0] * len(times),
            "volume": [0.0 if value == zero_at else 100.0 for value in times],
            "amount": [0.0 if value == zero_at else 200.0 for value in times],
        }
    ).write_parquet(path / "part.parquet")


def _days() -> tuple[list[date], list[date]]:
    warmup = [date(2024, 12, 16) + timedelta(days=offset) for offset in range(10)]
    requested = [date(2025, 1, 2), date(2025, 1, 3)]
    return warmup, requested


def test_complete_etf_range_is_ready(tmp_path) -> None:
    warmup, requested = _days()
    _write_daily(tmp_path, [*warmup, *requested])
    for trading_day in [*warmup, *requested]:
        _write_minute(tmp_path, trading_day)

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is True
    assert result["blocking_reasons"] == []
    assert result["warnings"] == []
    assert result["coverage"]["daily"]["warmup_days"] == 10
    assert result["coverage"]["minute"]["trading_days"] == 12
    assert result["coverage"]["minute"]["row_count"] == 2_892


def test_missing_warmup_minute_day_blocks_readiness(tmp_path) -> None:
    warmup, requested = _days()
    _write_daily(tmp_path, [*warmup, *requested])
    for trading_day in [*warmup[1:], *requested]:
        _write_minute(tmp_path, trading_day)

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is False
    assert result["coverage"]["minute"]["missing_days"] == [warmup[0].isoformat()]
    assert any("预热" in reason for reason in result["blocking_reasons"])


def test_daily_schema_missing_strategy_prices_blocks_readiness(tmp_path) -> None:
    warmup, requested = _days()
    _write_daily(tmp_path, [*warmup, *requested], include_strategy_fields=False)
    for trading_day in [*warmup, *requested]:
        _write_minute(tmp_path, trading_day)

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is False
    assert "ETF 日线数据字段不完整。" in result["blocking_reasons"]


def test_daily_invalid_strategy_prices_block_readiness(tmp_path) -> None:
    warmup, requested = _days()
    _write_daily(tmp_path, [*warmup, *requested], invalid_high=True)
    for trading_day in [*warmup, *requested]:
        _write_minute(tmp_path, trading_day)

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is False
    assert "ETF 日线数据包含无效价格。" in result["blocking_reasons"]


def test_minute_schema_missing_execution_prices_blocks_readiness(tmp_path) -> None:
    warmup, requested = _days()
    _write_daily(tmp_path, [*warmup, *requested])
    for trading_day in [*warmup, *requested]:
        _write_minute(tmp_path, trading_day)
    part = tmp_path / ETF_MINUTE_DATASET / f"date={requested[0].isoformat()}" / "part.parquet"
    pl.read_parquet(part).drop("amount").write_parquet(part)

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is False
    assert "ETF 分钟数据字段不完整。" in result["blocking_reasons"]


def test_missing_minute_day_blocks_readiness(tmp_path) -> None:
    warmup, requested = _days()
    _write_daily(tmp_path, [*warmup, *requested])
    for trading_day in warmup:
        _write_minute(tmp_path, trading_day)
    _write_minute(tmp_path, requested[0])

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is False
    assert result["coverage"]["minute"]["missing_days"] == ["2025-01-03"]
    assert any("缺少 1 个交易日" in reason for reason in result["blocking_reasons"])


def test_missing_essential_open_or_close_bar_blocks_readiness(tmp_path) -> None:
    warmup, requested = _days()
    _write_daily(tmp_path, [*warmup, *requested])
    for trading_day in warmup:
        _write_minute(tmp_path, trading_day)
    _write_minute(tmp_path, requested[0], omitted={time(9, 30)})
    _write_minute(tmp_path, requested[1], omitted={time(15, 0)})

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is False
    assert result["coverage"]["minute"]["missing_open_days"] == ["2025-01-02"]
    assert result["coverage"]["minute"]["missing_close_days"] == ["2025-01-03"]


def test_insufficient_daily_warmup_blocks_readiness(tmp_path) -> None:
    _, requested = _days()
    _write_daily(tmp_path, [date(2024, 12, 31), *requested])
    for trading_day in [date(2024, 12, 31), *requested]:
        _write_minute(tmp_path, trading_day)

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is False
    assert result["coverage"]["daily"]["warmup_days"] == 1
    assert any("至少需要 10 个预热交易日" in reason for reason in result["blocking_reasons"])


def test_sparse_terminal_bar_and_zero_volume_only_warn(tmp_path) -> None:
    warmup, requested = _days()
    _write_daily(tmp_path, [*warmup, *requested])
    for trading_day in warmup:
        _write_minute(tmp_path, trading_day)
    _write_minute(tmp_path, requested[0], omitted={time(14, 59)})
    _write_minute(tmp_path, requested[1], zero_at=time(14, 59))

    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=requested[0],
        end=requested[-1],
    )

    assert result["ready"] is True
    assert result["blocking_reasons"] == []
    assert result["coverage"]["minute"]["sparse_days"] == 1
    assert result["coverage"]["minute"]["missing_1459_days"] == ["2025-01-02"]
    assert result["coverage"]["minute"]["zero_volume_rows"] == 1
    assert len(result["warnings"]) == 3


def test_absent_etf_datasets_return_blocking_payload(tmp_path) -> None:
    result = Etf159915ReadinessService(tmp_path).check(
        symbol=SYMBOL,
        start=date(2025, 1, 2),
        end=date(2025, 1, 3),
    )

    assert result["ready"] is False
    assert len(result["blocking_reasons"]) == 2
