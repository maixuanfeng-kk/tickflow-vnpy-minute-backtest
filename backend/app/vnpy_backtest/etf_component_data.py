"""Point-in-time 159915 ETF component snapshots and factor weights."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping

import polars as pl

COMPONENT_DATASET = "etf_159915_components"
_FACTOR_CACHE: dict[str, tuple[pl.DataFrame, pl.DataFrame]] = {}


def normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(".XSHE", ".SZ").replace(".XSHG", ".SH")


def import_component_snapshots(source: str | Path, data_dir: str | Path) -> dict[str, int]:
    source = Path(source)
    root = Path(data_dir) / COMPONENT_DATASET
    raw = pl.read_parquet(source)
    required = {"交易日期", "前一交易日", "成分股代码", "盘前股票权重", "盘前成分市值"}
    if missing := required - set(raw.columns):
        raise ValueError(f"component source missing columns: {sorted(missing)}")
    valid = pl.col("记录有效") if "记录有效" in raw.columns else pl.lit(True)
    frame = raw.select(
        pl.col("交易日期").cast(pl.Date).alias("trade_date"),
        pl.col("前一交易日").cast(pl.Date).alias("previous_trade_date"),
        pl.col("成分股代码").map_elements(normalize_symbol, return_dtype=pl.Utf8).alias("symbol"),
        pl.col("盘前股票权重").cast(pl.Float64).alias("base_weight"),
        pl.col("盘前成分市值").cast(pl.Float64).alias("component_market_value"),
        valid.cast(pl.Boolean).alias("is_valid"),
        pl.lit(str(source)).alias("source_file"),
    ).filter(
        pl.col("trade_date").is_not_null()
        & pl.col("symbol").str.len_chars().gt(0)
        & pl.col("base_weight").is_finite()
    ).unique(["trade_date", "symbol"], keep="last")
    result: dict[str, int] = {}
    for part in frame.partition_by("trade_date", maintain_order=False):
        day = part["trade_date"][0]
        target = root / f"date={day.isoformat()}" / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = pl.read_parquet(target) if target.exists() else pl.DataFrame(schema=part.schema)
        merged = pl.concat([existing, part], how="diagonal_relaxed").unique(["trade_date", "symbol"], keep="last").sort("symbol")
        temporary = target.with_name(target.name + ".tmp")
        merged.write_parquet(temporary)
        temporary.replace(target)
        result[day.isoformat()] = merged.height
    return result


def snapshot_dates(data_dir: str | Path, start: date, end: date) -> list[date]:
    root = Path(data_dir) / COMPONENT_DATASET
    result: list[date] = []
    for directory in root.glob("date=*") if root.exists() else []:
        if not (directory / "part.parquet").exists():
            continue
        try:
            day = date.fromisoformat(directory.name.removeprefix("date="))
        except ValueError:
            continue
        if start <= day <= end:
            result.append(day)
    return sorted(result)


def load_snapshot(data_dir: str | Path, day: date) -> pl.DataFrame:
    part = Path(data_dir) / COMPONENT_DATASET / f"date={day.isoformat()}" / "part.parquet"
    if not part.exists():
        raise ValueError(f"missing component snapshot: {day.isoformat()}")
    return pl.read_parquet(part).filter(pl.col("is_valid") == True)  # noqa: E712


def _growth_factor(value: float) -> float:
    return 1.0 if value < 0.10 else 1.5 if value < 0.20 else 2.0


def adjusted_weights(data_dir: str | Path, snapshot: pl.DataFrame, signal_day: date) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Calculate weights with only rows announced strictly before signal_day."""
    root = Path(data_dir)
    symbols = [normalize_symbol(item) for item in snapshot.get_column("symbol").to_list()]
    cache_key = str(root.resolve())
    if cache_key not in _FACTOR_CACHE:
        daily_paths = list((root / "kline_daily_tushare").glob("date=*/part.parquet"))
        income_paths = list((root / "financials" / "income").glob("**/*.parquet"))
        if not daily_paths or not income_paths:
            raise ValueError("stock daily and financial parquet data are required")
        _FACTOR_CACHE[cache_key] = (
            pl.concat([pl.read_parquet(path) for path in daily_paths], how="diagonal_relaxed").select("symbol", "date", "close", "volume", "total_mv", "circ_mv").sort(["symbol", "date"]),
            pl.concat([pl.read_parquet(path) for path in income_paths], how="diagonal_relaxed").select("symbol", "period_end", "announce_date", "revenue", "net_income"),
        )
    daily_all, income_all = _FACTOR_CACHE[cache_key]
    daily = daily_all.filter(pl.col("symbol").is_in(symbols))
    # Revenue hard filtering is limited to reports announced during the year
    # ending immediately before the signal.  The same-day report is excluded.
    window_start = signal_day - timedelta(days=366)
    income = income_all.filter(pl.col("symbol").is_in(symbols) & (pl.col("announce_date") < signal_day))
    reports: dict[str, dict[date, dict]] = defaultdict(dict)
    for row in income.iter_rows(named=True):
        symbol = normalize_symbol(row["symbol"])
        old = reports[symbol].get(row["period_end"])
        if old is None or row["announce_date"] > old["announce_date"]:
            reports[symbol][row["period_end"]] = row
    output: list[dict[str, object]] = []
    filtered: defaultdict[str, int] = defaultdict(int)
    for component in snapshot.iter_rows(named=True):
        symbol = normalize_symbol(component["symbol"])
        history = daily.filter((pl.col("symbol") == symbol) & (pl.col("date") <= component["previous_trade_date"])).tail(6)
        if history.height < 6:
            raise ValueError(f"missing five completed daily rows: {symbol}")
        latest = history.tail(1).row(0, named=True)
        if any(latest.get(name) is None or float(latest[name]) <= 0 for name in ("close", "total_mv", "circ_mv")):
            raise ValueError(f"missing daily market data: {symbol}")
        # The local daily parquet stores volume in shares and circ_mv in CNY.
        turnover_values = [float(row["volume"]) * float(row["close"]) / float(row["circ_mv"]) for row in history.tail(5).iter_rows(named=True)]
        # Local Tushare daily import stores market values in CNY.
        if float(latest["total_mv"]) < 5_000_000_000:
            filtered["market_cap_below_50b"] += 1
            continue
        if turnover_values[-1] < 0.02:
            filtered["turnover_below_2pct"] += 1
            continue
        visible = reports.get(symbol, {})
        revenue_growths: list[float] = []
        revenue_bad = False
        for period, report in visible.items():
            if report["announce_date"] < window_start:
                continue
            prior = visible.get(period.replace(year=period.year - 1))
            if prior is None or report["revenue"] in (None, 0) or prior["revenue"] in (None, 0):
                continue
            growth = float(report["revenue"]) / float(prior["revenue"]) - 1.0
            revenue_growths.append(growth)
            revenue_bad |= growth < -0.20
        # Temporarily disabled: a revenue decline below -20% no longer
        # hard-filters the component. Revenue growth is still used below as a
        # weighting coefficient.
        if not revenue_growths:
            raise ValueError(f"missing revenue yoy data: {symbol}")
        latest_period = max(visible) if visible else None
        latest_report = visible.get(latest_period) if latest_period else None
        prior_report = visible.get(latest_period.replace(year=latest_period.year - 1)) if latest_period else None
        if not latest_report or not prior_report or latest_report["net_income"] is None or prior_report["net_income"] in (None, 0):
            raise ValueError(f"missing net profit yoy data: {symbol}")
        revenue_growth = revenue_growths[-1]
        profit_growth = (float(latest_report["net_income"]) - float(prior_report["net_income"])) / abs(float(prior_report["net_income"]))
        market_factor = 2.0 if float(latest["total_mv"]) < 20_000_000_000 else 1.5 if float(latest["total_mv"]) < 100_000_000_000 else 1.0 if float(latest["total_mv"]) < 300_000_000_000 else 0.7
        avg_turnover = sum(turnover_values) / 5
        turnover_factor = 1.0 if avg_turnover < 0.03 else 1.5 if avg_turnover < 0.05 else 2.0
        factors = {"market_cap": market_factor, "turnover_5d": turnover_factor, "revenue": _growth_factor(revenue_growth), "net_profit": _growth_factor(profit_growth)}
        base = float(component["base_weight"])
        output.append({"symbol": symbol, "base_weight": base, "adjusted_weight": base * market_factor * turnover_factor * factors["revenue"] * factors["net_profit"], "market_cap": float(latest["total_mv"]), "turnover_5d": avg_turnover, "revenue_growth": revenue_growth, "net_profit_growth": profit_growth, "factors": factors})
    total = sum(float(item["adjusted_weight"]) for item in output)
    if total <= 0:
        raise ValueError(f"no eligible components on {signal_day}")
    for item in output:
        item["normalized_weight"] = float(item["adjusted_weight"]) / total
    return output, dict(filtered)
