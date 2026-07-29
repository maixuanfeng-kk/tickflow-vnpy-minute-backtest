"""Administrator command for importing professional local daily CSV data."""
from __future__ import annotations

import argparse
import json
import sys

from app.config import settings
from app.services.local_daily_pro_import import LocalDailyProCsvImporter


def main() -> int:
    parser = argparse.ArgumentParser(description="导入本地年度专业日K CSV 到 kline_daily_pro")
    parser.add_argument("--dry-run", action="store_true", help="只校验和统计，不写入 Parquet")
    parser.add_argument("--years", nargs="+", type=int, help="仅导入指定年份，例如 --years 2025 2026")
    parser.add_argument("--batch-size", type=int, default=100, help="每批读取的 CSV 文件数")
    args = parser.parse_args()
    if not settings.local_daily_pro_csv_dir:
        parser.error("请先在 backend/.env 设置 LOCAL_DAILY_PRO_CSV_DIR")

    importer = LocalDailyProCsvImporter(
        settings.local_daily_pro_csv_dir,
        settings.data_dir,
        years=args.years,
        batch_size=args.batch_size,
        progress=lambda current, total, stage: print(f"[{stage}] {current}/{total}", flush=True),
    )
    try:
        print(json.dumps(importer.run(dry_run=args.dry_run).to_dict(), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"专业日K导入失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
