"""Command line. Run `python -m topstepbot --help`."""

from __future__ import annotations

import argparse
from pathlib import Path

from topstepbot.backtest.runner import run_backtest
from topstepbot.check import run_check
from topstepbot.config import load_config
from topstepbot.dashboard import serve_dashboard
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

    check = sub.add_parser(
        "check",
        help="Read-only ProjectX connection check. Never places, modifies, or cancels orders.",
    )
    check.add_argument(
        "--no-signalr",
        action="store_true",
        help="Skip the market-hub quote listen. The REST checks still run.",
    )
    check.add_argument(
        "--signalr-seconds",
        type=float,
        default=20,
        help="How long to listen for MES quotes (default 20).",
    )

    dashboard = sub.add_parser(
        "dashboard",
        help="Open the command center in your browser. It does not place orders.",
    )
    dashboard.add_argument("--port", type=int, default=8765, help="Port on this computer only (default 8765).")
    dashboard.add_argument("--no-browser", action="store_true", help="Do not open the browser window.")

    download = sub.add_parser("download-bars", help="Download MES 1-minute bars to a CSV. Requires .env credentials.")
    download.add_argument("--start", required=True, help="ISO date or timestamp, Central time if no timezone")
    download.add_argument("--end", required=True, help="ISO date or timestamp")
    download.add_argument("--out", default="data/downloads/mes_1m.csv")

    fetch = sub.add_parser(
        "fetch-history",
        help="Download MES 1-minute history with the read-only client. Cannot place orders.",
    )
    fetch.add_argument("--days", type=int, default=365, help="How many days back to ask for (default 365).")
    fetch.add_argument("--out", default="data/mes_1m_real.csv", help="CSV path. Resume-safe if the file already exists.")

    history = sub.add_parser(
        "history-report",
        help="Run the live strategy on a 1-minute CSV and write the history report. Does not change settings.",
    )
    history.add_argument("--csv", default="data/mes_1m_real.csv")
    history.add_argument("--out", default="logs/history-report.txt")
    history.add_argument("--label", default="", help="Optional first line, for a preliminary public-data file.")

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
    if args.command == "check":
        return run_check(
            config,
            skip_signalr=args.no_signalr,
            signalr_seconds=args.signalr_seconds,
        )
    if args.command == "dashboard":
        serve_dashboard(config, port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "download-bars":
        from topstepbot.download import download_bars

        count = download_bars(config, args.start, args.end, Path(args.out))
        print(f"Wrote {count} bars to {args.out}")
        return 0
    if args.command == "fetch-history":
        from topstepbot.historyfetch import fetch_history

        fetch_history(days=args.days, out=args.out)
        return 0
    if args.command == "history-report":
        from topstepbot.historyreport import write_history_report

        print(write_history_report(config, args.csv, out=args.out, label=args.label))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
