from decimal import Decimal as D
from fractions import Fraction as F

import pytest

from cyberninja import authority as A
from cyberninja.backtest import (
    FundingSeries, Signal, backtest, backtest_research, mean_r, oos_windows, trade_level_status, walk_forward)
from cyberninja.data.fetch import period_start_ms
from cyberninja.research import registry as R
from cyberninja.research.g8 import G8Inputs, evaluate
from test_data_g0_g1 import H4, T0, klines, row

DAY = 86_400_000
HOUR = 3_600_000


def bar(i, o, h, l, c, step=H4):
    return klines([row(T0 + i * step, o=str(o), h=str(h), l=str(l), c=str(c), step=step)])[0]


def flat(n, step=H4):
    return [bar(i, 100, 101, 99, 100, step) for i in range(n)]


def only(result):
    assert len(result["trades"]) == 1, result
    return result["trades"][0]


# --- fills, exits and R ------------------------------------------------------------------

def test_long_target_without_costs():
    t = only(backtest([bar(0, 100, 101, 99, 100.5), bar(1, 100.5, 105, 100, 104.5)],
                      [Signal("s1", T0, "LONG", "98", "104")], cost_multiplier=0))
    assert (t["exit_reason"], D(t["entry_fill"]), D(t["exit_fill"]), D(t["qty"])) == ("TARGET", 100, 104, 50)
    assert D(t["r"]) == 2 and D(t["pnl"]) == 200 and t["entry_time"] == T0


def test_costs_follow_the_documented_model():
    candles = [bar(0, 100, 101, 99, 100.5), bar(1, 100.5, 105, 100, 104.5)]
    t = only(backtest(candles, [Signal("s1", T0, "LONG", "98", "104")]))
    slip, fee = F(2, 10000), F(5, 10000)                      # 2 bps per side; taker 0.05 % (R1)
    fill_in, fill_out = 100 * (1 + slip), 104 * (1 - slip)
    qty = F(100) / (fill_in - 98)                             # 1 % of 10 000 over the entry-to-stop distance
    pnl = (fill_out - fill_in) * qty - (fill_in + fill_out) * qty * fee
    assert abs(F(t["r"]) - pnl / 100) < F(1, 10 ** 20)
    assert D(t["fees"]) > 0 and t["funding_status"] == "MISSING"   # no funding data was given


def test_stop_is_assumed_first_when_one_candle_touches_both():
    t = only(backtest([bar(0, 100, 101, 99, 100), bar(1, 100, 105, 97, 100)],
                      [Signal("s1", T0, "LONG", "98", "104")], cost_multiplier=0))
    assert t["exit_reason"] == "STOP" and D(t["r"]) == -1


@pytest.mark.parametrize("next_open,reason,r", [(97, "STOP_GAP", "-1.5"), (105, "TARGET_GAP", "2.5")])
def test_gap_fills_at_the_open(next_open, reason, r):
    t = only(backtest([bar(0, 100, 101, 99, 100), bar(1, next_open, next_open + 1, next_open - 1, next_open)],
                      [Signal("s1", T0, "LONG", "98", "104")], cost_multiplier=0))
    assert t["exit_reason"] == reason and D(t["exit_fill"]) == next_open and D(t["r"]) == D(r)


def test_short_mirrors_long():
    t = only(backtest([bar(0, 100, 101, 99, 100), bar(1, 99, 99.5, 95, 96)],
                      [Signal("s1", T0, "SHORT", "102", "96")], cost_multiplier=0))
    assert t["exit_reason"] == "TARGET" and D(t["r"]) == 2 and D(t["qty"]) == 50


def test_leverage_cap_limits_size_and_r_uses_the_risk_taken():
    r = backtest([bar(0, 100, 101, 99.5, 100)], [Signal("s1", T0, "LONG", "99.9")], cost_multiplier=0)
    t = only(r)
    assert t["leverage_capped"] and D(t["leverage"]) == 3 and D(t["qty"]) == 300 and D(t["risk"]) == 30
    assert D(t["r"]) == -1 and D(r["equity_end"]) == 9970


def test_end_of_data_closes_at_the_last_close():
    t = only(backtest([bar(0, 100, 101, 99, 100), bar(1, 100, 104, 99, 103)],
                      [Signal("s1", T0, "LONG", "90")], cost_multiplier=0))
    assert t["exit_reason"] == "END" and D(t["exit_fill"]) == 103 and D(t["r"]) == D("0.3")


# --- what may not be traded -----------------------------------------------------------------

def reasons(result):
    return {x["ref"]: x["reason"] for x in result["rejected"]}


def test_invalid_signals_are_rejected_with_a_reason():
    r = backtest(flat(2), [Signal("at_open", T0, "LONG", "100"), Signal("above", T0, "LONG", "101"),
                           Signal("target_below", T0, "LONG", "98", "99"), Signal("side", T0, "BUY", "98")])
    assert r["trades"] == [] and reasons(r) == {
        "at_open": "INVALID_STOP", "above": "INVALID_STOP", "target_below": "INVALID_TARGET", "side": "INVALID_SIDE"}


