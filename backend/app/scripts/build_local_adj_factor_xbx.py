"""Administrator CLI: derive local XBX adjustment factors."""
from __future__ import annotations

import argparse
import json

from app.config import settings
from app.services.local_adj_factor_xbx import LocalXbxAdjFactorBuilder


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local XBX adjustment factors from kline_daily_xbx")
    parser.add_argument("--dry-run", action="store_true", help="validate and report without writing data")
    args = parser.parse_args()
    summary = LocalXbxAdjFactorBuilder(settings.data_dir).run(dry_run=args.dry_run)
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
