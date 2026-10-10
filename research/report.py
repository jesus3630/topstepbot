"""Plain-English research report and the charts that go with it."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from research.costs import FEE_SOURCE, active_instrument
from research.strategies import P_VALUE_CUTOFF, grid_text


def _money(value) -> str:
    if value is None or value != value:
        return "—"
    if abs(value) < 0.005:
        return "$0.00"
    return f"${value:,.2f}"


def _num(value, digits=2) -> str:
    if value is None or value != value:
        return "—"
    return f"{value:,.{digits}f}"


def _pct(value) -> str:
    if value is None or value != value:
        return "—"
    return f"{100.0 * value:.1f}%"


def _points_dollars(points) -> str:
    if points is None:
        return "—"
    inst = active_instrument()
    dollars = points * inst.point_value
    if abs(dollars) < 0.5:
        dollars_txt = "$0"
    else:
        dollars_txt = f"${dollars:.0f}"
    return f"{points:.2f} pts ({dollars_txt} on 1 {inst.code})"


def write_charts(profile: dict, equity: dict, combine: dict | None, directory: Path) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.grid": True,
            "grid.alpha": 0.25,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    paths = {
        "profile": directory / "time_of_day.png",
        "equity": directory / "equity.png",
        "combine": None,
    }
    _profile_chart(plt, profile, paths["profile"])
    _equity_chart(plt, equity, paths["equity"])
    if combine:
        paths["combine"] = directory / "combine_pass_rate.png"
        _combine_chart(plt, combine, paths["combine"])
    plt.close("all")
    return paths


def _profile_chart(plt, profile, path: Path) -> None:
    rows = [
        row
        for row in profile["buckets"]
        if row["n"] >= 100 and 7 * 60 + 30 <= row["minute"] < 15 * 60 and row["median_range"] is not None
    ]
    fig, ax = plt.subplots(figsize=(12, 5.5))
    colors = []
    for row in rows:
        vr = row["variance_ratio"]
        if vr is None:
            colors.append("#9aa0a6")
        elif vr >= 1:
            colors.append("#e07a3d")
        else:
            colors.append("#3d6f99")
    ax.bar([row["clock"] for row in rows], [row["median_range"] for row in rows], color=colors, width=0.8)
    inst = active_instrument()
    ax.axhline(inst.fee_round_turn / inst.point_value + 0.5, color="#444444", linewidth=0.8, linestyle="--")
    ax.set_title(f"{inst.code} median range by 30 minutes, 7:30–15:00 CT")
    ax.set_ylabel("Median range (points)")
    ax.tick_params(axis="x", labelrotation=90)
    ax.legend(
        handles=[
            plt.Rectangle((0, 0), 1, 1, color="#e07a3d", label="Variance ratio ≥ 1 (trend)"),
            plt.Rectangle((0, 0), 1, 1, color="#3d6f99", label="Variance ratio < 1 (mean reversion)"),
            plt.Line2D([0], [0], color="#444444", linestyle="--", label="About one round-turn of cost"),
        ],
        frameon=False,
        loc="upper right",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _equity_chart(plt, equity: dict, path: Path) -> None:
    splits = equity["splits"]
    fig, axes = plt.subplots(1, len(splits), figsize=(4.2 * len(splits), 4.6), sharey=True)
    if len(splits) == 1:
        axes = [axes]
    for ax, split in zip(axes, splits):
        for name, series in equity["lines"]:
            values = series.get(split)
            if not values:
                continue
            ax.plot(range(len(values)), values, label=name, linewidth=1.4)
        ax.axhline(0, color="#444444", linewidth=0.7)
        ax.set_title(split)
        ax.set_xlabel("Cash session")
        ax.set_ylabel("Cumulative P&L after costs ($)")
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, frameon=False, loc="upper center", ncol=max(1, len(labels)))
    title = equity["title"]
    fig.suptitle(title, y=1.02)
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _combine_chart(plt, combine: dict, path: Path) -> None:
    names = combine["names"]
    splits = combine["splits"]
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(names))
    width = 0.8 / max(1, len(splits))
    for i, split in enumerate(splits):
        rates = []
        for name in names:
            block = combine["rows"][name].get(split) or {}
            rate = block.get("pass_rate_all")
            rates.append(0.0 if rate is None else rate)
        ax.bar(x + i * width, rates, width=width, label=split)
    ax.set_xticks(x + width * (len(splits) - 1) / 2, names, rotation=15, ha="right")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Monthly starts that passed")
    ax.set_title("50K Combine pass rate (unfinished attempts count as not passed)")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _cost_paragraph(inst) -> str:
    if inst.code == "MES":
        return (
            f"TopstepX lists the Micro E-mini S&P 500 round turn at **$1.22** "
            f"($0.50 commission, $0.70 exchange, $0.02 NFA), read from {FEE_SOURCE} on 2026-10-10. "
            "The October 2026 CME increase on that page is MCL and MNG, not MES. "
            "A market or stop order also pays 1 tick against the trader on the way in and, if it exits by stop or by a flatten, 1 tick on the way out. "
            f"That is **$3.72** all-in for a market round turn on one MES. A limit that is filled only when price trades through it pays no entry slippage. "
            "The live config still says $1.40. This study does not edit that file."
        )
    slip = inst.tick_value
    all_in = inst.fee_round_turn + 2 * slip
    return (
        f"TopstepX lists the Micro E-mini Nasdaq-100 round turn at **${inst.fee_round_turn:.2f}**, "
        f"read from {FEE_SOURCE} on 2026-10-10. The page shows that total beside MES and does not print a separate "
        "commission, exchange, and NFA split for MNQ. "
        "The October 2026 CME increase on that page is MCL and MNG, not MNQ. "
        "A market or stop order also pays 1 tick against the trader on the way in and, if it exits by stop or by a flatten, 1 tick on the way out. "
        f"That is **${all_in:.2f}** all-in for a market round turn on one {inst.code} "
        f"(the fee plus two ticks at ${slip:.2f}). A limit that is filled only when price trades through it pays no entry slippage. "
        "The live config still says $1.40. This study does not edit that file."
    )


def _account_paragraph(inst) -> str:
    if inst.code == "MES":
        other = (
            "MNQ and M2K were not tested. There is no second data file, and adding one would not have been a small side check."
        )
    else:
        other = f"This run prices {inst.code}. M2K was not tested."
    return (
        "The account rules used here are the 50K Combine: $3,000 profit target, $1,000 daily loss limit, "
        f"$2,000 end-of-day trailing max loss, at most 50 {inst.code}, and flat by 3:10 PM CT. "
        "Passing also requires the best day to be under half of the profits. The later payout path uses 40% instead of 50%; that stricter test is reported beside the Combine result, and it is not what decides a pass. "
        "Size in this study risks $200 a trade, skips the trade if one contract cannot fit, takes at most two trades a day, and stops opening new trades once the day is down $400. "
        + other
    )


def render(ctx: dict) -> str:
    inst = active_instrument()
    lines = []
    lines.append(f"# {inst.code} research: when it moves, and which simple rules survive")
    lines.append("")
    lines.append(_lead(ctx))
    lines.append("")
    lines.append("This is a research note, not a recommendation to place a live order and not financial advice. The live bot's settings were not changed.")
    lines.append("")
    lines.append("## Costs, hours, and the Combine")
    lines.append("")
    lines.append(_cost_paragraph(inst))
    lines.append("")
    lines.append(_account_paragraph(inst))
    lines.append("")
    lines.append("## The rules were written down first")
    lines.append("")
    lines.append(
        "The families, the parameter lists, the train/validation/holdout dates, and the pass rules live in `research/strategies.py`. "
        "The same text is saved to `docs/research/predeclared_grid.txt` at the start of the run, before the bars are scored. "
        f"There are **{ctx['n_eligible']}** eligible configurations and **{ctx['n_total']}** rows once the three benchmarks are included. "
        "The 100 extra random seeds are a null distribution. They are not 100 more chances to crown a winner."
    )
    lines.append("")
    lines.append("```text")
    lines.append(grid_text().rstrip())
    lines.append("```")
    lines.append("")
    lines.append("## The sample")
    lines.append("")
    load = ctx["load"]
    splits = ctx["splits"]
    lines.append(
        f"The file is unadjusted Databento `{inst.continuous}` 1-minute bars, {load['first']} through {load['last']}: "
        f"{load['bars']:,} bars, {load['sessions']:,} cash sessions, {load['rolls']} contract changes, {load['contracts']} outright labels. "
        "A roll is a real gap, so each cash session is cut to the outright that is trading at 8:30 CT. "
        "Prior-day high, low, and close carry forward only when yesterday's cash close was the same outright. "
        "Friday evening and Sunday evening both belong to Monday's overnight. Prices are not back-adjusted."
    )
    lines.append("")
    lines.append("| split | sessions | from | through |")
    lines.append("|---|---:|---|---|")
    for name in ("train", "validation", "holdout"):
        block = splits[name]
        lines.append(f"| {name} | {block['n']} | {block['first']} | {block['last']} |")
    lines.append("")
    lines.append(
        "Train is for discovery. Validation has to confirm the same kind of result. "
        "The holdout was not used to choose the grid, to drop a neighbor, or to pick the finalists."
    )
    lines.append("")
    lines.append(f"## What time of day {inst.code} actually moves")
    lines.append("")
    lines.extend(_times(ctx["profile"]))
    lines.append("")
    lines.append("![Median 30-minute range from 7:30 to 15:00 CT](research/time_of_day.png)")
    lines.append("")
    lines.append(_bucket_table(ctx["profile"]))
    lines.append("")
    lines.append(_weekday_and_gap(ctx["profile"]))
    lines.append("")
    lines.append("## Every configuration, after costs")
    lines.append("")
    lines.append(
        "Expectancy is average dollars per trade after the fee and the slippage. "
        "A day with no fill is not a trade. Benchmarks are in the table and cannot be selected."
    )
    lines.append("")
    lines.append(_results_table(ctx))
    lines.append("")
    lines.append("## Selection")
    lines.append("")
    lines.extend(_selection(ctx))
    lines.append("")
    lines.append("## Finalists, or the best rules that still failed")
    lines.append("")
    lines.extend(_finalist_section(ctx))
    lines.append("")
    lines.append("![Cumulative P&L after costs](research/equity.png)")
    lines.append("")
    if ctx["finalists"]:
        lines.append("## Combine attempts")
        lines.append("")
        lines.extend(_combine_section(ctx))
        lines.append("")
        lines.append("![Combine pass rate by monthly start](research/combine_pass_rate.png)")
        lines.append("")
    else:
        lines.append("## Combine attempts")
        lines.append("")
        lines.append(
            "No rule qualified, so there is no Combine pass rate. "
            "Inventing one for a rule that already lost money out of sample would dress up a failure."
        )
        lines.append("")
    lines.append("## What no edge looks like")
    lines.append("")
    lines.extend(_null_section(ctx))
    lines.append("")
    lines.append("## Recommendation for the live bot")
    lines.append("")
    lines.append(_recommendation(ctx))
    lines.append("")
    lines.append("## What this study did not change")
    lines.append("")
    lines.append(
        "Live defaults are untouched: the 2-tick retest, the $1.40 fee placeholder, the $200 risk, the 2-contract cap, the morning window, and the flatten times. "
        "Research limit fills are stricter than the paper broker. The paper broker fills a target when price touches it. This study fills a limit only when price trades through it. Stops still fill on a touch, and if a stop and a target are in the same bar the stop wins. "
        f"The research sizer is also separate. It can use up to 50 {inst.code} and it charges both sides of slippage up front. It does not call the live sizer, which is capped at 2 MES."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _lead(ctx: dict) -> str:
    finalists = ctx["finalists"]
    if not finalists:
        best = ctx["examined"][0] if ctx["examined"] else None
        extra = ""
        if best:
            val = ctx["val_stats"][best["name"]]
            train = ctx["train_stats"][best["name"]]
            extra = (
                f" The highest validation expectancy belongs to `{best['name']}`: "
                f"{_money(train['expectancy'])} per trade in train ({train['trades']} trades, net {_money(train['net'])}) and "
                f"{_money(val['expectancy'])} in validation ({val['trades']} trades, net {_money(val['net'])}). "
                f"It was rejected because {best['reasons']}."
            )
        return (
            "Do not change the live bot. No predeclared rule is good enough to trade. "
            f"Of {ctx['n_eligible']} eligible configurations, none stayed positive after costs on both the 2019–2023 train window and the 2024–mid-2025 validation window under the gates written down in advance. "
            "The 2025-07 through 2026-09 holdout was not opened."
            + extra
        )
    weak = [name for name in finalists if ctx["pvalues"][name]["bootstrap_adjusted"] > P_VALUE_CUTOFF]
    hold_fail = [
        name
        for name in finalists
        if ctx["hold_stats"][name]["expectancy"] <= 0 or ctx["hold_stats"][name]["net"] <= 0
    ]
    bits = []
    for name in finalists:
        hold = ctx["hold_stats"][name]
        val = ctx["val_stats"][name]
        train = ctx["train_stats"][name]
        raw = ctx["pvalues"][name]["bootstrap"]
        adjusted = ctx["pvalues"][name]["bootstrap_adjusted"]
        bits.append(
            f"`{name}` is the only rule that cleared the accounting gates. "
            f"It made {_money(train['expectancy'])} per trade in train ({train['trades']} trades, net {_money(train['net'])}), "
            f"{_money(val['expectancy'])} in validation ({val['trades']} trades, net {_money(val['net'])}), and "
            f"{_money(hold['expectancy'])} on the one holdout pass ({hold['trades']} trades, net {_money(hold['net'])}, win rate {_pct(hold['win_rate'])}). "
            f"The validation bootstrap p-value is {raw:.3f} before the penalty and {adjusted:.3f} after multiplying by the {ctx['n_eligible']} tests."
        )
    if weak or hold_fail:
        return (
            "Do not trade it, and do not change the live bot. "
            + " ".join(bits)
            + f" The cutoff written down before the run was an adjusted p-value of {P_VALUE_CUTOFF:.2f}. "
            "A positive dollar total that does not clear that bar is not an edge. "
            "The train result is about one dollar a trade, which is smaller than one tick, and the later windows are a jump rather than the same result repeating."
        )
    return " ".join(bits)


def _times(profile: dict) -> list[str]:
    h = profile["highlight"]
    lines = []
    lines.append(
        "The widest cash-session ranges are 8:30–10:00 CT. The quietest are around 11:30–1:00 CT. "
        "A high variance ratio later in the day, or a positive Monday average, is a description of the path. "
        "The predeclared long and short holds for those clocks are in the results table, and they lost money after costs."
    )
    lines.append("")
    lines.append(
        f"Across the whole file, the cash session from 8:30 to 15:00 CT has a mean move of {_points_dollars(profile['rth_mean'])} "
        f"and a median of {_points_dollars(profile['rth_median'])}. "
        f"The median absolute move in the first hour is {_points_dollars(profile['first_hour_abs_median'])}, "
        f"against {_points_dollars(profile['rest_abs_median'])} for the rest of the session. "
        f"The median range is {_points_dollars(profile['first_hour_range_median'])} in the first hour and "
        f"{_points_dollars(profile['rest_range_median'])} after 9:30. "
        "The first hour is the noisy part of the day. The rest of the session is usually a smaller net move inside a still-wide range, which is a bad trade if the stop has to sit outside that range."
    )
    lines.append("")
    if h:
        lines.append(
            f"Among 30-minute cash-session buckets with at least 200 days, the widest median range is **{h['widest']}** "
            f"at {_points_dollars(h['widest_range'])}. The quietest is **{h['quietest']}** at {_points_dollars(h['quietest_range'])}. "
            f"The typical bucket's median range is about {_points_dollars(h['median_of_median_range'])}. "
            "A round turn costs about 0.74 points, so the fee is not what makes a 30-minute bar untradable. "
            "A quiet bucket is still the worst place to trade, because a stop wide enough to survive the noise is a large piece of a small move, and a tight stop gets tagged by that noise."
        )
        lines.append("")
        mom = h.get("momentum")
        fade = h.get("fade")
        if mom and fade:
            lines.append(
                f"Where the range is at least average, **{mom}** is the bucket whose next half-hour behaves most like a continuation "
                f"(variance ratio {h['momentum_vr']:.2f}). **{fade}** is the one that behaves most like a fade "
                f"(variance ratio {h['fade_vr']:.2f}). "
                f"Pooled across adjacent cash-session buckets, the lag-1 autocorrelation is {_num(profile['pooled_autocorr'], 3)} "
                f"and the variance ratio is {_num(profile['pooled_variance_ratio'], 2)}. "
                "A variance ratio above 1 says the next half-hour tends to extend this one. Below 1, it tends to give the move back. "
                "These are descriptions of the path, not a license to trade the single best bucket. The time-of-day rules in the grid were listed before this picture was drawn, including both directions, so the clock could not be picked after the fact."
            )
            lines.append("")
    lines.append(_news(profile))
    lines.append("")
    lines.append(_regimes(profile))
    lines.append("")
    gap = profile["gap"]
    lines.append(
        f"The overnight gap, from the prior 15:00 close to the 8:30 open on the same outright, averages {_points_dollars(gap['mean'])} "
        f"(median {_points_dollars(gap['median'])}, {gap['n']} days). "
        f"The cash-session mean is {_points_dollars(profile['rth_mean'])}. "
        "A large share of the futures drift shows up while the cash session is shut. A Topstep day account cannot hold that overnight move, so a strategy that needs it is not available here. "
        f"After a gap up, the next 30 minutes average {_points_dollars(gap['up_next_mean'])} and continue higher {_pct(gap['up_continuation'])} of the time ({gap['up_n']} days). "
        f"After a gap down, the next 30 minutes average {_points_dollars(gap['down_next_mean'])} and continue lower {_pct(gap['down_continuation'])} of the time ({gap['down_n']} days). "
        "A continuation rate near 50% means the open does not reliably follow the gap."
    )
    return lines


def _news(profile: dict) -> str:
    wanted = {"07:30": None, "08:00": None, "08:30": None, "09:00": None, "13:00": None}
    for row in profile["buckets"]:
        if row["clock"] in wanted:
            wanted[row["clock"]] = row
    bits = []
    for clock, row in wanted.items():
        if row is None or row["median_range"] is None:
            bits.append(f"{clock} has too few complete buckets")
        else:
            bits.append(
                f"{clock} median range {_points_dollars(row['median_range'])}, "
                f"median absolute move {_points_dollars(row['median_abs'])}, "
                f"mean signed move {_points_dollars(row['mean_signed'])}"
            )
    return (
        "Scheduled US data is usually 8:30 ET, which is **7:30 CT**, half an hour before the cash open. "
        "8:30 CT is the stock open (9:30 ET), not the data print. 9:00 CT is 10:00 ET. 1:00 PM CT is 2:00 PM ET. "
        + ". ".join(bits)
        + "."
    )


def _regimes(profile: dict) -> str:
    lines = ["The same open looks different across regimes.", "", "| regime | days | mean cash-session move | median first-hour absolute move | median 8:30 bucket range |", "|---|---:|---:|---:|---:|"]
    for name in ("all", "2020 covid", "2022 bear", "2023-2026"):
        row = profile["regimes"][name]
        lines.append(
            f"| {name} | {row['n']} | {_points_dollars(row['rth_mean'])} | {_points_dollars(row['first_hour_abs_median'])} | {_points_dollars(row['open_bucket_range_median'])} |"
        )
    lines.append("")
    lines.append(
        "Covid and the 2022 bear market were wider. A rule fit to those ranges will look busy and then go quiet, which is why a 2024–2025 confirmation window exists."
    )
    return "\n".join(lines)


def _bucket_table(profile: dict) -> str:
    lines = [
        "Thirty-minute buckets with at least 100 days. A bucket is dropped inside a day when it has fewer than 20 one-minute bars, which removes partial prints around a roll. Range is the high minus the low. The variance ratio uses that bucket and the next one: variance of the two-bucket sum, divided by twice the variance of the first bucket.",
        "",
        "| clock | phase | days | mean move | median abs move | median range | autocorr | variance ratio |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in profile["buckets"]:
        if row["n"] < 100:
            continue
        lines.append(
            f"| {row['clock']} | {row['phase']} | {row['n']} | {_num(row['mean_signed'])} | {_num(row['median_abs'])} | "
            f"{_num(row['median_range'])} | {_num(row['autocorr'], 2)} | {_num(row['variance_ratio'], 2)} |"
        )
    return "\n".join(lines)


def _weekday_and_gap(profile: dict) -> str:
    lines = [
        "Cash-session move by weekday, 8:30 open to the last print before 15:00, same outright.",
        "",
        "| day | sessions | mean | median |",
        "|---|---:|---:|---:|",
    ]
    for row in profile["weekday"]:
        lines.append(f"| {row['day']} | {row['n']} | {_points_dollars(row['mean'])} | {_points_dollars(row['median'])} |")
    lines.append("")
    lines.append("Weekday differences of a point or two are smaller than the range inside the day. They are not a strategy.")
    return "\n".join(lines)


def _results_table(ctx: dict) -> str:
    lines = [
        "| rule | train n | train win | train $/trade | train net | val n | val win | val $/trade | val net | status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for spec in ctx["specs"]:
        train = ctx["train_stats"][spec.name]
        val = ctx["val_stats"][spec.name]
        lines.append(
            f"| {spec.name} | {train['trades']} | {_pct(train['win_rate'])} | {_money(train['expectancy'])} | {_money(train['net'])} | "
            f"{val['trades']} | {_pct(val['win_rate'])} | {_money(val['expectancy'])} | {_money(val['net'])} | {ctx['status'][spec.name]} |"
        )
    return "\n".join(lines)


def _selection(ctx: dict) -> list[str]:
    lines = [
        f"N for the multiple-testing penalty is {ctx['n_eligible']}. Benchmarks are excluded from that count.",
        "",
    ]
    if not ctx["finalists"]:
        lines.append("Nothing passed every gate. The five highest validation expectancies, and why each one was rejected:")
        lines.append("")
    else:
        lines.append(
            "Finalists are the rules that passed the train gate, the validation gate, and the neighbor gate, then ranked high enough and were not too correlated with a higher-ranked finalist. "
            "Adjusted p-values are reported beside them. A bad p-value does not hide the holdout result, and it does block a recommendation to trade."
        )
        lines.append("")
    lines.append("| rule | why it is here | train $/trade | val $/trade | val trades | neighbors with val expectancy ≤ 0 |")
    lines.append("|---|---|---:|---:|---:|---|")
    shown = ctx["finalist_rows"] + ctx["examined"]
    for row in shown:
        lines.append(
            f"| {row['name']} | {row['reasons']} | {_money(ctx['train_stats'][row['name']]['expectancy'])} | "
            f"{_money(ctx['val_stats'][row['name']]['expectancy'])} | {ctx['val_stats'][row['name']]['trades']} | {row['neighbor_note']} |"
        )
    return lines


def _finalist_section(ctx: dict) -> list[str]:
    lines = []
    if ctx["finalists"]:
        lines.append("Holdout numbers below were computed once, after the names were locked. They are reported whether or not they made money.")
        lines.append("")
        names = ctx["finalists"]
        qual = "qualified"
    else:
        lines.append(
            "There is no finalist. The chart shows the rejected rule with the highest validation expectancy, on train and validation only. There is no holdout line."
        )
        lines.append("")
        names = [ctx["examined"][0]["name"]] if ctx["examined"] else []
        qual = "rejected"
    lines.append("| rule | split | trades | win rate | avg win | avg loss | $/trade | net | max drawdown | worst day | best day | avg qty |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in names:
        blocks = [("train", ctx["train_stats"][name]), ("validation", ctx["val_stats"][name])]
        if qual == "qualified":
            blocks.append(("holdout", ctx["hold_stats"][name]))
        for label, summary in blocks:
            lines.append(
                f"| {name} | {label} | {summary['trades']} | {_pct(summary['win_rate'])} | {_money(summary['avg_win'])} | "
                f"{_money(summary['avg_loss'])} | {_money(summary['expectancy'])} | {_money(summary['net'])} | "
                f"{_money(summary['max_dd'])} | {_money(summary['worst_day'])} | {_money(summary['best_day'])} | {_num(summary['avg_qty'], 1)} |"
            )
    if ctx["finalists"]:
        lines.append("")
        lines.append("| rule | bootstrap p | adjusted bootstrap p | sign-flip p | adjusted sign-flip p | share of random nulls at least this high |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for name in ctx["finalists"]:
            p = ctx["pvalues"][name]
            lines.append(
                f"| {name} | {p['bootstrap']:.3f} | {p['bootstrap_adjusted']:.3f} | {p['signflip']:.3f} | "
                f"{p['signflip_adjusted']:.3f} | {_pct(p['null_ge'])} |"
            )
        lines.append("")
        lines.append(
            "The bootstrap asks how often a resample of this rule's own validation days has a mean at or below zero. "
            "The sign-flip asks how often scrambling the sign of those days still looks as good as the real result. "
            "The last column compares the rule with the random-entry null (an 8-point stop, an 8-point target, and a 10:30 exit). "
            f"Adjusted values multiply the raw p-value by the {ctx['n_eligible']} eligible tests and cap the result at 1. "
            "The bootstrap uses every validation session, including days with no trade, because a Combine lives through those days too."
        )
    return lines


def _combine_section(ctx: dict) -> list[str]:
    lines = [
        "Each month inside a split starts a fresh $50,000 attempt. The attempt cannot see the next split. "
        "Pass means +$3,000 before a $1,000 intraday daily loss or a touch of the trailing $2,000 floor, and the best closed day is under half of profits. "
        "Resolved pass rate ignores attempts that ran out of calendar. The all-starts rate counts those unfinished attempts as failures, which is the number that matters if you buy a Combine on a random month.",
        "",
        "| book | split | starts | passed | failed | censored | resolved pass rate | all-starts pass rate | median days to pass | passes that also met 40% |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, blocks in ctx["combine"].items():
        for split, row in blocks.items():
            if row is None:
                continue
            lines.append(
                f"| {name} | {split} | {row['starts']} | {row['passes']} | {row['fails']} | {row['censored']} | "
                f"{_pct(row['pass_rate_resolved'])} | {_pct(row['pass_rate_all'])} | {_num(row['median_days'], 1)} | {row['passes_also_40']} |"
            )
    if ctx.get("combo_note"):
        lines.append("")
        lines.append(ctx["combo_note"])
    return lines


def _null_section(ctx: dict) -> list[str]:
    null = ctx["null"]
    lines = [
        f"Two buy-and-hold benchmarks buy one direction at 8:30 and flatten at 15:00. One has no stop and is always 1 {active_instrument().code}. The other uses a 20-point stop and the $200 sizer. "
        "The random benchmark flips a coin from the session date (seed 7), enters at 9:00 with an 8-point stop and an 8-point target, and flattens at 10:30. "
        "One hundred more seeds, 1000 through 1099, were run on train and validation only.",
        "",
        f"Random-seed validation expectancy: mean {_money(null['val']['mean'])}, median {_money(null['val']['median'])}, "
        f"5th percentile {_money(null['val']['p05'])}, 95th percentile {_money(null['val']['p95'])}, "
        f"share positive {_pct(null['val']['share_positive'])}.",
        f"Train: mean {_money(null['train']['mean'])}, median {_money(null['train']['median'])}, share positive {_pct(null['train']['share_positive'])}.",
        "",
        "Those 100 seeds are only two books. Adding an even number or an odd number to the date flips every day the same way, so half the seeds are one coin-flip and half are its mirror. "
        "Both mirrors lost money, on the order of $7 to $24 per trade. Beating that null is a low bar: a coin-flip with an 8-point stop and an 8-point target loses to costs. "
        "It shows what no edge looks like. It does not prove that a rule which lost less is a real edge. The bootstrap on the rule's own days is the check that matters.",
        "",
    ]
    best_name = ctx["examined"][0]["name"] if ctx["examined"] else None
    if best_name and not ctx["finalists"]:
        share = ctx["examined"][0].get("null_ge")
        lines.append(
            f"The best rejected validation expectancy is {_money(ctx['val_stats'][best_name]['expectancy'])} on `{best_name}`. "
            f"{_pct(share)} of the random seeds did at least that well on validation. "
            "That is the scale of 'no edge': a coin-flip entry with the same kind of exit wanders around zero, and a searched rule has to clear that cloud after you remember how many rules you looked at."
        )
    return lines


def _recommendation(ctx: dict) -> str:
    if not ctx["finalists"]:
        return (
            "Do not change the live bot. Do not loosen the chop filter, do not widen the retest, and do not replace the morning setup with one of these researched rules. "
            "None of them cleared a confirmation window that was decided before the test. "
            "The prior seven-year replay of the live opening-range retest was already about 102 trades, a 38% win rate, and a loss of roughly $7 a trade. This broader search does not rescue it, and it does not offer a substitute. "
            "Keep trading only while you are at the screen, on the rules already in the config, or do not trade. A new Combine attempt is not justified by this file."
        )
    weak = [name for name in ctx["finalists"] if ctx["pvalues"][name]["bootstrap_adjusted"] > P_VALUE_CUTOFF]
    hold_fail = [
        name
        for name in ctx["finalists"]
        if ctx["hold_stats"][name]["expectancy"] <= 0 or ctx["hold_stats"][name]["net"] <= 0
    ]
    if hold_fail or weak:
        parts = []
        if hold_fail:
            parts.append(
                "These finalists lost money, or made nothing per trade, on the holdout: " + ", ".join(f"`{name}`" for name in hold_fail) + "."
            )
        if weak:
            parts.append(
                "These finalists do not clear the predeclared adjusted p-value cutoff of "
                f"{P_VALUE_CUTOFF:.2f}: " + ", ".join(f"`{name}`" for name in weak) + ". Do not trade them."
            )
        parts.append(
            "Do not change the live bot. Counting all 63 tests, the result is indistinguishable from luck, even where the holdout dollars happened to stay positive. "
            "Leave the 2-tick retest and the current risk settings where they are. Do not loosen a filter to force more trades."
        )
        return " ".join(parts)
    return (
        "The finalists above stayed positive on the holdout and cleared the adjusted p-value cutoff. "
        "That still does not flip the live settings by itself. The bot is supervised and the account is a Combine: paper the rule beside the current morning session before any config change, and keep the human flatten at 3:10 PM CT. "
        "This note does not change `config/settings.yaml`."
    )
