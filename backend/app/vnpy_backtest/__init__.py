"""Optional vn.py CTA strategies and local-data backtest helpers."""
"""vn.py integration helpers.

vn.py falls back to ``Path.home()/.vntrader`` when the working directory lacks
this folder.  Creating it locally before any vn.py import keeps a web service or
test run from unexpectedly writing to a user's home directory.
"""
from pathlib import Path

Path.cwd().joinpath(".vntrader").mkdir(parents=True, exist_ok=True)
