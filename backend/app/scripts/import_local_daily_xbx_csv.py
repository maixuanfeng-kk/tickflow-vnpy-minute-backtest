"""Administrator command for XBX historical daily CSV import."""
from __future__ import annotations

import argparse
import json
import sys

from app.config import settings
from app.services.local_daily_xbx_import import LocalDailyXbxCsvImporter


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 XBX 本地日K CSV 到 kline_daily_xbx")
    parser.add_argument("--dry-run", action="store_true", help="只校验和统计，不写入 Parquet")
    parser.add_argument("--batch-size", type=int, default=100, help="每批读取的 CSV 文件数")
    args = parser.parse_args()
    if not settings.local_daily_xbx_csv_dir:
        parser.error("请先在 backend/.env 设置 LOCAL_DAILY_XBX_CSV_DIR")
    try:
        importer = LocalDailyXbxCsvImporter(settings.local_daily_xbx_csv_dir, settings.data_dir, batch_size=args.batch_size, progress=lambda current, total, stage: print(f"[{stage}] {current}/{total}", flush=True))
        print(json.dumps(importer.run(dry_run=args.dry_run).to_dict(), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"XBX 日K导入失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
