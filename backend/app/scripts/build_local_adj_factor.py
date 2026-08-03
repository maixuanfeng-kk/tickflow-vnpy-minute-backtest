"""Administrator command to derive standard local adjustment factors."""
from __future__ import annotations

import argparse
import json

from app.config import settings
from app.services.local_adj_factor import LocalAdjFactorBuilder


def main() -> int:
    parser = argparse.ArgumentParser(description="Build adj_factor from standard kline_daily pre_close")
    parser.add_argument("--dry-run", action="store_true", help="validate and report without writing Parquet")
    args = parser.parse_args()
    summary = LocalAdjFactorBuilder(settings.data_dir).run(dry_run=args.dry_run)
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
