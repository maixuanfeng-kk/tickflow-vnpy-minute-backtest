"""Administrator command for importing local XBX financial CSV files."""
from __future__ import annotations

import argparse
import json
import sys

from app.config import settings
from app.services.local_financial_import import LocalFinancialCsvImporter


def main() -> int:
    parser = argparse.ArgumentParser(description="导入本地财务 CSV 并生成股票池标准财务表")
    parser.add_argument("--dry-run", action="store_true", help="只校验和统计，不写入 Parquet")
    parser.add_argument("--batch-size", type=int, default=50, help="每批读取的 CSV 文件数")
    args = parser.parse_args()
    if not settings.local_financial_csv_dir:
        parser.error("请先在 backend/.env 设置 LOCAL_FINANCIAL_CSV_DIR")
    importer = LocalFinancialCsvImporter(
        settings.local_financial_csv_dir, settings.data_dir, batch_size=args.batch_size,
        progress=lambda current, total, stage: print(f"[{stage}] {current}/{total}", flush=True),
    )
    try:
        print(json.dumps(importer.run(dry_run=args.dry_run).to_dict(), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"财务数据导入失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
