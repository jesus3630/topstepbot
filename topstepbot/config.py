"""Load the single YAML settings file into typed objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import yaml

LIVE_FUNDED_OVERRIDE = (
    "I_UNDERSTAND_PROJECTX_CANNOT_TRADE_TOPSTEP_LIVE_FUNDED_ACCOUNTS"
)


def _parse_hhmm(value: str) -> time:
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"expected HH:MM, got {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"clock time out of range: {value!r}")
    return time(hour, minute)


@dataclass(frozen=True)
class InstrumentConfig:
    symbol: str
    tick_size: float
    tick_value: float
    contract_id: str


@dataclass(frozen=True)
class AccountConfig:
    label: str
    starting_balance: float
    account_id: int | None
    account_name_contains: str
    kind: str


@dataclass(frozen=True)
class SessionConfig:
    timezone: str
    rth_open: time
    opening_range_end: time
    entry_start: time
    entry_end: time
    flatten_time: time
    hard_flat_time: time
    prior_rth_end: time
    vwap_anchor: time
    setup_b_entry_end: time


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float
    daily_max_loss: float
    daily_profit_stop: float
    max_trades_per_day: int
    max_consecutive_losses: int
    max_contracts: int
    slippage_ticks: int
    round_turn_fee_per_contract: float
    topstep_max_loss: float
    topstep_max_contracts: int
    mll_halve_risk_multiple: float
    mll_stop_multiple: float


@dataclass(frozen=True)
class ExitConfig:
    partial_fraction: float
    min_reward_risk: float
    move_stop_to_breakeven_after_first_target: bool
    trail_ticks_beyond_swing: int
    stop_buffer_ticks: int
    time_exit_minutes: int
    volatility_exit_atr_multiple: float
    lyl_invalidation_r_fraction: float
    no_chase_r: float
    retest_tolerance_ticks: int
    retest_timeout_bars: int
    fakeout_bars: int
    entry_stop_offset_ticks: int
    momentum_fade_bars: int
    entry_order_timeout_bars: int
    cooldown_bars_after_stop: int


@dataclass(frozen=True)
class FilterConfig:
    signal_timeframe_minutes: int
    volume_lookback: int
    volume_multiple: float
    strong_candle_body_fraction: float
    strong_candle_close_location: float
    ema_period: int
    atr_period: int
    overextended_atr: float
    chop_ema_crosses: int
    chop_lookback_bars: int
    chop_slope_bars: int
    chop_slope_atr_fraction: float
    swing_bars_each_side: int
    round_number_points: float
    require_index_agreement: bool
    use_macd_filter: bool
    macd_fast: int
    macd_slow: int
    macd_signal: int
    setup_a_enabled: bool
    setup_a_entry_mode: str
    setup_b_enabled: bool
    setup_b_levels: tuple[str, ...]


@dataclass(frozen=True)
class IntradayBlackout:
    on_date: str
    start: time
    end: time


@dataclass(frozen=True)
class NewsConfig:
    no_trade_today: bool
    blackout_dates: tuple[str, ...]
    intraday_blackouts: tuple[IntradayBlackout, ...]


@dataclass(frozen=True)
class ComplianceConfig:
    min_seconds_between_orders: int
    min_hold_seconds: int
    allow_live_funded_account_override: str


@dataclass(frozen=True)
class RuntimeConfig:
    armed: bool
    kill_switch_file: str
    log_dir: str
    poll_seconds: int
    flatten_on_start: bool
    quote_timeout_seconds: int


@dataclass(frozen=True)
class BrokerConfig:
    projectx_use_live_data: bool


@dataclass(frozen=True)
class BotConfig:
    instrument: InstrumentConfig
    account: AccountConfig
    session: SessionConfig
    risk: RiskConfig
    exits: ExitConfig
    filters: FilterConfig
    news: NewsConfig
    compliance: ComplianceConfig
    runtime: RuntimeConfig
    broker: BrokerConfig
    source_path: str = ""

    @property
    def dollars_per_point(self) -> float:
        return self.instrument.tick_value / self.instrument.tick_size


def load_config(path: str | Path) -> BotConfig:
    raw_path = Path(path)
    with raw_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    cfg = _build(data, str(raw_path))
    _validate(cfg)
    return cfg


def _build(data: dict, source: str) -> BotConfig:
    instrument = data["instrument"]
    account = data["account"]
    session = data["session"]
    risk = data["risk"]
    exits = data["exits"]
    filters = data["filters"]
    news = data["news"]
    compliance = data["compliance"]
    runtime = data["runtime"]
    broker = data["broker"]

    blackouts = []
    for item in news.get("intraday_blackouts") or []:
        blackouts.append(
            IntradayBlackout(
                on_date=str(item["date"]),
                start=_parse_hhmm(item["start"]),
                end=_parse_hhmm(item["end"]),
            )
        )

    account_id = account.get("account_id")
    if account_id in ("", None):
        account_id = None
    else:
        account_id = int(account_id)

    return BotConfig(
        instrument=InstrumentConfig(
            symbol=str(instrument["symbol"]).upper(),
            tick_size=float(instrument["tick_size"]),
            tick_value=float(instrument["tick_value"]),
            contract_id=str(instrument.get("contract_id") or "").strip(),
        ),
        account=AccountConfig(
            label=str(account["label"]),
            starting_balance=float(account["starting_balance"]),
            account_id=account_id,
            account_name_contains=str(account.get("account_name_contains") or ""),
            kind=str(account.get("kind") or "practice").strip().lower(),
        ),
        session=SessionConfig(
            timezone=str(session["timezone"]),
            rth_open=_parse_hhmm(session["rth_open"]),
            opening_range_end=_parse_hhmm(session["opening_range_end"]),
            entry_start=_parse_hhmm(session["entry_start"]),
            entry_end=_parse_hhmm(session["entry_end"]),
            flatten_time=_parse_hhmm(session["flatten_time"]),
            hard_flat_time=_parse_hhmm(session["hard_flat_time"]),
            prior_rth_end=_parse_hhmm(session["prior_rth_end"]),
            vwap_anchor=_parse_hhmm(session["vwap_anchor"]),
            setup_b_entry_end=_parse_hhmm(session["setup_b_entry_end"]),
        ),
        risk=RiskConfig(
            risk_per_trade=float(risk["risk_per_trade"]),
            daily_max_loss=float(risk["daily_max_loss"]),
            daily_profit_stop=float(risk["daily_profit_stop"]),
            max_trades_per_day=int(risk["max_trades_per_day"]),
            max_consecutive_losses=int(risk["max_consecutive_losses"]),
            max_contracts=int(risk["max_contracts"]),
            slippage_ticks=int(risk["slippage_ticks"]),
            round_turn_fee_per_contract=float(risk["round_turn_fee_per_contract"]),
            topstep_max_loss=float(risk["topstep_max_loss"]),
            topstep_max_contracts=int(risk["topstep_max_contracts"]),
            mll_halve_risk_multiple=float(risk["mll_halve_risk_multiple"]),
            mll_stop_multiple=float(risk["mll_stop_multiple"]),
        ),
        exits=ExitConfig(
            partial_fraction=float(exits["partial_fraction"]),
            min_reward_risk=float(exits["min_reward_risk"]),
            move_stop_to_breakeven_after_first_target=bool(
                exits["move_stop_to_breakeven_after_first_target"]
            ),
            trail_ticks_beyond_swing=int(exits["trail_ticks_beyond_swing"]),
            stop_buffer_ticks=int(exits["stop_buffer_ticks"]),
            time_exit_minutes=int(exits["time_exit_minutes"]),
            volatility_exit_atr_multiple=float(exits["volatility_exit_atr_multiple"]),
            lyl_invalidation_r_fraction=float(exits["lyl_invalidation_r_fraction"]),
            no_chase_r=float(exits["no_chase_r"]),
            retest_tolerance_ticks=int(exits["retest_tolerance_ticks"]),
            retest_timeout_bars=int(exits["retest_timeout_bars"]),
            fakeout_bars=int(exits["fakeout_bars"]),
            entry_stop_offset_ticks=int(exits["entry_stop_offset_ticks"]),
            momentum_fade_bars=int(exits["momentum_fade_bars"]),
            entry_order_timeout_bars=int(exits["entry_order_timeout_bars"]),
            cooldown_bars_after_stop=int(exits["cooldown_bars_after_stop"]),
        ),
        filters=FilterConfig(
            signal_timeframe_minutes=int(filters["signal_timeframe_minutes"]),
            volume_lookback=int(filters["volume_lookback"]),
            volume_multiple=float(filters["volume_multiple"]),
            strong_candle_body_fraction=float(filters["strong_candle_body_fraction"]),
            strong_candle_close_location=float(filters["strong_candle_close_location"]),
            ema_period=int(filters["ema_period"]),
            atr_period=int(filters["atr_period"]),
            overextended_atr=float(filters["overextended_atr"]),
            chop_ema_crosses=int(filters["chop_ema_crosses"]),
            chop_lookback_bars=int(filters["chop_lookback_bars"]),
            chop_slope_bars=int(filters["chop_slope_bars"]),
            chop_slope_atr_fraction=float(filters["chop_slope_atr_fraction"]),
            swing_bars_each_side=int(filters["swing_bars_each_side"]),
            round_number_points=float(filters["round_number_points"]),
            require_index_agreement=bool(filters["require_index_agreement"]),
            use_macd_filter=bool(filters["use_macd_filter"]),
            macd_fast=int(filters["macd_fast"]),
            macd_slow=int(filters["macd_slow"]),
            macd_signal=int(filters["macd_signal"]),
            setup_a_enabled=bool(filters["setup_a_enabled"]),
            setup_a_entry_mode=str(filters["setup_a_entry_mode"]).strip().lower(),
            setup_b_enabled=bool(filters["setup_b_enabled"]),
            setup_b_levels=tuple(str(x) for x in filters["setup_b_levels"]),
        ),
        news=NewsConfig(
            no_trade_today=bool(news["no_trade_today"]),
            blackout_dates=tuple(str(d) for d in (news.get("blackout_dates") or [])),
            intraday_blackouts=tuple(blackouts),
        ),
        compliance=ComplianceConfig(
            min_seconds_between_orders=int(compliance["min_seconds_between_orders"]),
            min_hold_seconds=int(compliance["min_hold_seconds"]),
            allow_live_funded_account_override=str(
                compliance.get("allow_live_funded_account_override") or ""
            ).strip(),
        ),
        runtime=RuntimeConfig(
            armed=bool(runtime["armed"]),
            kill_switch_file=str(runtime["kill_switch_file"]),
            log_dir=str(runtime["log_dir"]),
            poll_seconds=int(runtime["poll_seconds"]),
            flatten_on_start=bool(runtime["flatten_on_start"]),
            quote_timeout_seconds=int(runtime.get("quote_timeout_seconds", 20)),
        ),
        broker=BrokerConfig(
            projectx_use_live_data=bool(broker["projectx_use_live_data"]),
        ),
        source_path=source,
    )


def _validate(cfg: BotConfig) -> None:
    if cfg.instrument.tick_size <= 0 or cfg.instrument.tick_value <= 0:
        raise ValueError("tick size and tick value must be positive")
    if cfg.account.kind not in {"practice", "combine", "express"}:
        raise ValueError("account.kind must be practice, combine, or express")
    if cfg.filters.setup_a_entry_mode not in {"retest", "classic"}:
        raise ValueError("setup_a_entry_mode must be retest or classic")
    s = cfg.session
    if not (s.rth_open < s.opening_range_end <= s.entry_start < s.entry_end < s.flatten_time):
        raise ValueError("session times must run open < range end <= entry start < entry end < flatten")
    if s.flatten_time > s.hard_flat_time:
        raise ValueError("flatten time must be at or before the 3:10 PM CT hard flat")
    if cfg.risk.risk_per_trade <= 0 or cfg.risk.daily_max_loss <= 0:
        raise ValueError("risk amounts must be positive")
    if cfg.risk.daily_max_loss >= cfg.risk.topstep_max_loss:
        raise ValueError("daily max loss must sit inside Topstep's maximum loss limit")
    if cfg.risk.max_contracts < 1:
        raise ValueError("max_contracts must be at least 1")
    if cfg.risk.max_contracts > cfg.risk.topstep_max_contracts:
        raise ValueError("max_contracts exceeds the Topstep position cap in config")
    if not 0 < cfg.exits.partial_fraction <= 1:
        raise ValueError("partial_fraction must be between 0 and 1")
    if cfg.compliance.min_seconds_between_orders < 1:
        raise ValueError("minimum order interval must be at least 1 second")
    if cfg.compliance.min_hold_seconds < 1:
        raise ValueError("minimum hold time must be at least 1 second")
