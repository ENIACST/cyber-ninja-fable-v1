import re
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from cyberninja import authority as A

DOCTRINE = (Path(__file__).resolve().parents[1] / "docs/CYBER_NINJA_v5.2_HYBRID.md").read_text("utf-8")


def latest(pattern):
    """The most recent approved value: later Governance Events come later in the doctrine."""
    found = re.findall(pattern, DOCTRINE, flags=re.M)
    assert found, f"not in the doctrine: {pattern}"
    return found[-1]


@pytest.mark.parametrize("pattern,parse,value", [
    (r"HOLDOUT_MONTHS\s+= (\d+)$", int, A.HOLDOUT_MONTHS),
    (r"WF_TRAIN_MONTHS\s+= (\d+)$", int, A.WF_TRAIN_MONTHS),
    (r"WF_TEST_MONTHS\s+= (\d+)\s", int, A.WF_TEST_MONTHS),
    (r"MIN_OOS_WINDOWS\s+= (\d+)$", int, A.MIN_OOS_WINDOWS),
    (r"BOOTSTRAP\s+= ([\d ]+?) преизвадки", lambda s: int(s.replace(" ", "")), A.BOOTSTRAP_RESAMPLES),
    (r"при ниво\s+1 − ([\d.]+) / N", Decimal, A.CI_ALPHA),
    (r"MIN_OOS_TRADES\s+= (\d+) общо", int, A.MIN_OOS_TRADES),
    (r"MIN_OOS_TRADES\s+= \d+ общо; ≥ (\d+) на прозорец", int, A.MIN_TRADES_PER_WINDOW),
    (r"MIN_PROFIT_FACTOR_OOS\s+= ([\d.]+)$", Decimal, A.MIN_PROFIT_FACTOR_OOS),
    (r"MIN_OOS_TO_IS_MEAN_R\s+= ([\d.]+)$", Decimal, A.MIN_OOS_TO_IS_MEAN_R),
    (r"MIN_POSITIVE_WINDOWS\s+= (\d+/\d+)$", Fraction, A.MIN_POSITIVE_WINDOWS),
    (r"PARAM_PERTURBATION\s+= ±(\d+) %$", lambda s: Decimal(s) / 100, A.PARAM_PERTURBATION),
    (r"MIN_ROBUST_NEIGHBOURS\s+= (\d+) %$", lambda s: Fraction(int(s), 100), A.MIN_ROBUST_NEIGHBOURS),
    (r"при такси и slippage × (\d+)", int, A.COST_STRESS_MULTIPLIER),
    (r"REGIME_MIN_TRADES\s+= (\d+)$", int, A.REGIME_MIN_TRADES),
    (r"MAX_RISK_PER_TRADE\s+= ([\d.]+) %", Decimal, A.RISK["MAX_RISK_PER_TRADE_PCT"]),
    (r"DAILY_LOSS_LIMIT\s+= ([\d.]+) %$", Decimal, A.RISK["DAILY_LOSS_LIMIT_PCT"]),
    (r"MAX_DRAWDOWN_LIMIT\s+= ([\d.]+) %$", Decimal, A.RISK["MAX_DRAWDOWN_LIMIT_PCT"]),
    (r"MAX_LEVERAGE\s+= (\d+)x$", Decimal, A.RISK["MAX_LEVERAGE"]),
    (r"FEE \(maker / taker\)\s+= ([\d.]+) %", Decimal, A.RISK["FEE_MAKER_PCT"]),
    (r"FEE \(maker / taker\)\s+= [\d.]+ % / ([\d.]+) %$", Decimal, A.RISK["FEE_TAKER_PCT"]),
    (r"SLIPPAGE\s+= (\d+) bps", Decimal, A.RISK["SLIPPAGE_BPS_PER_SIDE"]),
    (r"PROP_FIRM\s+= (\S+)$", str, A.RISK["PROP_FIRM"]),
    (r"LIQUIDATION_BUFFER\s+= (\d+) ×", Decimal, A.RISK["LIQUIDATION_BUFFER_STOPS"]),
    (r"RISK_METHOD \(по подразбиране\) = (\w+)-based", lambda s: s.upper(), A.RISK["RISK_METHOD"]),
    (r"FIB_LEVELS\s+= ([\d., ]+?)$", lambda s: tuple(s.split(", ")), A.FIB_LEVELS),
    (r"стойност преди бар (\d+) × n", int, A.WARMUP_BARS_PER_LENGTH),
])
def test_code_values_equal_the_latest_approved_values(pattern, parse, value):
    assert parse(latest(pattern)) == value


# --- T19 -----------------------------------------------------------------------

def test_t19_empty_risk_field_is_g7_missing():
    for field in A.RISK:
        for empty in (None, ""):
            cfg = A.RISK | {field: empty}
            assert A.risk_config_status(cfg)[0] == "MISSING"
            assert A.g7(cfg, trade_validation="VALID")[0] == "MISSING"


def test_g7_complete_config_needs_trade_level_validation_to_pass():
    assert A.risk_config_status(A.RISK) == ("VALID", [])
    status, problems = A.g7()
    assert status == "MISSING" and "trade-level" in problems[0]
    assert A.g7(trade_validation="VALID") == ("PASS", [])
    assert A.g7(trade_validation="INVALID")[0] == "FAIL"


@pytest.mark.parametrize("field,value", [
    ("MAX_RISK_PER_TRADE_PCT", Decimal("0")),
    ("MAX_LEVERAGE", Decimal("-3")),
    ("RISK_METHOD", "MARTINGALE"),
])
def test_invalid_risk_value_fails_g7(field, value):
    assert A.g7(A.RISK | {field: value}, trade_validation="VALID")[0] == "FAIL"