def test_one_position_at_a_time():
    r = backtest(flat(4), [Signal("a", T0, "LONG", "90"), Signal("b", T0 + H4, "LONG", "90")])
    assert reasons(r) == {"b": "POSITION_OPEN"}
    gap = [bar(0, 100, 101, 99, 100), bar(1, 97, 98, 96, 97), bar(2, 97, 98, 96, 97)]
    r = backtest(gap, [Signal("a", T0, "LONG", "98"), Signal("b", T0 + H4, "LONG", "90")])
    assert reasons(r) == {"b": "POSITION_OPEN"} and r["trades"][0]["exit_reason"] == "STOP_GAP"


def test_signal_is_filled_at_the_first_open_at_or_after_available_from():
    t = only(backtest(flat(4), [Signal("s1", T0 + H4 + 1, "LONG", "90")], cost_multiplier=0))
    assert t["entry_time"] == T0 + 2 * H4


def test_signals_outside_the_data_are_out_of_range():
    r = backtest(flat(2), [Signal("early", T0 - 1, "LONG", "90"), Signal("late", T0 + H4 + 1, "LONG", "90")])
    assert reasons(r) == {"early": "OUT_OF_RANGE", "late": "OUT_OF_RANGE"} and r["trades"] == []


def test_daily_loss_limit_blocks_the_rest_of_the_day():
    day1 = [bar(i, 100, 100.5, 97, 97.5) for i in range(6)]
    day2 = [bar(6 + i, 100, 100.5, 97, 97.5) for i in range(6)]
    sigs = [Signal(f"d1_{i}", T0 + i * H4, "LONG", "98") for i in range(5)] + [Signal("d2", T0 + 6 * H4, "LONG", "98")]
    r = backtest(day1 + day2, sigs, cost_multiplier=0)
    # after 3 losses equity is 2.9701 % down (< 3 %), after 4 losses 3.940399 % (>= 3 %)
    assert reasons(r) == {"d1_4": "DAILY_LOSS_LIMIT"} and [t["ref"] for t in r["trades"]][-1] == "d2"
    assert D(r["equity_end"]) == D(10000) * D("0.99") ** 5


def test_backtest_refuses_candles_with_gaps_or_out_of_order():
    with pytest.raises(ValueError):
        backtest([bar(0, 100, 101, 99, 100), bar(2, 100, 101, 99, 100)], [])
    with pytest.raises(ValueError):
        backtest([bar(1, 100, 101, 99, 100), bar(0, 100, 101, 99, 100)], [])


# --- funding (42.2) ----------------------------------------------------------------------------

def funding(*events, until=T0 + 3 * DAY):
    return FundingSeries(tuple((t, rate, "100") for t, rate in events), T0, until)


def four_h_stop_at_12():
    return [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100), bar(2, 100, 101, 99, 100), bar(3, 100, 101, 97, 98)]


def test_funding_counts_events_while_held():
    ev = funding((T0, "0.0001"), (T0 + 8 * HOUR, "0.0001"), (T0 + 16 * HOUR, "0.0001"))
    t = only(backtest(four_h_stop_at_12(), [Signal("s", T0, "LONG", "98")], funding=ev, cost_multiplier=0))
    # 00:00 is the entry instant (fill just after it), 16:00 is after the exit candle: only 08:00 counts
    assert t["funding_status"] == "VALID" and D(t["funding"]) == D("0.5") and D(t["r"]) == D("-1.005")
    short = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100), bar(2, 100, 101, 99, 100), bar(3, 100, 103, 99, 102)]
    t = only(backtest(short, [Signal("s", T0, "SHORT", "102")], funding=ev, cost_multiplier=0))
    assert D(t["funding"]) == D("-0.5") and D(t["r"]) == D("-0.995")      # a short receives a positive rate


def test_funding_not_covered_is_missing_and_blocks_g8_input():
    ev = funding((T0 + 8 * HOUR, "0.0001"), until=T0 + 12 * HOUR)       # ends before the exit candle closes
    r = backtest(four_h_stop_at_12(), [Signal("s", T0, "LONG", "98")], funding=ev, cost_multiplier=0)
    assert only(r)["funding_status"] == "MISSING" and r["funding_status"] == "MISSING"
    assert oos_windows(r, [(T0, T0 + DAY)]) is None


def daily(*bars):
    return [bar(i, *b, step=DAY) for i, b in enumerate(bars)]


