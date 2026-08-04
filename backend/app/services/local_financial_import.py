"""Archive local XBX financial CSV files and build a point-in-time income projection."""
from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl


@dataclass
class LocalFinancialImportSummary:
    status: str
    dry_run: bool
    source_label: str
    files_discovered: int = 0
    files_imported: int = 0
    rows_read: int = 0
    raw_batches: int = 0
    income_rows: int = 0
    skipped_files: list[dict[str, str]] = field(default_factory=list)
    failed_files: list[dict[str, str]] = field(default_factory=list)
    imported_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LocalFinancialCsvImporter:
    """Full-fidelity XBX archive plus the narrow income table used by stock pools."""

    def __init__(
        self,
        source_dir: Path,
        data_dir: Path,
        *,
        batch_size: int = 50,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.source_dir = Path(source_dir)
        self.data_dir = Path(data_dir)
        self.batch_size = max(1, batch_size)
        self.progress = progress

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "user_data" / "local_financial_import.json"

    def run(self, *, dry_run: bool = False) -> LocalFinancialImportSummary:
        source_dir = self.source_dir.resolve()
        if not source_dir.is_dir():
            raise ValueError(f"LOCAL_FINANCIAL_CSV_DIR 不是可访问目录: {source_dir}")
        files = sorted(source_dir.rglob("*.csv"))
        summary = LocalFinancialImportSummary(
            status="preview" if dry_run else "running", dry_run=dry_run,
            source_label=source_dir.name, files_discovered=len(files),
        )
        if not files:
            raise ValueError("未找到财务 CSV 文件")

        stage_root = self.data_dir / ".local-financial-import-staging" / uuid4().hex
        try:
            for batch_index, start in enumerate(range(0, len(files), self.batch_size)):
                frames = [self._read_file(path, source_dir, summary) for path in files[start:start + self.batch_size]]
                valid = [frame for frame in frames if frame is not None and not frame.is_empty()]
                if valid and not dry_run:
                    batch = pl.concat(valid, how="diagonal_relaxed")
                    self._stage_batch(batch, stage_root, batch_index)
                    summary.raw_batches += 1
                self._report(min(start + self.batch_size, len(files)), len(files), "解析本地财务 CSV")
            if not dry_run:
                summary.income_rows = self._publish(stage_root)
                summary.status = "succeeded"
                summary.imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                self._write_manifest(summary)
            return summary
        except Exception as exc:
            summary.status = "failed"
            self._record(summary.failed_files, "<import>", str(exc))
            if not dry_run:
                summary.imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                self._write_manifest(summary)
            raise
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)
            try:
                stage_root.parent.rmdir()
            except OSError:
                pass

    def _read_file(self, path: Path, source_dir: Path, summary: LocalFinancialImportSummary) -> pl.DataFrame | None:
        try:
            try:
                raw = pl.read_csv(
                    path, skip_rows=1, infer_schema_length=500, null_values=["", "None", "nan"],
                    truncate_ragged_lines=True, encoding="utf8",
                )
            except Exception as utf8_error:  # Some archive files are GBK encoded.
                try:
                    raw = pl.read_csv(
                        path, skip_rows=1, infer_schema_length=500, null_values=["", "None", "nan"],
                        truncate_ragged_lines=True, encoding="gbk",
                    )
                except Exception as gbk_error:  # noqa: BLE001
                    raise ValueError(f"UTF-8: {utf8_error}; GBK: {gbk_error}") from gbk_error
        except Exception as exc:  # noqa: BLE001
            self._record(summary.failed_files, str(path.relative_to(source_dir)), f"CSV 读取失败: {exc}")
            return None
        required = {"stock_code", "report_date", "publish_date", "statement_format"}
        missing = required - set(raw.columns)
        if missing:
            self._record(summary.failed_files, str(path.relative_to(source_dir)), f"缺少字段: {', '.join(sorted(missing))}")
            return None
        summary.rows_read += raw.height
        summary.files_imported += 1
        return raw.with_columns(pl.lit(path.relative_to(source_dir).as_posix()).alias("source_file"))

    def _stage_batch(self, raw: pl.DataFrame, stage_root: Path, batch_index: int) -> None:
        raw_path = stage_root / "raw_xbx" / f"batch={batch_index:05d}" / "part.parquet"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw.write_parquet(raw_path)
        income = self._income_projection(raw)
        income_path = stage_root / "income" / f"batch={batch_index:05d}.parquet"
        income_path.parent.mkdir(parents=True, exist_ok=True)
        income.write_parquet(income_path)

    @staticmethod
    def _income_projection(raw: pl.DataFrame) -> pl.DataFrame:
        def col_or_null(name: str) -> pl.Expr:
            return pl.col(name).cast(pl.Float64, strict=False) if name in raw.columns else pl.lit(None, dtype=pl.Float64)

        code = pl.col("stock_code").cast(pl.Utf8).str.strip_chars().str.to_lowercase()
        symbol = (
            pl.when(code.str.contains(r"^(sh|sz|bj)\d{6}$"))
            .then(pl.concat_str([code.str.slice(2), pl.lit("."), code.str.slice(0, 2).str.to_uppercase()]))
            .when(code.str.contains(r"^\d{6}\.(sh|sz|bj)$"))
            .then(code.str.to_uppercase())
            .otherwise(pl.lit(None, dtype=pl.Utf8))
        )
        report_text = pl.col("report_date").cast(pl.Utf8).str.strip_chars()
        announce_text = pl.col("publish_date").cast(pl.Utf8).str.strip_chars()
        revenue_primary = col_or_null("R_revenue@xbx")
        revenue_fallback = col_or_null("R_operating_total_revenue@xbx")
        return raw.select(
            symbol.alias("symbol"),
            pl.coalesce([
                report_text.str.strptime(pl.Date, "%Y%m%d", strict=False),
                report_text.str.strptime(pl.Date, "%Y-%m-%d", strict=False),
            ]).alias("period_end"),
            pl.coalesce([
                announce_text.str.strptime(pl.Date, "%Y-%m-%d", strict=False),
                announce_text.str.strptime(pl.Date, "%Y%m%d", strict=False),
            ]).alias("announce_date"),
            pl.coalesce([revenue_primary, revenue_fallback]).alias("revenue"),
            pl.when(revenue_primary.is_not_null()).then(pl.lit("R_revenue@xbx"))
            .when(revenue_fallback.is_not_null()).then(pl.lit("R_operating_total_revenue@xbx"))
            .otherwise(pl.lit(None, dtype=pl.Utf8)).alias("revenue_source"),
            col_or_null("R_np@xbx").alias("net_income"),
            pl.col("statement_format").cast(pl.Utf8).alias("statement_format"),
            pl.col("source_file").cast(pl.Utf8).alias("source_file"),
        ).filter(
            pl.col("symbol").is_not_null() & pl.col("period_end").is_not_null() & pl.col("announce_date").is_not_null()
        )

    def _publish(self, stage_root: Path) -> int:
        raw_stage = stage_root / "raw_xbx"
        income_paths = sorted((stage_root / "income").glob("batch=*.parquet"))
        if not income_paths:
            raise ValueError("没有可发布的标准财务利润表记录")
        income = pl.concat([pl.read_parquet(path) for path in income_paths], how="vertical_relaxed").sort("source_file").unique(
            subset=["symbol", "period_end", "announce_date"], keep="last", maintain_order=True
        ).sort(["symbol", "period_end", "announce_date"])
        income_directory = self.data_dir / "financials" / "income"
        income_directory.mkdir(parents=True, exist_ok=True)
        income_temporary = income_directory / "part.tmp"
        income_final = income_directory / "part.parquet"
        income.write_parquet(income_temporary)
        income_temporary.replace(income_final)
        self._swap_raw_archive(raw_stage)
        metadata = {
            "dataset": "financials/raw_xbx", "version": 1,
            "income_projection": ["symbol", "period_end", "announce_date", "revenue", "revenue_source", "net_income", "statement_format"],
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        metadata_path = self.data_dir / "financials" / "raw_xbx" / "_metadata.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return income.height

    def _swap_raw_archive(self, raw_stage: Path) -> None:
        target = self.data_dir / "financials" / "raw_xbx"
        backup = target.with_name(f".raw_xbx_backup_{uuid4().hex}")
        if target.exists():
            target.replace(backup)
        try:
            raw_stage.replace(target)
        except Exception:
            if backup.exists():
                backup.replace(target)
            raise
        shutil.rmtree(backup, ignore_errors=True)

    def _write_manifest(self, summary: LocalFinancialImportSummary) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)

    def _report(self, current: int, total: int, stage: str) -> None:
        if self.progress:
            self.progress(current, total, stage)

    @staticmethod
    def _record(records: list[dict[str, str]], filename: str, reason: str) -> None:
        if len(records) < 100:
            records.append({"file": filename, "reason": reason})
