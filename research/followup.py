"""MNQ extras: ES/NQ correlation, four cross rules, event days, combine.

The 63-rule MNQ screen is `python -m research.run --instrument MNQ`.
This module does not change that menu and does not touch live settings.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from research.calendar import TRAIN_END, VAL_END, split_days
from research.combine import combine_rows, session_rows, summarize_attempts
from research.costs import MES, MNQ, using_instrument
from research.cross import RULES, describe_pair, score_cross
from research.events import tag_sessions
from research.stats import Trade, summarize
from research.strategies import all_specs, run_days


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, np.integer):
        return int(value)
    return value


def _spec(name: str):
    return next(item for item in all_specs() if item.name == name)


def _run(days, name: str, instrument) -> list[Trade]:
    with using_instrument(instrument):
        return run_days(days, _spec(name), instrument=instrument)


def _on(trades, predicate) -> list[Trade]:
    return [trade for trade in trades if predicate(trade.session)]


def _support(train_all, train_off, val_all, val_off) -> bool:
    """Predeclared: skipping red days is supported only if both windows improve."""
    if train_off["trades"] < 40 or val_off["trades"] < 40:
        return False
    return (
        train_off["expectancy"] > train_all["expectancy"]
        and train_off["net"] > 0
        and val_off["expectancy"] > val_all["expectancy"]
        and val_off["net"] > 0
    )


def _combine(trades, sessions, censor, drop: set | None = None):
    kept = trades if not drop else [trade for trade in trades if trade.session not in drop]
    if not sessions:
        return None
    return summarize_attempts(session_rows(kept, sessions), censor)


def _pair_combine(mes_trades, mnq_trades, sessions, censor, drop: set | None = None):
    if not sessions:
        return None
    if drop:
        mes_trades = [trade for trade in mes_trades if trade.session not in drop]
        mnq_trades = [trade for trade in mnq_trades if trade.session not in drop]
    rows = combine_rows(
        [session_rows(mes_trades, sessions), session_rows(mnq_trades, sessions)],
        [0.5, 0.5],
    )
    return summarize_attempts(rows, censor)


def _event_block(trades, sessions, tags) -> dict:
    def has(name):
        return _on(trades, lambda day: name in tags[day])

    def lacks(name):
        return _on(trades, lambda day: name not in tags[day])

    out = {"all": summarize(trades), "red": summarize(has("red")), "not_red": summarize(lacks("red"))}
    for name in (
        "nfp",
        "cpi",
        "ppi",
        "fomc",
        "fomc_notation",
        "ism_mfg",
        "ism_svc",
        "opex",
        "quad",
        "roll_week",
        "half_day",
        "month_end",
        "vix_high",
        "vix_low",
    ):
        out[name] = {"on": summarize(has(name)), "off": summarize(lacks(name))}
    out["support_red_skip"] = _support(out["all"], out["not_red"], out["all"], out["not_red"])
    # The line above compares a split with itself. Real support is filled by the caller
    # once train and validation are both known. Leave the placeholder false.
    out["support_red_skip"] = False
    out["sessions"] = sessions
    return out


def event_study(books: dict[str, list[Trade]], sessions: list) -> dict:
    tags = tag_sessions(sessions)
    splits = {
        "train": [day for day in sessions if day <= TRAIN_END],
        "validation": [day for day in sessions if TRAIN_END < day <= VAL_END],
        "holdout": [day for day in sessions if day > VAL_END],
    }
    report = {}
    for name, trades in books.items():
        report[name] = {}
        for label, days in splits.items():
            day_set = set(days)
            subset = [trade for trade in trades if trade.session in day_set]
            block = _event_block(subset, days, tags)
            block.pop("sessions")
            report[name][label] = block
        report[name]["red_skip_supported"] = _support(
            report[name]["train"]["all"],
            report[name]["train"]["not_red"],
            report[name]["validation"]["all"],
            report[name]["validation"]["not_red"],
        )
    red = {day for day, marks in tags.items() if "red" in marks}
    vix_bucket = {}
    for day, marks in tags.items():
        for label in ("vix_low", "vix_mid", "vix_high"):
            if label in marks:
                vix_bucket[day] = label
    return {"books": report, "red": red, "vix_bucket": vix_bucket, "tags": tags}


def write_charts(pair: dict, directory: Path) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    rolling = [row for row in pair["rolling_60"] if row["corr"] is not None]
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(range(len(rolling)), [row["corr"] for row in rolling], color="#3d6f99", linewidth=1.1)
    ax.axhline(pair["daily_corr"] or 0, color="#444444", linewidth=0.8, linestyle="--")
    ax.set_title("60-session correlation of MES and MNQ cash-session returns")
    ax.set_ylabel("Correlation")
    ax.set_ylim(-0.2, 1.05)
    step = max(1, len(rolling) // 8)
    ax.set_xticks(range(0, len(rolling), step), [rolling[i]["session"][0:7] for i in range(0, len(rolling), step)])
    fig.tight_layout()
    paths["rolling"] = directory / "es_nq_rolling_corr.png"
    fig.savefig(paths["rolling"], dpi=120)
    plt.close(fig)

    lags = pair["lead_lag"]
    order = [str(i) for i in range(-5, 6)]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(order, [lags[key]["corr"] or 0 for key in order], color="#e07a3d")
    ax.axhline(0, color="#444444", linewidth=0.7)
    ax.set_title("1-minute lead-lag. Positive lag means MNQ's later minute")
    ax.set_xlabel("Lag of MNQ relative to MES (minutes)")
    ax.set_ylabel("Correlation")
    fig.tight_layout()
    paths["lag"] = directory / "es_nq_lead_lag.png"
    fig.savefig(paths["lag"], dpi=120)
    plt.close(fig)

    clocks = pair["by_clock"]
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.bar([row["clock"] for row in clocks], [0 if row["corr"] is None else row["corr"] for row in clocks], color="#3d6f99")
    ax.set_title("Correlation of 1-minute MES and MNQ returns by half hour")
    ax.tick_params(axis="x", labelrotation=90)
    ax.set_ylabel("Correlation")
    ax.set_ylim(0.85, 1.0)
    fig.tight_layout()
    paths["clock"] = directory / "es_nq_corr_by_time.png"
    fig.savefig(paths["clock"], dpi=120)
    plt.close(fig)

    series = pair["ratio"]["series"]
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(range(len(series)), [row["ratio"] for row in series], color="#3d6f99", linewidth=1.0)
    ax.set_title("MNQ close / MES close at the cash close")
    ax.set_ylabel("Ratio")
    step = max(1, len(series) // 8)
    ax.set_xticks(range(0, len(series), step), [series[i]["session"][0:7] for i in range(0, len(series), step)])
    fig.tight_layout()
    paths["ratio"] = directory / "es_nq_ratio.png"
    fig.savefig(paths["ratio"], dpi=120)
    plt.close(fig)
    return paths


def _public_cross(scored: dict) -> dict:
    out = {}
    for name, block in scored.items():
        out[name] = {
            "candidate": block["candidate"],
            "pvalue": block["pvalue"],
            "stats": block["stats"],
        }
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="ES/NQ correlation, cross rules, and event tags")
    parser.add_argument("--mes-early", required=True)
    parser.add_argument("--mes-late", required=True)
    parser.add_argument("--mnq-early", required=True)
    parser.add_argument("--mnq-late", required=True)
    parser.add_argument("--out", default="docs/research/followup.json")
    args = parser.parse_args(argv)
    from research.market import load_days

    print("loading MES", flush=True)
    mes_days, mes_summary = load_days([args.mes_early, args.mes_late])
    print("loading MNQ", flush=True)
    mnq_days, mnq_summary = load_days([args.mnq_early, args.mnq_late])
    mes_sessions = [day.session for day in mes_days]
    mnq_sessions = [day.session for day in mnq_days]
    matched = sorted(set(mes_sessions) & set(mnq_sessions))
    tags_pack = event_study({}, matched)
    print("pair description", flush=True)
    pair = describe_pair(mes_days, mnq_days, tags_pack["vix_bucket"])
    print("cross rules", flush=True)
    scored = score_cross(mes_days, mnq_days)
    print("event books", flush=True)
    books = {}
    for instrument, days, code in ((MES, mes_days, "mes"), (MNQ, mnq_days, "mnq")):
        for name in ("fh_15bp_1.5R", "orb_15_opposite_1R"):
            print(f"  {code} {name}", flush=True)
            books[f"{code}:{name}"] = _run(days, name, instrument)
    for name in ("orb_15_opposite_2R", "orb_30_opposite_1R"):
        print(f"  mnq {name}", flush=True)
        books[f"mnq:{name}"] = _run(mnq_days, name, MNQ)
    events = event_study(books, matched)
    red = events["red"]
    mes_split = dict(zip(("train", "validation", "holdout"), split_days(mes_days)))
    mnq_split = dict(zip(("train", "validation", "holdout"), split_days(mnq_days)))
    combine = {}
    censor = {"train": TRAIN_END, "validation": VAL_END, "holdout": matched[-1]}
    for key in ("mes:fh_15bp_1.5R", "mnq:fh_15bp_1.5R", "mnq:orb_15_opposite_2R", "mnq:orb_30_opposite_1R"):
        market = mes_split if key.startswith("mes:") else mnq_split
        combine[key] = {}
        supported = events["books"][key]["red_skip_supported"]
        for label, days in market.items():
            sessions = [day.session for day in days]
            combine[key][label] = _combine(books[key], sessions, censor[label])
            if supported:
                combine[key][f"{label}_skip_red"] = _combine(books[key], sessions, censor[label], red)
    pair_key = "pair:fh_15bp_1.5R"
    combine[pair_key] = {}
    for label in ("train", "validation", "holdout"):
        sessions = [day.session for day in mes_split[label] if day.session in set(mnq_sessions)]
        combine[pair_key][label] = _pair_combine(
            books["mes:fh_15bp_1.5R"], books["mnq:fh_15bp_1.5R"], sessions, censor[label]
        )
    # Both first-hour books have to improve on train and on validation.
    pair_supported = (
        events["books"]["mes:fh_15bp_1.5R"]["red_skip_supported"]
        and events["books"]["mnq:fh_15bp_1.5R"]["red_skip_supported"]
    )
    if pair_supported:
        for label in ("train", "validation", "holdout"):
            sessions = [day.session for day in mes_split[label] if day.session in set(mnq_sessions)]
            combine[pair_key][f"{label}_skip_red"] = _pair_combine(
                books["mes:fh_15bp_1.5R"], books["mnq:fh_15bp_1.5R"], sessions, censor[label], red
            )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    charts = write_charts(pair, out.parent)
    pair_public = dict(pair)
    # The chart already used the daily ratio path. Keep the yearly and rolling series.
    payload = {
        "mes": mes_summary,
        "mnq": mnq_summary,
        "matched_sessions": len(matched),
        "pair": pair_public,
        "cross": _public_cross(scored),
        "events": events["books"],
        "combine": combine,
        "pair_skip_supported": pair_supported,
        "red_sessions": len(red),
    }
    # Drop the long ratio path from the json after the chart is written. Rolling stays.
    payload["pair"]["ratio"] = {key: value for key, value in pair["ratio"].items() if key != "series"}
    out.write_text(json.dumps(_jsonable(payload), indent=2))
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    for path in charts.values():
        target = artifacts / path.name
        target.write_bytes(path.read_bytes())
    print(f"wrote {out}", flush=True)
    print("cross", {name: (block["candidate"], block["pvalue"]) for name, block in scored.items()}, flush=True)
    print("red skip", {name: block["red_skip_supported"] for name, block in events["books"].items()}, flush=True)


if __name__ == "__main__":
    main()
