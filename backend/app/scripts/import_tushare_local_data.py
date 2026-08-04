"""Administrator CLI for importing local Tushare export datasets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import settings
from app.services.tushare_import import TushareLocalImporter


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local Tushare research exports into isolated Parquet datasets")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--datasets", nargs="+", choices=["names", "daily", "minute", "financials", "factors", "all"], default=["all"])
    parser.add_argument("--market-years", nargs="+", type=int, default=[2025, 2026])
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    requested = {"names", "daily", "minute", "financials", "factors"} if "all" in args.datasets else set(args.datasets)
    importer = TushareLocalImporter(args.source_root, settings.data_dir, batch_size=args.batch_size, progress=print)
    output = []
    if "names" in requested:
        output.append(importer.import_names(dry_run=args.dry_run).to_dict())
    if "daily" in requested:
        output.append(importer.import_daily(args.market_years, dry_run=args.dry_run).to_dict())
    if "financials" in requested:
        output.append(importer.import_financials(dry_run=args.dry_run).to_dict())
    if "minute" in requested:
        output.append(importer.import_minutes(args.market_years, dry_run=args.dry_run).to_dict())
    if "factors" in requested:
        output.append(importer.build_adjustment_factors(dry_run=args.dry_run).to_dict())
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
