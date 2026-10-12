from topstepbot.sizing import contracts_for_risk, split_quantity


def test_size_is_risk_over_stop_cost_then_capped():
    # 16 ticks * $1.25 + 1 tick slippage + $1.40 fee = $22.65; 200/22.65 = 8, cap 2.
    qty = contracts_for_risk(
        entry=5000,
        stop=4996,
        tick_size=0.25,
        tick_value=1.25,
        risk_per_trade=200,
        slippage_ticks=1,
        round_turn_fee=1.40,
        max_contracts=2,
        topstep_max_contracts=50,
    )
    assert qty == 2


def test_wide_stop_is_skipped():
    qty = contracts_for_risk(
        entry=5000,
        stop=4800,
        tick_size=0.25,
        tick_value=1.25,
        risk_per_trade=200,
        slippage_ticks=1,
        round_turn_fee=1.40,
        max_contracts=2,
        topstep_max_contracts=50,
    )
    assert qty == 0


def test_size_respects_the_topstep_cap():
    qty = contracts_for_risk(
        entry=5000,
        stop=4999.75,
        tick_size=0.25,
        tick_value=1.25,
        risk_per_trade=5000,
        slippage_ticks=0,
        round_turn_fee=0,
        max_contracts=40,
        topstep_max_contracts=10,
    )
    assert qty == 10


def test_one_contract_exits_fully_at_the_first_target():
    assert split_quantity(1, 0.5) == (1, 0)
    assert split_quantity(2, 0.5) == (1, 1)
    assert split_quantity(4, 0.5) == (2, 2)
