"""EMA, ATR, VWAP, swings, and the candle tests from the rulebook.

Every threshold is passed in by the caller from config. Nothing here is a
secret trading constant.
"""

from __future__ import annotations

from topstepbot.models import Bar, Side


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def true_ranges(bars: list[Bar]) -> list[float]:
    ranges: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            ranges.append(bar.high - bar.low)
            continue
        prev_close = bars[i - 1].close
        ranges.append(
            max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        )
    return ranges


def atr(bars: list[Bar], period: int) -> list[float | None]:
    """Wilder ATR."""
    out: list[float | None] = [None] * len(bars)
    if period <= 0 or len(bars) < period:
        return out
    ranges = true_ranges(bars)
    prev = sum(ranges[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(ranges)):
        prev = (prev * (period - 1) + ranges[i]) / period
        out[i] = prev
    return out


def macd(
    values: list[float], fast: int, slow: int, signal: int
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (macd line, signal line, histogram). Unused unless the filter is on."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line: list[float | None] = [None] * len(values)
    for i, (f, s) in enumerate(zip(fast_ema, slow_ema)):
        if f is not None and s is not None:
            line[i] = f - s
    # Signal EMA over the compact MACD series, then map back.
    compact = [v for v in line if v is not None]
    signal_compact = ema(compact, signal)
    sig: list[float | None] = [None] * len(values)
    hist: list[float | None] = [None] * len(values)
    j = 0
    for i, value in enumerate(line):
        if value is None:
            continue
        sig[i] = signal_compact[j]
        if signal_compact[j] is not None:
            hist[i] = value - signal_compact[j]
        j += 1
    return line, sig, hist


def vwap_from_anchor(bars: list[Bar]) -> float | None:
    """Session VWAP. Caller passes only bars since the anchor."""
    pv = 0.0
    vol = 0.0
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3.0
        vol += bar.volume
        pv += typical * bar.volume
    if vol <= 0:
        return None
    return pv / vol


def swing_points(bars: list[Bar], wing: int, kind: str) -> list[tuple[int, float]]:
    """Fractal swings. A point at index i is confirmed once i+wing exists.

    kind is "high" or "low". The swing must be strictly beyond both wings.
    """
    found: list[tuple[int, float]] = []
    if wing < 1 or len(bars) < wing * 2 + 1:
        return found
    for i in range(wing, len(bars) - wing):
        if kind == "high":
            price = bars[i].high
            if all(price > bars[i - k].high for k in range(1, wing + 1)) and all(
                price > bars[i + k].high for k in range(1, wing + 1)
            ):
                found.append((i, price))
        else:
            price = bars[i].low
            if all(price < bars[i - k].low for k in range(1, wing + 1)) and all(
                price < bars[i + k].low for k in range(1, wing + 1)
            ):
                found.append((i, price))
    return found


def uptrend(bars: list[Bar], wing: int) -> bool:
    highs = swing_points(bars, wing, "high")
    lows = swing_points(bars, wing, "low")
    if len(highs) < 2 or len(lows) < 2:
        return False
    return highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]


def downtrend(bars: list[Bar], wing: int) -> bool:
    highs = swing_points(bars, wing, "high")
    lows = swing_points(bars, wing, "low")
    if len(highs) < 2 or len(lows) < 2:
        return False
    return highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]


def trend_for(bars: list[Bar], wing: int, side: Side) -> bool:
    return uptrend(bars, wing) if side is Side.LONG else downtrend(bars, wing)


def ema_cross_count(closes: list[float], emas: list[float | None], lookback: int) -> int:
    start = max(1, len(closes) - lookback)
    crosses = 0
    prev_sign = None
    for i in range(start, len(closes)):
        if emas[i] is None:
            continue
        diff = closes[i] - emas[i]
        sign = 1 if diff > 0 else (-1 if diff < 0 else 0)
        if sign == 0:
            continue
        if prev_sign is not None and sign != prev_sign:
            crosses += 1
        prev_sign = sign
    return crosses


def is_chop(
    closes: list[float],
    emas: list[float | None],
    atr_now: float | None,
    *,
    cross_limit: int,
    cross_lookback: int,
    slope_bars: int,
    slope_atr_fraction: float,
) -> bool:
    if ema_cross_count(closes, emas, cross_lookback) >= cross_limit:
        return True
    if atr_now is None or emas[-1] is None:
        return True
    if len(emas) <= slope_bars or emas[-1 - slope_bars] is None:
        return True
    slope = abs(emas[-1] - emas[-1 - slope_bars])
    return slope < slope_atr_fraction * atr_now


def is_strong_break(
    bar: Bar,
    level: float,
    side: Side,
    tick: float,
    body_fraction: float,
    close_location: float,
) -> bool:
    rng = bar.high - bar.low
    if rng <= 0:
        return False
    if side is Side.LONG:
        body = bar.close - bar.open
        if body < body_fraction * rng:
            return False
        if bar.close < level + tick - 1e-9:
            return False
        if (bar.close - bar.low) / rng < close_location - 1e-12:
            return False
        return True
    body = bar.open - bar.close
    if body < body_fraction * rng:
        return False
    if bar.close > level - tick + 1e-9:
        return False
    if (bar.high - bar.close) / rng < close_location - 1e-12:
        return False
    return True


def volume_confirmed(volumes: list[float], lookback: int, multiple: float) -> bool:
    if len(volumes) < lookback + 1:
        return False
    prior = volumes[-(lookback + 1) : -1]
    avg = sum(prior) / len(prior)
    if avg <= 0:
        return False
    return volumes[-1] >= multiple * avg


def is_retest_bar(bar: Bar, level: float, side: Side, tolerance: float) -> bool:
    """Price comes back to within `tolerance` of the broken level and closes back beyond it."""
    if side is Side.LONG:
        touched = (level - tolerance - 1e-9) <= bar.low <= (level + tolerance + 1e-9)
        held = bar.close > level + 1e-9
        return touched and held
    touched = (level - tolerance - 1e-9) <= bar.high <= (level + tolerance + 1e-9)
    held = bar.close < level - 1e-9
    return touched and held


def round_to_tick(price: float, tick: float) -> float:
    steps = round(price / tick)
    return round(steps * tick, 10)
