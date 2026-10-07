"""Command line. Run `python -m topstepbot --help`."""

from __future__ import annotations

import argparse
from pathlib import Path

from topstepbot.backtest.runner import run_backtest
from topstepbot.config import load_config
from topstepbot.live import run_paper, run_practice


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="topstepbot",
        description="Supervised MES day bot for a Topstep practice or combine account. Educational software, not financial advice.",
    )
    parser.add_argument("--config", default="config/settings.yaml", help="Path to the settings file")
    sub = parser.add_subparsers(dest="command", required=True)

    backtest = sub.add_parser("backtest", help="Replay a 1-minute CSV through the strategy")
    backtest.add_argument("--csv", default="data/sample_mes_1m_synthetic.csv")

    paper = sub.add_parser("paper", help="Replay a CSV on the paper broker and watch the kill switch")
    paper.add_argument("--csv", default="data/sample_mes_1m_synthetic.csv")
    paper.add_argument("--speed", type=float, default=0.0, help="Seconds to pause between bars. Use 0.2 or more if you want time to type kill.")
    paper.add_argument("--arm", action="store_true", help="Allow paper entries. Without this, signals are logged and no paper orders are sent.")

    practice = sub.add_parser("practice", help="Trade a Topstep Practice or Combine account through ProjectX")
    practice.add_argument("--arm", action="store_true", help="You are at this PC and you accept responsibility for orders.")

    download = sub.add_parser("download-bars", help="Download MES 1-minute bars to a CSV. Requires .env credentials.")
    download.add_argument("--start", required=True, help="ISO date or timestamp, Central time if no timezone")
    download.add_argument("--end", required=True, help="ISO date or timestamp")
    download.add_argument("--out", default="data/downloads/mes_1m.csv")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    armed = bool(config.runtime.armed or getattr(args, "arm", False))

    if args.command == "backtest":
        report = run_backtest(config, args.csv)
        print(report.text(config))
        return 0
    if args.command == "paper":
        run_paper(config, args.csv, args.speed, armed=armed)
        return 0
    if args.command == "practice":
        run_practice(config, armed=armed)
        return 0
    if args.command == "download-bars":
        from topstepbot.download import download_bars

        count = download_bars(config, args.start, args.end, Path(args.out))
        print(f"Wrote {count} bars to {args.out}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
