"""Authority-plane values approved in CN-CP-003 / CN-CP-003-R1 (2026-10-04).

They change only through a Governance Event appended to the doctrine;
tests/test_authority.py fails when a value here differs from the latest
approved value in the doctrine (§66: no silent parameter changes).
They apply to backtests and gates G7/G8. EXECUTION = OFF.
"""

from decimal import Decimal
from fractions import Fraction

from .gates import gate_result

# A. G8 acceptance criteria (57.1–57.5)
HOLDOUT_MONTHS = 12
WF_TRAIN_MONTHS = 24
WF_TEST_MONTHS = 6
MIN_OOS_WINDOWS = 6
BOOTSTRAP_RESAMPLES = 10_000
CI_ALPHA = Decimal("0.05")              # 57.4 b: level 1 − 0.05 / N
MIN_OOS_TRADES = 100
MIN_TRADES_PER_WINDOW = 10
MIN_PROFIT_FACTOR_OOS = Decimal("1.2")
MIN_OOS_TO_IS_MEAN_R = Decimal("0.5")
MIN_POSITIVE_WINDOWS = Fraction(2, 3)
PARAM_PERTURBATION = Decimal("0.20")
MIN_ROBUST_NEIGHBOURS = Fraction(80, 100)
COST_STRESS_MULTIPLIER = 2
REGIME_MIN_TRADES = 30

# B. Risk parameters for backtests (42.1–42.2)
RISK = {
    "MAX_RISK_PER_TRADE_PCT": Decimal("1.0"),
    "DAILY_LOSS_LIMIT_PCT": Decimal("3.0"),
    "MAX_DRAWDOWN_LIMIT_PCT": Decimal("15"),
    "MAX_LEVERAGE": Decimal("3"),
    "FEE_MAKER_PCT": Decimal("0.0200"),
    "FEE_TAKER_PCT": Decimal("0.0500"),
    "SLIPPAGE_BPS_PER_SIDE": Decimal("2"),
    "PROP_FIRM": "N/A",
    "LIQUIDATION_BUFFER_STOPS": Decimal("1"),
    "RISK_METHOD": "ENTRY",
}
RISK_METHODS = ("ENTRY", "CLOSE", "ATR")  # §44: one per strategy

# D. Indicators (29.1, 31.4)
FIB_LEVELS = ("0.236", "0.382", "0.5", "0.618", "0.786")
WARMUP_BARS_PER_LENGTH = 10


def risk_config_status(cfg: dict) -> tuple[str, list[str]]:
    """§7 status of a risk configuration: an empty field is MISSING, never a default."""
    missing = [k for k in RISK if cfg.get(k) in (None, "")]
    if missing:
        return "MISSING", [f"{k} is empty" for k in missing]
    bad = [k for k, v in cfg.items() if k in RISK and isinstance(v, Decimal) and v <= 0]
    if cfg["RISK_METHOD"] not in RISK_METHODS:
        bad.append("RISK_METHOD")
    if bad:
        return "INVALID", [f"{k} has an invalid value" for k in bad]
    return "VALID", []


def g7(cfg: dict = RISK, trade_validation: str = "MISSING") -> tuple[str, list[str]]:
    """G7 RISK VALIDATION = the risk configuration plus trade-level checks (§43).

    Trade-level validation (position size, stop distance, daily loss, liquidation
    buffer per simulated trade) belongs to the backtester, which does not exist
    yet; until it reports a status, G7 cannot pass.
    """
    status, problems = risk_config_status(cfg)
    if trade_validation == "MISSING":
        problems.append("trade-level risk validation (§43) has not run")
    return gate_result([status, trade_validation]), problems
