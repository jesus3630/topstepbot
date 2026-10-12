"""M2K correlations, the four predictors, and the Sunday reopen.

The 63-rule M2K screen is ``python -m research.run --instrument M2K``. This
module does not rescore that menu.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from research.costs import MES, MNQ, M2K
from research.events import load_vix
from research.followup import _jsonable
from research.predictors import load_dgs10, pair_clock, score_predictors, signal_correlations
from research.sunday import describe_gaps, load_episodes, public_gaps, score_sunday


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="M2K predictors and the Sunday reopen")
    parser.add_argument("--mes-early", required=True)
    parser.add_argument("--mes-late", required=True)
    parser.add_argument("--mnq-early", required=True)
    parser.add_argument("--mnq-late", required=True)
    parser.add_argument("--m2k-early", required=True)
    parser.add_argument("--m2k-late", required=True)
    parser.add_argument("--out", default="docs/research/m2k_followup.json")
    args = parser.parse_args(argv)
    from research.market import load_days

    print("loading MES", flush=True)
    mes_days, mes_summary = load_days([args.mes_early, args.mes_late])
    print("loading MNQ", flush=True)
    mnq_days, mnq_summary = load_days([args.mnq_early, args.mnq_late])
    print("loading M2K", flush=True)
    m2k_days, m2k_summary = load_days([args.m2k_early, args.m2k_late])
    vix = load_vix()
    yields = load_dgs10()
    print("correlations", flush=True)
    m2k_mes = pair_clock(mes_days, m2k_days, MES.point_value, M2K.point_value)
    m2k_mnq = pair_clock(mnq_days, m2k_days, MNQ.point_value, M2K.point_value)
    signals = signal_correlations(mes_days, mnq_days, m2k_days, vix, yields)
    print("predictors", flush=True)
    predictors = score_predictors(mes_days, mnq_days, m2k_days, vix, yields)
    print("sunday", flush=True)
    sunday = {}
    gap_paths = {}
    for code, inst, early, late in (
        ("MES", MES, args.mes_early, args.mes_late),
        ("MNQ", MNQ, args.mnq_early, args.mnq_late),
        ("M2K", M2K, args.m2k_early, args.m2k_late),
    ):
        print(f"  {code}", flush=True)
        episodes, volume = load_episodes([early, late])
        gaps = describe_gaps(episodes, inst.tick)
        gap_paths[code] = gaps.pop("abs_percent")
        sunday[code] = {"volume": volume, "gaps": public_gaps(gaps), "rules": score_sunday(episodes, inst)}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    charts = write_charts(m2k_mes, m2k_mnq, gap_paths, out.parent)
    payload = {
        "mes": mes_summary,
        "mnq": mnq_summary,
        "m2k": m2k_summary,
        "sources": {
            "vix": "Cboe VIX_History.csv, daily closes. VIX futures intraday was not used: the Cboe VX history file returned HTTP 403 on 2026-10-12.",
            "yield": "U.S. Treasury daily par yield curve, 10 Yr column, retrieved 2026-10-12 from home.treasury.gov. This is the series FRED publishes as DGS10. FRED returned HTTP 500. The predictor uses the prior change only.",
            "zn": "No Databento key is in this environment, so metadata.get_cost was not called and no ZN file was downloaded.",
            "sunday_hours": "https://help.topstep.com/en/articles/8284206-when-and-what-products-can-i-trade read 2026-10-12. Sunday open 5:00 PM CT. Flat by 3:10 PM CT. The page does not print that Sunday P&L counts as Monday; this study uses the CME trade date, which is Monday.",
            "m2k_fee": "https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees read 2026-10-12. M2K round turn $1.22, no separate split printed.",
        },
        "m2k_vs_mes": m2k_mes,
        "m2k_vs_mnq": m2k_mnq,
        "signals": signals,
        "predictors": predictors,
        "sunday": sunday,
    }
    out.write_text(json.dumps(_jsonable(payload), indent=2))
    artifacts = Path("/opt/cursor/artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    for path in charts.values():
        (artifacts / path.name).write_bytes(path.read_bytes())
    print(f"wrote {out}", flush=True)
    print("predictors", {name: (block["candidate"], block["pvalue"]) for name, block in predictors.items()}, flush=True)
    for code, block in sunday.items():
        print(code, {name: (rule["candidate"], rule["pvalue"]) for name, rule in block["rules"].items()}, flush=True)


def write_charts(m2k_mes: dict, m2k_mnq: dict, gaps: dict, directory: Path) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    fig, ax = plt.subplots(figsize=(11, 4.2))
    clocks = m2k_mes["by_clock"]
    ax.bar([row["clock"] for row in clocks], [0 if row["corr"] is None else row["corr"] for row in clocks], color="#3d6f99")
    ax.set_ylim(0.5, 1.0)
    ax.set_title("Correlation of 1-minute MES and M2K returns by half hour")
    ax.tick_params(axis="x", labelrotation=90)
    ax.set_ylabel("Correlation")
    fig.tight_layout()
    paths["mes"] = directory / "m2k_es_corr_by_time.png"
    fig.savefig(paths["mes"], dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4.2))
    clocks = m2k_mnq["by_clock"]
    ax.bar([row["clock"] for row in clocks], [0 if row["corr"] is None else row["corr"] for row in clocks], color="#e07a3d")
    ax.set_ylim(0.5, 1.0)
    ax.set_title("Correlation of 1-minute MNQ and M2K returns by half hour")
    ax.tick_params(axis="x", labelrotation=90)
    ax.set_ylabel("Correlation")
    fig.tight_layout()
    paths["mnq"] = directory / "m2k_nq_corr_by_time.png"
    fig.savefig(paths["mnq"], dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4.2))
    order = [str(i) for i in range(-5, 6)]
    lags = m2k_mes["lead_lag"]
    ax.bar(order, [lags[key]["corr"] or 0 for key in order], color="#3d6f99")
    ax.axhline(0, color="#444444", linewidth=0.7)
    ax.set_title("1-minute lead-lag. Positive lag means M2K's later minute")
    ax.set_xlabel("Lag of M2K relative to MES (minutes)")
    ax.set_ylabel("Correlation")
    fig.tight_layout()
    paths["lag"] = directory / "m2k_es_lead_lag.png"
    fig.savefig(paths["lag"], dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4.2))
    for code, color in (("MES", "#3d6f99"), ("MNQ", "#e07a3d"), ("M2K", "#2f6b4f")):
        values = np.asarray(gaps.get(code) or [], dtype=float) * 100
        if len(values) == 0:
            continue
        ax.hist(values, bins=40, range=(-2.5, 2.5), histtype="step", linewidth=1.4, label=code, color=color)
    ax.axvline(0, color="#444444", linewidth=0.7)
    ax.set_title("Sunday reopen gap versus Friday's cash close")
    ax.set_xlabel("Gap, percent")
    ax.set_ylabel("Sundays")
    ax.legend()
    fig.tight_layout()
    paths["gap"] = directory / "sunday_gap.png"
    fig.savefig(paths["gap"], dpi=120)
    plt.close(fig)
    return paths


if __name__ == "__main__":
    main()
