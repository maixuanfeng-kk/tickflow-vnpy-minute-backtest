"""Canonical data adapter for stock-pool construction."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from app.pricing.adjustment import AdjustmentDataError, load_local_factors, project_to_reference
from app.stock_pools.base import StockPoolInput


@dataclass(frozen=True)
class DataReadiness:
    month: str
    as_of_date: date | None
    ready: bool
    missing: tuple[str, ...]
    warnings: tuple[str, ...]
    daily_trading_days: int = 0
    daily_imported_at: str | None = None
    financial_imported_at: str | None = None
    factor_built_at: str | None = None
    daily_dataset: str = "kline_daily_tushare"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "month": self.month,
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
            "ready": self.ready,
            "missing": list(self.missing),
            "warnings": list(self.warnings),
            "coverage": {
                "daily_dataset": self.daily_dataset,
                "daily_trading_days_before_as_of": self.daily_trading_days,
                "listing_age_min_trading_days": 250,
                "market_cap_unit": "CNY",
                "signal_price_basis": "qfq",
                "adjustment_factor_dataset": "adj_factor_tushare",
                "daily_imported_at": self.daily_imported_at,
                "financial_imported_at": self.financial_imported_at,
                "factor_built_at": self.factor_built_at,
            },
        }


class StockPoolDataAdapter:
    """Only this adapter knows TickFlow's on-disk canonical datasets."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def readiness(self, month: str) -> DataReadiness:
        try:
            year, month_number = (int(value) for value in month.split("-", 1))
            first_of_month = date(year, month_number, 1)
        except ValueError:
            return DataReadiness(month, None, False, ("月份格式必须为 YYYY-MM",), ())
        daily_dataset, daily_path = self._daily_dataset()
        factor_path = self.data_dir / "adj_factor_tushare"
        income_path = self._financial_path()
        if not daily_path.exists() or not any(daily_path.rglob("*.parquet")):
            return DataReadiness(month, None, False, ("缺少本地 Tushare 日K数据",), ())
        daily_scan = pl.scan_parquet(str(daily_path / "**" / "*.parquet"))
        try:
            daily_columns = set(daily_scan.collect_schema().names())
        except Exception:  # noqa: BLE001
            return DataReadiness(month, None, False, ("无法读取本地 Tushare 日K数据",), ())
        required_daily = {"symbol", "date", "high", "close", "total_mv"}
        if not required_daily.issubset(daily_columns):
            missing_columns = ", ".join(sorted(required_daily - daily_columns))
            return DataReadiness(month, None, False, (f"本地 Tushare 日K缺少字段: {missing_columns}",), ())
        missing: list[str] = []
        name_changes_path = self._name_changes_path()
        if not ({"name", "is_st"} & daily_columns):
            if not name_changes_path.exists():
                missing.append("缺少历史股票名称/ST状态数据")
            else:
                try:
                    name_columns = set(pl.scan_parquet(str(name_changes_path)).collect_schema().names())
                    required_name = {"symbol", "name", "start_date", "end_date"}
                    if not required_name.issubset(name_columns):
                        missing.append("历史股票名称数据字段不完整")
                except Exception:  # noqa: BLE001
                    missing.append("无法读取历史股票名称/ST状态数据")
        dates = (
            daily_scan
            .select(pl.col("date").cast(pl.Date).alias("date"))
            .unique()
            .sort("date")
            .collect()["date"]
            .to_list()
        )
        target_dates = [item for item in dates if item >= first_of_month]
        if not target_dates:
            return DataReadiness(month, None, False, ("目标月份没有本地 Tushare 日K交易日",), ())
        first_trade_date = target_dates[0]
        prior_dates = [item for item in dates if item < first_trade_date]
        as_of_date = prior_dates[-1] if prior_dates else None
        daily_days = len(prior_dates)
        if as_of_date is None:
            missing.append("缺少月初前一交易日的日K")
        elif daily_days < 250:
            missing.append(f"日K历史不足250个交易日，无法校验上市满一年（当前{daily_days}个）")
        elif (
            daily_scan.filter(pl.col("date").cast(pl.Date) == as_of_date)
            .select(pl.col("total_mv").cast(pl.Float64, strict=False).is_not_null().sum())
            .collect().item()
            == 0
        ):
            missing.append("月初前一交易日缺少历史总市值（total_mv）")
        if not factor_path.exists() or not any(factor_path.rglob("*.parquet")):
            missing.append("缺少本地 Tushare 复权因子，请先导入 Tushare 日K并构建复权因子")
        elif as_of_date is not None:
            try:
                factor_scan = pl.scan_parquet(str(factor_path / "**" / "*.parquet"))
                factor_columns = set(factor_scan.collect_schema().names())
                required_factor = {"symbol", "trade_date", "cum_factor", "quality_status"}
                if not required_factor.issubset(factor_columns):
                    missing.append("本地 Tushare 复权因子字段不完整，请重新导入日K")
                else:
                    daily_at_reference = daily_scan.filter(
                        pl.col("date").cast(pl.Date) == as_of_date
                    ).select(pl.col("symbol").n_unique()).collect().item()
                    at_reference = factor_scan.filter(
                        (pl.col("trade_date").cast(pl.Date) == as_of_date)
                        & (pl.col("quality_status") == "ok")
                    ).select(pl.col("symbol").n_unique()).collect().item()
                    if at_reference < daily_at_reference:
                        missing.append("本地 Tushare 复权因子未完整覆盖月初前一交易日")
            except Exception:  # noqa: BLE001
                missing.append("无法读取本地 Tushare 复权因子")
        if not income_path.exists():
            missing.append("缺少标准化财务利润表数据")
        else:
            try:
                income_scan = pl.scan_parquet(str(income_path))
                columns = set(income_scan.collect_schema().names())
                if not {"symbol", "revenue", "net_income"}.issubset(columns):
                    missing.append("财务利润表缺少 symbol、revenue 或 net_income 字段")
                if not ({"period_end", "report_date"} & columns) or not ({"announce_date", "publish_date"} & columns):
                    missing.append("财务利润表缺少报告期或公告日期字段")
                elif as_of_date is not None:
                    publish_column = "announce_date" if "announce_date" in columns else "publish_date"
                    announced = income_scan.filter(pl.col(publish_column).cast(pl.Date, strict=False) <= as_of_date).select(pl.len()).collect().item()
                    if announced == 0:
                        missing.append("截至目标日没有已公告的财务利润表数据")
            except Exception:  # noqa: BLE001
                missing.append("无法读取标准化财务利润表数据")
        return DataReadiness(
            month, as_of_date, not missing, tuple(missing), (), daily_days,
            self._manifest_value(
                "local_daily_pro_import.json",
                "imported_at",
            ),
            self._manifest_value("tushare_financial_import.json", "imported_at"),
            self._manifest_value("tushare_adj_factor_import.json", "imported_at"),
            daily_dataset=daily_dataset,
        )

    def load(self, readiness: DataReadiness) -> StockPoolInput:
        if not readiness.ready or readiness.as_of_date is None:
            raise ValueError("数据尚未就绪")
        _, daily_root = self._daily_dataset()
        daily_scan = pl.scan_parquet(str(daily_root / "**" / "*.parquet"))
        daily_columns = set(daily_scan.collect_schema().names())
        name = pl.col("name").cast(pl.Utf8) if "name" in daily_columns else pl.col("symbol").cast(pl.Utf8)
        is_st = pl.col("is_st") if "is_st" in daily_columns else pl.lit(False)
        open_price = pl.col("open") if "open" in daily_columns else pl.col("close").alias("open")
        raw_daily = (
            daily_scan
            .select(pl.col("symbol").cast(pl.Utf8), name.alias("name"), is_st.alias("is_st"), "date", open_price, "high", "close", "total_mv")
            .with_columns(pl.col("date").cast(pl.Date))
            .filter(pl.col("date") <= readiness.as_of_date)
            .sort(["symbol", "date"])
            # The strategy only needs the latest 250 sessions: 200 prior bars
            # for a strict breakout, 20 recent trigger sessions, and MA60.
            .sort(["symbol", "date"])
            .collect()
        )
        name_changes_path = self._name_changes_path()
        if name_changes_path.exists():
            name_changes = pl.read_parquet(name_changes_path)
            raw_daily = self._apply_point_in_time_name_changes(raw_daily, name_changes)
        # Local Tushare history also keeps delisted securities.  The strategy
        # excludes symbols without an as-of bar, so remove them before the qfq
        # projection instead of requiring a factor at a date they no longer
        # traded.
        active_symbols = raw_daily.filter(pl.col("date") == readiness.as_of_date)["symbol"].unique().to_list()
        raw_daily = raw_daily.filter(pl.col("symbol").is_in(active_symbols))
        factors = load_local_factors(
            self.data_dir,
            symbols=raw_daily["symbol"].unique().to_list(),
            start=raw_daily["date"].min(),
            end=readiness.as_of_date,
            dataset="adj_factor_tushare",
        )
        try:
            daily = project_to_reference(raw_daily, factors, readiness.as_of_date, price_columns=("open", "high", "close"))
        except AdjustmentDataError as exc:
            raise ValueError(f"股票池前复权价格不可用: {exc}") from exc
        daily = daily.with_columns(
            pl.col("open").alias("raw_open"),
            pl.col("close").alias("raw_close"),
            pl.col("high").alias("raw_high"),
            pl.col("signal_open").alias("open"),
            pl.col("signal_close").alias("close"),
            pl.col("signal_high").alias("high"),
        ).drop(["signal_open", "signal_close", "signal_high"])
        income = pl.read_parquet(self._financial_path())
        instruments = raw_daily.group_by("symbol").agg(pl.col("date").min().alias("listing_date"))
        financials = self._normalise_financials(income)
        return StockPoolInput(
            month=readiness.month,
            as_of_date=readiness.as_of_date,
            daily=daily,
            financials=financials,
            instruments=instruments,
            warnings=readiness.warnings,
        )

    @staticmethod
    def _normalise_financials(df: pl.DataFrame) -> pl.DataFrame:
        period = "period_end" if "period_end" in df.columns else "report_date"
        publish = "announce_date" if "announce_date" in df.columns else "publish_date"
        provided_revenue_yoy = (
            pl.col("revenue_yoy").cast(pl.Float64, strict=False)
            if "revenue_yoy" in df.columns
            else pl.lit(None, dtype=pl.Float64)
        )
        normalized = df.select(
            pl.col("symbol").cast(pl.Utf8),
            pl.col(period).cast(pl.Date, strict=False).alias("report_date"),
            pl.col(publish).cast(pl.Date, strict=False).alias("publish_date"),
            pl.col("revenue").cast(pl.Float64, strict=False),
            pl.col("net_income").cast(pl.Float64, strict=False).alias("net_profit"),
            provided_revenue_yoy.alias("provided_revenue_yoy"),
        ).drop_nulls(["symbol", "report_date", "publish_date"])
        rows: list[dict[str, object]] = []
        for frame in normalized.partition_by("symbol", maintain_order=False):
            ordered = frame.sort(["report_date", "publish_date"])
            by_report = {row["report_date"]: row for row in ordered.to_dicts()}
            for row in ordered.to_dicts():
                prior = by_report.get(date(row["report_date"].year - 1, row["report_date"].month, row["report_date"].day))
                revenue = row.get("revenue")
                prior_revenue = prior.get("revenue") if prior else None
                calculated_revenue_yoy = (
                    float(revenue) / float(prior_revenue) - 1
                    if revenue is not None and prior_revenue is not None and float(prior_revenue) != 0
                    else None
                )
                row["revenue_yoy"] = calculated_revenue_yoy if calculated_revenue_yoy is not None else row.get("provided_revenue_yoy")
                row.pop("provided_revenue_yoy", None)
                rows.append(row)
        return pl.DataFrame(rows, schema={
            "symbol": pl.Utf8, "report_date": pl.Date, "publish_date": pl.Date,
            "revenue": pl.Float64, "net_profit": pl.Float64, "revenue_yoy": pl.Float64,
        })

    def _manifest_value(self, filename: str, key: str) -> str | None:
        path = self.data_dir / "user_data" / filename
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(key)
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _apply_point_in_time_name_changes(daily: pl.DataFrame, name_changes: pl.DataFrame) -> pl.DataFrame:
        """Apply historical ST names to daily bars without using today's name."""
        if daily.is_empty() or name_changes.is_empty():
            return daily
        changes = name_changes.select(
            pl.col("symbol").cast(pl.Utf8),
            pl.col("name").cast(pl.Utf8).alias("_pit_name"),
            pl.col("start_date").cast(pl.Date, strict=False),
            pl.col("end_date").cast(pl.Date, strict=False),
        ).drop_nulls(["symbol", "_pit_name", "start_date"])
        candidates = daily.select("symbol", "date").join(changes, on="symbol", how="left")
        matches = (
            candidates
            .filter(
                (pl.col("start_date") <= pl.col("date"))
                & (pl.col("end_date").is_null() | (pl.col("date") <= pl.col("end_date")))
            )
            .sort(["symbol", "date", "start_date"])
            .group_by(["symbol", "date"], maintain_order=True)
            .agg(pl.col("_pit_name").last())
        )
        enriched = daily.join(matches, on=["symbol", "date"], how="left")
        pit_normalized = pl.col("_pit_name").fill_null("").str.strip_chars().str.to_uppercase().str.replace_all(" ", "")
        pit_is_st = pit_normalized.str.contains(r"^(?:\*ST|ST|S\*ST|SST)")
        existing_is_st = pl.col("is_st").cast(pl.Boolean, strict=False).fill_null(False)
        return (
            enriched
            .with_columns(
                pl.when(pl.col("_pit_name").is_not_null()).then(pl.col("_pit_name"))
                .otherwise(pl.col("name")).alias("name"),
                (existing_is_st | pit_is_st).alias("is_st"),
            )
            .drop("_pit_name")
        )

    def _daily_dataset(self) -> tuple[str, Path]:
        for dataset in ("kline_daily_tushare", "kline_daily_pro"):
            path = self.data_dir / dataset
            if path.exists() and any(path.rglob("*.parquet")):
                return dataset, path
        return "kline_daily_tushare", self.data_dir / "kline_daily_tushare"

    def _name_changes_path(self) -> Path:
        return self.data_dir / "name_changes_tushare" / "part.parquet"

    def _financial_path(self) -> Path:
        for path in (
            self.data_dir / "financial_tushare" / "income" / "part.parquet",
            self.data_dir / "financials" / "income" / "part.parquet",
        ):
            if path.exists():
                return path
        return self.data_dir / "financial_tushare" / "income" / "part.parquet"
