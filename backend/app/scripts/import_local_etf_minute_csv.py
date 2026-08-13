"""Import administrator-provided ETF minute CSV files."""
from __future__ import annotations

import argparse
import json

from app.config import settings
from app.services.local_etf_minute_import import LocalEtfMinuteCsvImporter, refresh_etf_minute_view
from app.tickflow.repository import DataStore, KlineRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local ETF minute CSV files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not settings.local_etf_minute_csv_dir:
        parser.error("Set LOCAL_ETF_MINUTE_CSV_DIR first")
    summary = LocalEtfMinuteCsvImporter(settings.local_etf_minute_csv_dir, settings.data_dir).run(dry_run=args.dry_run)
    if not args.dry_run:
        refresh_etf_minute_view(KlineRepository(DataStore(settings.data_dir)))
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
