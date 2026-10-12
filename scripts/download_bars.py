#!/usr/bin/env python3
"""Download MES 1-minute bars from ProjectX into a CSV.

Example:
    python scripts/download_bars.py --start 2026-01-05 --end 2026-01-07 --out data/downloads/mes_1m.csv

The CSV format is the same one the backtester reads. See the README.
This script sends no orders.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from topstepbot.config import load_config
from topstepbot.download import download_bars


def main() -> int:
    parser = argparse.ArgumentParser(description="Download MES 1-minute bars. Does not place orders.")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--start", required=True, help="Start date or timestamp. Naive times are Central time.")
    parser.add_argument("--end", required=True, help="End date or timestamp.")
    parser.add_argument("--out", default="data/downloads/mes_1m.csv")
    args = parser.parse_args()
    count = download_bars(load_config(args.config), args.start, args.end, Path(args.out))
    print(f"Wrote {count} bars to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
