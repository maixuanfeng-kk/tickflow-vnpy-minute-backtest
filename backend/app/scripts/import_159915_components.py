"""Import 159915 component snapshots into date-partitioned parquet."""
from __future__ import annotations

import argparse
from pathlib import Path

from app.vnpy_backtest.etf_component_data import import_component_snapshots


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    counts = import_component_snapshots(args.source, args.data_dir)
    print(f"imported {len(counts)} snapshots, {sum(counts.values())} rows")


if __name__ == "__main__":
    main()
