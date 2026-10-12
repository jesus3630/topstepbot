"""Rolling Topstep 50K Combine attempts. Research only.

Each attempt starts at the first cash session of a month and stops at the
end of its own sample, so a train attempt never spends validation days and
a validation attempt never spends holdout days.
"""

from __future__ import annotations

from datetime import date

from research.stats import Trade

START = 50_000.0
TARGET = 3_000.0
DLL = 1_000.0
TRAIL = 2_000.0


def session_rows(trades: list[Trade], sessions: list[date]) -> list[dict]:
    grouped: dict[date, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(trade.session, []).append(trade)
    rows = []
    for session in sessions:
        closed = 0.0
        worst = 0.0
        for trade in grouped.get(session, ()):
            heat = closed - float(trade.mae)
            worst = min(worst, heat)
            closed += float(trade.pnl)
            worst = min(worst, closed)
        rows.append({"session": session, "pnl": closed, "worst": worst})
    return rows


def combine_rows(row_lists: list[list[dict]], weights: list[float]) -> list[dict]:
    """Add scaled books. Heat is summed, which assumes the losses can overlap."""
    combined = []
    for index in range(len(row_lists[0])):
        pnl = 0.0
        worst = 0.0
        for rows, weight in zip(row_lists, weights):
            pnl += weight * float(rows[index]["pnl"])
            worst += weight * float(rows[index]["worst"])
        combined.append({"session": row_lists[0][index]["session"], "pnl": pnl, "worst": worst})
    return combined


def month_starts(sessions: list[date]) -> list[date]:
    seen = set()
    starts = []
    for session in sessions:
        key = (session.year, session.month)
        if key in seen:
            continue
        seen.add(key)
        starts.append(session)
    return starts


def run_attempt(rows: list[dict], start: date, censor: date) -> dict:
    balance = START
    high = START
    floor = START - TRAIL
    best = 0.0
    days = 0
    saw = False
    for rec in rows:
        session = rec["session"]
        if session < start or session > censor:
            continue
        saw = True
        days += 1
        worst = float(rec["worst"])
        dll = worst <= -DLL
        mll = balance + worst <= floor
        if dll or mll:
            if dll and mll:
                reason = "dll+mll"
            elif dll:
                reason = "dll"
            else:
                reason = "mll"
            return {
                "status": "fail",
                "reason": reason,
                "days": days,
                "profit": balance - START,
                "best_day": best,
            }
        balance += float(rec["pnl"])
        if float(rec["pnl"]) > best:
            best = float(rec["pnl"])
        if balance > high:
            high = balance
        trailed = min(START, high - TRAIL)
        if trailed > floor:
            floor = trailed
        profit = balance - START
        if profit >= TARGET and best < 0.5 * profit:
            return {
                "status": "pass",
                "days": days,
                "profit": profit,
                "best_day": best,
                "best_share": best / profit,
                "also_40": best < 0.4 * profit,
            }
    if not saw:
        return {"status": "empty", "days": 0, "profit": 0.0, "best_day": 0.0}
    profit = balance - START
    return {
        "status": "censored",
        "days": days,
        "profit": profit,
        "best_day": best,
        "consistency_blocked": profit >= TARGET and not (best < 0.5 * profit),
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[mid])
    return float(ordered[mid - 1] + ordered[mid]) / 2.0


def summarize_attempts(rows: list[dict], censor: date) -> dict:
    starts = month_starts([rec["session"] for rec in rows if rec["session"] <= censor])
    attempts = [run_attempt(rows, start, censor) for start in starts]
    passes = [item for item in attempts if item["status"] == "pass"]
    fails = [item for item in attempts if item["status"] == "fail"]
    censored = [item for item in attempts if item["status"] == "censored"]
    resolved = len(passes) + len(fails)
    reasons: dict[str, int] = {}
    for item in fails:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    return {
        "starts": len(attempts),
        "passes": len(passes),
        "fails": len(fails),
        "censored": len(censored),
        "consistency_blocked": sum(1 for item in censored if item.get("consistency_blocked")),
        "pass_rate_resolved": (len(passes) / resolved) if resolved else None,
        "pass_rate_all": (len(passes) / len(attempts)) if attempts else None,
        "median_days": _median([item["days"] for item in passes]),
        "fail_reasons": reasons,
        "passes_also_40": sum(1 for item in passes if item.get("also_40")),
    }
