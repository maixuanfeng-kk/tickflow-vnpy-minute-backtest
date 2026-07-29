"""Canonical data adapter for stock-pool construction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

import polars as pl

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

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "month": self.month,
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
            "ready": self.ready,
            "missing": list(self.missing),
            "warnings": list(self.warnings),
            "coverage": {
                "daily_dataset": "kline_daily_xbx",
                "daily_trading_days_before_as_of": self.daily_trading_days,
                "listing_age_min_trading_days": 250,
                "market_cap_unit": "CNY",
                "daily_imported_at": self.daily_imported_at,
                "financial_imported_at": self.financial_imported_at,
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
        daily_path = self.data_dir / "kline_daily_xbx"
        income_path = self.data_dir / "financials" / "income" / "part.parquet"
        instruments_path = self.data_dir / "instruments" / "instruments.parquet"
        if not daily_path.exists() or not any(daily_path.rglob("*.parquet")):
            return DataReadiness(month, None, False, ("缺少专业日K数据（kline_daily_pro）",), ())
        daily_scan = pl.scan_parquet(str(daily_path / "**" / "*.parquet"))
        try:
            daily_columns = set(daily_scan.collect_schema().names())
        except Exception:  # noqa: BLE001
            return DataReadiness(month, None, False, ("无法读取专业日K Parquet 数据",), ())
        required_daily = {"symbol", "name", "date", "high", "close", "total_mv"}
        if not required_daily.issubset(daily_columns):
            missing_columns = ", ".join(sorted(required_daily - daily_columns))
            return DataReadiness(month, None, False, (f"专业日K缺少字段: {missing_columns}",), ())
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
            return DataReadiness(month, None, False, ("目标月份没有专业日K交易日",), ())
        first_trade_date = target_dates[0]
        prior_dates = [item for item in dates if item < first_trade_date]
        as_of_date = prior_dates[-1] if prior_dates else None
        missing: list[str] = []
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
        if not instruments_path.exists():
            missing.append("缺少股票基础信息，无法校验上市日期")
        else:
            try:
                columns = set(pl.read_parquet_schema(instruments_path).names())
                if not {"symbol", "listing_date"}.issubset(columns):
                    missing.append("股票基础信息缺少 symbol 或 listing_date 字段")
            except Exception:  # noqa: BLE001
                missing.append("无法读取股票基础信息")
        return DataReadiness(
            month, as_of_date, not missing, tuple(missing), (), daily_days,
            self._manifest_value("local_daily_xbx_import.json", "imported_at"),
            self._manifest_value("local_financial_import.json", "imported_at"),
        )

    def load(self, readiness: DataReadiness) -> StockPoolInput:
        if not readiness.ready or readiness.as_of_date is None:
            raise ValueError("数据尚未就绪")
        daily_path = self.data_dir / "kline_daily_xbx" / "**" / "*.parquet"
        daily = (
            pl.scan_parquet(str(daily_path))
            .select("symbol", "name", "date", "high", "close", "total_mv")
            .with_columns(pl.col("date").cast(pl.Date))
            .filter(pl.col("date") <= readiness.as_of_date)
            .sort(["symbol", "date"])
            # The strategy only needs the latest 250 sessions: 200 prior bars
            # for a strict breakout, 20 recent trigger sessions, and MA60.
            .group_by("symbol", maintain_order=True)
            .tail(250)
            .sort(["symbol", "date"])
            .collect()
        )
        income = pl.read_parquet(self.data_dir / "financials" / "income" / "part.parquet")
        instruments = pl.read_parquet(self.data_dir / "instruments" / "instruments.parquet").select(
            pl.col("symbol").cast(pl.Utf8),
            pl.coalesce([
                pl.col("listing_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y-%m-%d", strict=False),
                pl.col("listing_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False),
            ]).alias("listing_date"),
        ).unique(subset=["symbol"], keep="last")
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
        normalized = df.select(
            pl.col("symbol").cast(pl.Utf8),
            pl.col(period).cast(pl.Date, strict=False).alias("report_date"),
            pl.col(publish).cast(pl.Date, strict=False).alias("publish_date"),
            pl.col("revenue").cast(pl.Float64, strict=False),
            pl.col("net_income").cast(pl.Float64, strict=False).alias("net_profit"),
        ).drop_nulls(["symbol", "report_date", "publish_date"])
        rows: list[dict[str, object]] = []
        for frame in normalized.partition_by("symbol", maintain_order=False):
            by_report = {row["report_date"]: row for row in frame.to_dicts()}
            for row in frame.to_dicts():
                prior = by_report.get(date(row["report_date"].year - 1, row["report_date"].month, row["report_date"].day))
                revenue = row.get("revenue")
                prior_revenue = prior.get("revenue") if prior else None
                row["revenue_yoy"] = (
                    float(revenue) / float(prior_revenue) - 1
                    if revenue is not None and prior_revenue is not None and float(prior_revenue) != 0
                    else None
                )
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
