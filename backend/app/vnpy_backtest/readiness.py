"""Local data readiness checks for the 159915 ETF minute strategy."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path

import polars as pl

from app.tickflow.etf_datasets import ETF_DAILY_DATASET, ETF_MINUTE_DATASET


class Etf159915ReadinessService:
    EXPECTED_MINUTE_ROWS = 241
    REQUIRED_WARMUP_DAYS = 10
    SAMPLE_LIMIT = 20

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def check(self, *, symbol: str, start: date, end: date) -> dict[str, object]:
        blocking: list[str] = []
        warnings: list[str] = []
        daily, daily_error = self._load_daily(symbol)
        if daily_error:
            blocking.append(daily_error)
        daily_coverage = self._daily_coverage(daily, start, end)
        required_days = [
            *daily_coverage["warmup_required_days"],
            *daily_coverage["requested_days"],
        ]
        minute_start = date.fromisoformat(required_days[0]) if required_days else start
        minute, minute_error = self._load_minute(symbol, minute_start, end)
        if minute_error:
            blocking.append(minute_error)

        minute_coverage = self._minute_coverage(minute)

        if daily is not None:
            requested_days = daily_coverage["requested_days"]
            if not requested_days:
                blocking.append(f"{symbol} 在所选区间没有 ETF 日线数据。")
            if daily_coverage["warmup_days"] < self.REQUIRED_WARMUP_DAYS:
                blocking.append(
                    f"日线预热不足：至少需要 {self.REQUIRED_WARMUP_DAYS} 个预热交易日，"
                    f"当前只有 {daily_coverage['warmup_days']} 个。"
                )

        if minute is not None:
            available_days = set(minute_coverage["available_days"])
            missing_days = [day for day in required_days if day not in available_days]
            minute_coverage["missing_days"] = missing_days[: self.SAMPLE_LIMIT]
            minute_coverage["missing_day_count"] = len(missing_days)
            if missing_days:
                missing_warmup = set(daily_coverage["warmup_required_days"]) & set(missing_days)
                if missing_warmup:
                    blocking.append(f"分钟预热数据缺少 {len(missing_warmup)} 个交易日。")
                missing_requested = set(daily_coverage["requested_days"]) & set(missing_days)
                if missing_requested:
                    blocking.append(f"分钟数据缺少 {len(missing_requested)} 个交易日。")
            if minute_coverage["missing_open_days"]:
                blocking.append("分钟数据缺少关键的 09:30 开盘分钟线。")
            if minute_coverage["missing_close_days"]:
                blocking.append("分钟数据缺少关键的 15:00 执行分钟线。")
            if minute_coverage["sparse_days"]:
                warnings.append(
                    f"有 {minute_coverage['sparse_days']} 个交易日分钟线少于 "
                    f"{self.EXPECTED_MINUTE_ROWS} 根。"
                )
            if minute_coverage["missing_1459_days"]:
                warnings.append(
                    f"有 {len(minute_coverage['missing_1459_days'])} 个交易日缺少 14:59 分钟线。"
                )
            if minute_coverage["zero_volume_rows"]:
                warnings.append(
                    f"分钟数据包含 {minute_coverage['zero_volume_rows']} 根零成交量记录。"
                )

        return {
            "strategy_id": "etf_159915_minute",
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "ready": not blocking,
            "blocking_reasons": blocking,
            "warnings": warnings,
            "coverage": {"daily": daily_coverage, "minute": minute_coverage},
        }

    def _load_daily(self, symbol: str) -> tuple[pl.DataFrame | None, str | None]:
        root = self.data_dir / ETF_DAILY_DATASET
        if not root.exists():
            return None, "ETF 日线数据集不存在。"
        try:
            scan = pl.scan_parquet(str(root / "**" / "*.parquet"))
            columns = set(scan.collect_schema().names())
            required = {"symbol", "date", "open", "high", "low", "close", "pre_close"}
            if not required.issubset(columns):
                return None, "ETF 日线数据字段不完整。"
            frame = (
                scan.filter(pl.col("symbol") == symbol)
                .select(
                    pl.col("date").cast(pl.Date),
                    *[
                        pl.col(name).cast(pl.Float64)
                        for name in ("open", "high", "low", "close", "pre_close")
                    ],
                )
                .sort("date")
                .collect()
            )
            invalid = frame.filter(
                pl.any_horizontal(
                    *[
                        pl.col(name).is_null() | (pl.col(name) <= 0)
                        for name in ("open", "high", "low", "close", "pre_close")
                    ]
                )
            )
            if not invalid.is_empty():
                return None, "ETF 日线数据包含无效价格。"
            return frame, None
        except Exception:  # noqa: BLE001
            return None, "ETF 日线数据读取失败。"

    def _load_minute(
        self, symbol: str, start: date, end: date
    ) -> tuple[pl.DataFrame | None, str | None]:
        root = self.data_dir / ETF_MINUTE_DATASET
        if not root.exists():
            return None, "ETF 分钟数据集不存在。"
        try:
            required = {
                "symbol", "datetime", "open", "high", "low", "close", "volume", "amount",
            }
            for part in root.glob("date=*/part.parquet"):
                try:
                    trading_day = date.fromisoformat(part.parent.name.removeprefix("date="))
                except ValueError:
                    continue
                if start <= trading_day <= end and not required.issubset(
                    set(pl.read_parquet_schema(part))
                ):
                    return None, "ETF 分钟数据字段不完整。"
            scan = pl.scan_parquet(str(root / "**" / "*.parquet"))
            columns = set(scan.collect_schema().names())
            if not required.issubset(columns):
                return None, "ETF 分钟数据字段不完整。"
            frame = (
                scan.filter(
                    (pl.col("symbol") == symbol)
                    & (pl.col("datetime").dt.date() >= start)
                    & (pl.col("datetime").dt.date() <= end)
                )
                .select(
                    pl.col("datetime").cast(pl.Datetime),
                    pl.col("volume").cast(pl.Float64),
                )
                .sort("datetime")
                .collect()
            )
            return frame, None
        except Exception:  # noqa: BLE001
            return None, "ETF 分钟数据读取失败。"

    @staticmethod
    def _daily_coverage(frame: pl.DataFrame | None, start: date, end: date) -> dict[str, object]:
        dates = frame["date"].to_list() if frame is not None and not frame.is_empty() else []
        before = [value for value in dates if value < start]
        requested = [value for value in dates if start <= value <= end]
        required_warmup = before[-Etf159915ReadinessService.REQUIRED_WARMUP_DAYS :]
        return {
            "first_date": dates[0].isoformat() if dates else None,
            "last_date": dates[-1].isoformat() if dates else None,
            "requested_days": [value.isoformat() for value in requested],
            "requested_day_count": len(requested),
            "warmup_days": len(before),
            "warmup_required_days": [value.isoformat() for value in required_warmup],
        }

    def _minute_coverage(self, frame: pl.DataFrame | None) -> dict[str, object]:
        if frame is None or frame.is_empty():
            return {
                "first_datetime": None,
                "last_datetime": None,
                "available_days": [],
                "trading_days": 0,
                "row_count": 0,
                "sparse_days": 0,
                "missing_open_days": [],
                "missing_close_days": [],
                "missing_1459_days": [],
                "zero_volume_rows": 0,
            }
        frame = frame.with_columns(pl.col("datetime").dt.date().alias("trade_date"))
        by_day = (
            frame.group_by("trade_date")
            .agg(
                pl.len().alias("row_count"),
                pl.col("datetime").dt.time().alias("times"),
                (pl.col("volume") <= 0).sum().alias("zero_volume_rows"),
            )
            .sort("trade_date")
        )
        rows = by_day.to_dicts()
        return {
            "first_datetime": frame["datetime"].min().isoformat(),
            "last_datetime": frame["datetime"].max().isoformat(),
            "available_days": [row["trade_date"].isoformat() for row in rows],
            "trading_days": len(rows),
            "row_count": frame.height,
            "sparse_days": sum(row["row_count"] < self.EXPECTED_MINUTE_ROWS for row in rows),
            "missing_open_days": [
                row["trade_date"].isoformat()
                for row in rows
                if time(9, 30) not in row["times"]
            ],
            "missing_close_days": [
                row["trade_date"].isoformat()
                for row in rows
                if time(15, 0) not in row["times"]
            ],
            "missing_1459_days": [
                row["trade_date"].isoformat()
                for row in rows
                if time(14, 59) not in row["times"]
            ],
            "zero_volume_rows": sum(row["zero_volume_rows"] for row in rows),
        }