@pytest.mark.parametrize("bars,events,expected", [
    # stop inside day 2: an event at 08:00 of day 2 may fall before or after the exit
    (((100, 101, 99, 100), (100, 101, 97, 98)), [(T0 + DAY + 8 * HOUR, "0.0001")], "0.5"),     # a cost: counted
    (((100, 101, 99, 100), (100, 101, 97, 98)), [(T0 + DAY + 8 * HOUR, "-0.0001")], "0"),      # a credit: not counted
    (((100, 101, 99, 100), (100, 101, 97, 98)), [(T0 + 8 * HOUR, "-0.0001")], "-0.5"),         # surely held: counted
    # gap at the open of day 2: held at 00:00 of day 2, closed before 08:00
    (((100, 101, 99, 100), (97, 98, 96, 97)), [(T0 + DAY, "0.0001"), (T0 + DAY + 8 * HOUR, "0.0001")], "0.5"),
    # end of data: held until the last close
    (((100, 101, 99, 100),), [(T0 + 8 * HOUR, "-0.0001"), (T0 + 16 * HOUR, "0.0001")], "0"),
])
def test_funding_inside_the_exit_candle(bars, events, expected):
    t = only(backtest(daily(*bars), [Signal("s", T0, "LONG", "98")], funding=funding(*events), cost_multiplier=0))
    assert D(t["funding"]) == D(expected)


# --- costs × 2, drawdown -------------------------------------------------------------------------

def test_cost_multiplier_two_doubles_fees_and_slippage():
    candles = [bar(0, 100, 101, 99, 100.5), bar(1, 100.5, 105, 100, 104.5)]
    r1, r2 = (backtest(candles, [Signal("s1", T0, "LONG", "98", "104")], cost_multiplier=k) for k in (1, 2))
    assert r2["costs"]["fee_taker_pct"] == "0.1000" and r2["costs"]["slippage_bps"] == "4"
    assert D(mean_r(r2)) < D(mean_r(r1)) < 2


def test_max_drawdown_includes_the_intrabar_trough():
    r = backtest([bar(0, 100, 101, 95, 100), bar(1, 100, 111, 99, 110)],
                 [Signal("s1", T0, "LONG", "90", "110")], cost_multiplier=0)
    assert only(r)["exit_reason"] == "TARGET" and D(r["max_drawdown_pct"]) == D("0.5") and D(r["equity_end"]) == 10100


# --- G7 / G8 wiring ------------------------------------------------------------------------------

def test_walk_forward_layout_matches_r1():
    holdout, windows = walk_forward("2020-01", "2026-09")
    assert holdout == period_start_ms("2025-10") == R.holdout_start("2026-09")
    assert len(windows) == 7 and windows[0] == (period_start_ms("2022-01"), period_start_ms("2022-07"))
    assert windows[-1] == (period_start_ms("2025-01"), period_start_ms("2025-07"))


def test_trade_level_status_for_g7():
    r = backtest(flat(2), [Signal("s", T0, "LONG", "90")])
    assert trade_level_status(r) == "UNKNOWN"                          # liquidation model not approved
    assert A.g7(trade_validation=trade_level_status(r))[0] == "UNKNOWN"
    assert trade_level_status(backtest(flat(2), [])) == "MISSING"
    t = r["trades"][0]
    limit = D(t["equity_before"]) / 100
    t["risk"] = str(limit * (1 + D("1e-27")))                           # 28-digit rounding: still within the limit
    assert trade_level_status(r) == "UNKNOWN"
    t["risk"] = str(limit * (1 + D("1e-6")))
    assert trade_level_status(r) == "INVALID"


def test_backtest_feeds_g8(tmp_path):
    path = tmp_path / "registry.jsonl"
    R.register(path, hypothesis_id="h1", family="f", params={}, dataset_hash="ds_" + "a" * 64,
               holdout_start=T0 + 10 * DAY, at=1)
    candles = [bar(i, 100, 101, 99, 100) for i in range(12)]
    ev = FundingSeries((), T0, T0 + 10 * DAY)
    r = backtest_research(path, "h1", candles, [Signal("s", T0, "LONG", "98", "100.5")], funding=ev, cost_multiplier=0)
    windows = oos_windows(r, [(T0, T0 + DAY)])
    assert windows == [[r["trades"][0]["r"]]]
    result = evaluate(path, "h1", G8Inputs(oos_windows=windows), seed=1, at=2)
    assert result["criteria"]["a_trades"]["value"].startswith("1 trades") and result["G8"] in ("FAIL", "MISSING")


def test_backtest_research_never_sees_the_holdout(tmp_path):
    path = tmp_path / "registry.jsonl"
    with pytest.raises(R.NotRegistered):
        backtest_research(path, "h1", flat(4), [])
    R.register(path, hypothesis_id="h1", family="f", params={}, dataset_hash="ds_" + "a" * 64,
               holdout_start=T0 + 2 * H4, at=1)
    r = backtest_research(path, "h1", flat(4), [Signal("in", T0, "LONG", "90"), Signal("holdout", T0 + 2 * H4, "LONG", "90")])
    assert reasons(r) == {"holdout": "OUT_OF_RANGE"}
    assert only(r)["exit_reason"] == "END" and only(r)["exit_time"] == T0 + H4   # closed at the last research candle
