"""Server administrator command for importing LOCAL_MINUTE_CSV_DIR."""
from __future__ import annotations

import argparse
import json
import sys

from app.config import settings
from app.services.local_minute_import import LocalMinuteCsvImporter, refresh_minute_view
from app.tickflow.repository import DataStore, KlineRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 .env 配置的本地分钟 CSV 到 TickFlow 标准库")
    parser.add_argument("--dry-run", action="store_true", help="只校验并统计，不写 Parquet 或导入清单")
    parser.add_argument("--batch-size", type=int, default=20, help="每批读取的 CSV 文件数，默认 20")
    args = parser.parse_args()

    if not settings.local_minute_csv_dir:
        parser.error("请先在 backend/.env 设置 LOCAL_MINUTE_CSV_DIR=你的CSV目录")

    def progress(current: int, total: int, stage: str) -> None:
        print(f"[{stage}] {current}/{total}", flush=True)

    importer = LocalMinuteCsvImporter(
        settings.local_minute_csv_dir,
        settings.data_dir,
        batch_size=args.batch_size,
        progress=progress,
    )
    try:
        summary = importer.run(dry_run=args.dry_run)
        if not args.dry_run:
            store = DataStore(settings.data_dir)
            refresh_minute_view(KlineRepository(store))
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"本地分钟 CSV 导入失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
