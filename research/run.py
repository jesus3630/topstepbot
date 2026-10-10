"""Run the predeclared MES study.

The holdout split is scored only after select() returns finalists, and only
for those names. python -m research.run --early a.csv.gz --late b.csv.gz
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from itertools import combinations
from pathlib import Path

import numpy as np

from research.calendar import TRAIN_END, VAL_END, split_days
from research.combine import combine_rows, session_rows, summarize_attempts
from research.market import load_days
from research.profile import build_profile
from research.report import render, write_charts
from research.stats import (
    adjust,
    bootstrap_p,
    corr,
    daily_vector,
    failure_reasons,
    signflip_p,
    summarize,
)
from research.strategies import (
    all_specs,
    eligible_count,
    grid_text,
    make_random,
    neighbor_stable,
    neighbors_of,
    run_days,
    select,
)


def _dist(values) -> dict:
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(len(arr)),
        "mean": float(arr.mean()) if len(arr) else None,
        "median": float(np.median(arr)) if len(arr) else None,
        "p05": float(np.percentile(arr, 5)) if len(arr) else None,
        "p95": float(np.percentile(arr, 95)) if len(arr) else None,
        "share_positive": float(np.mean(arr > 0)) if len(arr) else None,
    }


def _span(days) -> dict:
    if not days:
        return {"n": 0, "first": "", "last": ""}
    return {"n": len(days), "first": days[0].session.isoformat(), "last": days[-1].session.isoformat()}


def _neighbor_note(spec, specs, val_stats) -> str:
    neigh = neighbors_of(spec, specs)
    if not neigh:
        return "no neighbor on the predeclared list"
    bad = [item.name for item in neigh if val_stats[item.name]["expectancy"] <= 0]
    detail = ", ".join(bad) if bad else "none"
    return f"{len(bad)} of {len(neigh)} ({detail})"


def _why(spec, train_stats, val_stats, specs, finalist_names) -> str:
    if spec.name in finalist_names:
        return "passed train, validation, and the neighbor rule"
    reasons = failure_reasons(train_stats[spec.name], val_stats[spec.name])
    if not reasons and not neighbor_stable(spec, specs, val_stats):
        reasons = ["more than half of the neighboring parameters had validation expectancy at or below zero"]
    if not reasons:
        reasons = ["passed the gates but lost the rank or the correlation cutoff"]
    return "; ".join(reasons)


def _pick_combo(finalists, train_daily):
    for size in (3, 2):
        if len(finalists) < size:
            continue
        for group in combinations(finalists, size):
            if all(corr(train_daily[a.name], train_daily[b.name]) < 0.3 for a, b in combinations(group, 2)):
                return list(group)
    return []


def _cumsum(series) -> list[float]:
    total = 0.0
    out = []
    for value in series:
        total += float(value)
        out.append(total)
    return out


def random_null(train_days, val_days) -> dict:
    train_exp = []
    val_exp = []
    for seed in range(1000, 1100):
        spec = make_random(seed)
        train_exp.append(summarize(run_days(train_days, spec))["expectancy"])
        val_exp.append(summarize(run_days(val_days, spec))["expectancy"])
        if seed % 20 == 0:
            print(f"null seed {seed}", flush=True)
    return {"train": train_exp, "val": val_exp}


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float) and value != value:
        return None
    return value


def _attempt_block(trades, sessions, censor):
    if not sessions:
        return None
    return summarize_attempts(session_rows(trades, sessions), censor)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Predeclared MES research study")
    parser.add_argument("--early", required=True)
    parser.add_argument("--late", required=True)
    parser.add_argument("--out", default="docs/RESEARCH.md")
    args = parser.parse_args(argv)

    out = Path(args.out)
    research_dir = out.parent / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    grid_path = research_dir / "predeclared_grid.txt"
    grid_path.write_text(grid_text())
    print(f"wrote {grid_path} before loading bars", flush=True)

    started = time.time()
    days, summary = load_days([args.early, args.late])
    print(f"loaded {summary} in {time.time() - started:.1f}s", flush=True)
    profile = build_profile(days)
    train, validation, holdout = split_days(days)
    print(
        f"splits train={len(train)} validation={len(validation)} holdout={len(holdout)}",
        flush=True,
    )
    specs = all_specs()
    train_trades = {}
    val_trades = {}
    loop = time.time()
    for index, spec in enumerate(specs, start=1):
        print(f"[{index}/{len(specs)}] {spec.name}", flush=True)
        train_trades[spec.name] = run_days(train, spec)
        val_trades[spec.name] = run_days(validation, spec)
    print(f"train/val done in {time.time() - loop:.1f}s", flush=True)

    train_stats = {name: summarize(trades) for name, trades in train_trades.items()}
    val_stats = {name: summarize(trades) for name, trades in val_trades.items()}
    train_sessions = [day.session for day in train]
    val_sessions = [day.session for day in validation]
    hold_sessions = [day.session for day in holdout]
    train_daily = {name: daily_vector(trades, train_sessions) for name, trades in train_trades.items()}
    val_daily = {name: daily_vector(trades, val_sessions) for name, trades in val_trades.items()}

    finalists = select(specs, train_stats, val_stats, val_daily)
    finalist_names = [spec.name for spec in finalists]
    print(f"finalists {finalist_names}", flush=True)

    hold_trades = {}
    hold_stats = {}
    hold_daily = {}
    if finalists:
        print("HOLDOUT START", flush=True)
        for spec in finalists:
            trades = run_days(holdout, spec)
            hold_trades[spec.name] = trades
            hold_stats[spec.name] = summarize(trades)
            hold_daily[spec.name] = daily_vector(trades, hold_sessions)

    print("random null", flush=True)
    null_raw = random_null(train, validation)
    null = {"train": _dist(null_raw["train"]), "val": _dist(null_raw["val"])}
    val_null = np.asarray(null_raw["val"], dtype=float)

    ranked = sorted(
        (spec for spec in specs if spec.eligible),
        key=lambda spec: (-val_stats[spec.name]["expectancy"], spec.name),
    )
    examined = []
    for spec in [item for item in ranked if item.name not in finalist_names][:5]:
        examined.append(
            {
                "name": spec.name,
                "reasons": _why(spec, train_stats, val_stats, specs, set(finalist_names)),
                "neighbor_note": _neighbor_note(spec, specs, val_stats),
                "null_ge": float(np.mean(val_null >= val_stats[spec.name]["expectancy"])) if len(val_null) else None,
            }
        )
    finalist_rows = [
        {
            "name": spec.name,
            "reasons": "passed train, validation, and the neighbor rule",
            "neighbor_note": _neighbor_note(spec, specs, val_stats),
        }
        for spec in finalists
    ]
    n_tests = eligible_count(specs)
    pvalues = {}
    for spec in finalists:
        raw = bootstrap_p(val_daily[spec.name])
        flip = signflip_p(val_daily[spec.name])
        pvalues[spec.name] = {
            "bootstrap": raw,
            "bootstrap_adjusted": adjust(raw, n_tests),
            "signflip": flip,
            "signflip_adjusted": adjust(flip, n_tests),
            "null_ge": float(np.mean(val_null >= val_stats[spec.name]["expectancy"])),
        }

    combine = {}
    combo_note = ""
    combo_daily = {}
    if finalists:
        pieces = {
            "train": (train_trades, train_sessions, TRAIN_END),
            "validation": (val_trades, val_sessions, VAL_END),
            "holdout": (hold_trades, hold_sessions, hold_sessions[-1]),
        }
        for spec in finalists:
            combine[spec.name] = {
                label: _attempt_block(trades[spec.name], sessions, censor)
                for label, (trades, sessions, censor) in pieces.items()
            }
        group = _pick_combo(finalists, train_daily)
        if group:
            weight = 1.0 / len(group)
            combo_name = "combo:" + "+".join(spec.name for spec in group)
            pair_txt = ", ".join(
                f"`{left.name}`/`{right.name}` {corr(train_daily[left.name], train_daily[right.name]):.2f}"
                for left, right in combinations(group, 2)
            )
            combo_note = (
                f"`{combo_name}` puts weight {weight:.2f} on "
                + ", ".join(f"`{spec.name}`" for spec in group)
                + f". Train daily correlations: {pair_txt}. "
                "Every pair is under 0.30 in the train window, which is why they are combined. "
                "Intraday heat is added across the books, so losses that happen on the same day both count. "
                "This is not an extra searched rule. It is those finalists at a smaller size."
            )
            combine[combo_name] = {}
            for label, (trades, sessions, censor) in pieces.items():
                rows = [session_rows(trades[spec.name], sessions) for spec in group]
                merged = combine_rows(rows, [weight] * len(group))
                combine[combo_name][label] = summarize_attempts(merged, censor)
                combo_daily[label] = [row["pnl"] for row in merged]

    if finalists:
        equity_lines = []
        for spec in finalists:
            equity_lines.append(
                (
                    spec.name,
                    {
                        "train": _cumsum(train_daily[spec.name]),
                        "validation": _cumsum(val_daily[spec.name]),
                        "holdout": _cumsum(hold_daily[spec.name]),
                    },
                )
            )
        if combo_daily:
            equity_lines.append(
                (
                    next(name for name in combine if name.startswith("combo:")),
                    {label: _cumsum(series) for label, series in combo_daily.items()},
                )
            )
        equity = {
            "splits": ["train", "validation", "holdout"],
            "lines": equity_lines,
            "title": "Finalists after costs",
        }
        combine_chart = {
            "names": list(combine),
            "splits": ["train", "validation", "holdout"],
            "rows": combine,
        }
    else:
        name = examined[0]["name"]
        equity = {
            "splits": ["train", "validation"],
            "lines": [
                (
                    f"{name} (did not qualify)",
                    {
                        "train": _cumsum(train_daily[name]),
                        "validation": _cumsum(val_daily[name]),
                    },
                )
            ],
            "title": "Best validation result did not qualify. No holdout line.",
        }
        combine_chart = None

    charts = write_charts(profile, equity, combine_chart, research_dir)
    ctx = {
        "load": summary,
        "splits": {"train": _span(train), "validation": _span(validation), "holdout": _span(holdout)},
        "profile": profile,
        "specs": specs,
        "train_stats": train_stats,
        "val_stats": val_stats,
        "hold_stats": hold_stats,
        "finalists": finalist_names,
        "finalist_rows": finalist_rows,
        "examined": examined,
        "pvalues": pvalues,
        "null": null,
        "combine": combine,
        "combo_note": combo_note,
        "status": {
            spec.name: ("finalist" if spec.name in finalist_names else _why(spec, train_stats, val_stats, specs, set(finalist_names)))
            if spec.eligible
            else "benchmark, not eligible"
            for spec in specs
        },
        "n_eligible": n_tests,
        "n_total": len(specs),
    }
    text = render(ctx)
    out.write_text(text)
    payload = {
        "load": summary,
        "splits": ctx["splits"],
        "n_eligible": n_tests,
        "n_total": len(specs),
        "finalists": finalist_names,
        "examined": examined,
        "pvalues": pvalues,
        "null": null,
        "combine": combine,
        "combo_note": combo_note,
        "stats": {
            spec.name: {"train": train_stats[spec.name], "validation": val_stats[spec.name], "holdout": hold_stats.get(spec.name)}
            for spec in specs
        },
        "profile_highlight": profile["highlight"],
        "profile_gap": profile["gap"],
        "profile_regimes": profile["regimes"],
        "rth_mean": profile["rth_mean"],
        "pooled_variance_ratio": profile["pooled_variance_ratio"],
        "pooled_autocorr": profile["pooled_autocorr"],
    }
    (research_dir / "results.json").write_text(json.dumps(_jsonable(payload), indent=2))
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, artifacts / "RESEARCH.md")
    for key, path in charts.items():
        if path is not None:
            shutil.copy(path, artifacts / path.name)
    print(f"wrote {out} in {time.time() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
