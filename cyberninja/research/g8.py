"""G8 OOS / ROBUSTNESS per CN-CP-003 57.4–57.5 with the approved R1 thresholds.

The inputs are results of a backtest that does not exist yet. Every trade
result is in R after fees, slippage and funding (57.4). A metric that is not
given is MISSING, so G8 cannot pass on partial evidence. The result, with N
and the bootstrap seed, is appended to the research registry (57.3).

57.4 b uses a two-sided percentile bootstrap at level 1 − 0.05/N: the lower
bound is the k-th smallest of the B resampled means, k = max(1, ⌊0.025/N · B⌋)
computed exactly (lower_bound_rank).
Threshold checks use Decimal; only the bootstrap uses floats.
"""

import math
import platform
import random
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from .. import authority as A
from ..gates import gate_result
from . import registry


@dataclass
class G8Inputs:
    oos_windows: list | None = None        # per walk-forward OOS window: trade results in R
    is_mean_r: object = None               # in-sample mean R of the same candidate (57.4 d)
    neighbour_mean_r: list | None = None   # mean R of each ±20 % parameter neighbour (57.4 f)
    oos_mean_r_costs_x2: object = None     # OOS mean R with fees and slippage × 2 (57.4 g)
    oos_max_drawdown_pct: object = None    # max drawdown of the OOS equity curve, in % (57.4 h)
    holdout_mean_r: object = None          # mean R on the holdout, final G8 only (57.4 i)
    oos_by_regime: dict | None = None      # regime -> OOS trade results in R (57.5)


def _d(x) -> Decimal:
    if isinstance(x, bool):
        raise TypeError("bool is not a number here")
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(x)


def _mean(xs: list) -> Decimal:
    return sum(xs, Decimal(0)) / len(xs)


def _c(ok: bool, value, threshold) -> dict:
    return {"status": "PASS" if ok else "FAIL", "value": str(value), "threshold": str(threshold)}


def _missing(what: str) -> dict:
    return {"status": "MISSING", "value": None, "threshold": what}


def lower_bound_rank(n_variants: int) -> int:
    return max(1, math.floor(Fraction(A.CI_ALPHA) / n_variants / 2 * A.BOOTSTRAP_RESAMPLES))


def bootstrap_lower(rs: list, n_variants: int, seed: int) -> float:
    xs = [float(r) for r in rs]
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(xs, k=len(xs))) / len(xs) for _ in range(A.BOOTSTRAP_RESAMPLES))
    return means[lower_bound_rank(n_variants) - 1]


def criteria(inp: G8Inputs, n: int, seed: int, holdout_opened: bool) -> dict:
    c = {}
    windows = None if inp.oos_windows is None else [[_d(r) for r in w] for w in inp.oos_windows]
    pooled = [r for w in windows for r in w] if windows else []
    if windows is None:
        c["a_trades"] = _missing("OOS windows")
    else:
        per = min((len(w) for w in windows), default=0)
        c["a_trades"] = _c(len(windows) >= A.MIN_OOS_WINDOWS and len(pooled) >= A.MIN_OOS_TRADES
                           and per >= A.MIN_TRADES_PER_WINDOW,
                           f"{len(pooled)} trades, {len(windows)} windows, min {per} per window",
                           f">= {A.MIN_OOS_TRADES} trades, >= {A.MIN_OOS_WINDOWS} windows, "
                           f">= {A.MIN_TRADES_PER_WINDOW} per window")
    if not pooled:
        for k in ("b_mean_r_ci", "c_profit_factor", "d_oos_vs_is"):
            c[k] = _missing("OOS trades")
    else:
        mean = _mean(pooled)
        lower = bootstrap_lower(pooled, n, seed)
        c["b_mean_r_ci"] = _c(mean > 0 and lower > 0, f"mean {mean}, lower bound {lower!r}",
                              f"mean > 0 and lower bound > 0 at level 1 - {A.CI_ALPHA}/{n}")
        gains, losses = sum((r for r in pooled if r > 0), Decimal(0)), -sum((r for r in pooled if r < 0), Decimal(0))
        if losses == 0:
            c["c_profit_factor"] = _c(gains > 0, "inf" if gains > 0 else "undefined", f">= {A.MIN_PROFIT_FACTOR_OOS}")
        else:
            c["c_profit_factor"] = _c(gains / losses >= A.MIN_PROFIT_FACTOR_OOS, gains / losses,
                                      f">= {A.MIN_PROFIT_FACTOR_OOS}")
        if inp.is_mean_r is None:
            c["d_oos_vs_is"] = _missing("in-sample mean R")
        else:
            floor = A.MIN_OOS_TO_IS_MEAN_R * _d(inp.is_mean_r)
            c["d_oos_vs_is"] = _c(mean >= floor, mean, f">= {floor}")
    if not windows:
        c["e_positive_windows"] = _missing("OOS windows")
    else:
        positive = sum(1 for w in windows if w and _mean(w) > 0)
        c["e_positive_windows"] = _c(Fraction(positive, len(windows)) >= A.MIN_POSITIVE_WINDOWS,
                                     f"{positive}/{len(windows)}", f">= {A.MIN_POSITIVE_WINDOWS}")
    if not inp.neighbour_mean_r:
        c["f_parameter_robustness"] = _missing("parameter neighbours")
    else:
        ns = [_d(x) for x in inp.neighbour_mean_r]
        positive = sum(1 for x in ns if x > 0)
        c["f_parameter_robustness"] = _c(Fraction(positive, len(ns)) >= A.MIN_ROBUST_NEIGHBOURS,
                                         f"{positive}/{len(ns)}", f">= {A.MIN_ROBUST_NEIGHBOURS}")
    if inp.oos_mean_r_costs_x2 is None:
        c["g_costs_x2"] = _missing(f"OOS mean R with costs x {A.COST_STRESS_MULTIPLIER}")
    else:
        c["g_costs_x2"] = _c(_d(inp.oos_mean_r_costs_x2) > 0, _d(inp.oos_mean_r_costs_x2), "> 0")
    if inp.oos_max_drawdown_pct is None:
        c["h_max_drawdown"] = _missing("OOS max drawdown")
    else:
        limit = A.RISK["MAX_DRAWDOWN_LIMIT_PCT"]
        c["h_max_drawdown"] = _c(_d(inp.oos_max_drawdown_pct) <= limit, _d(inp.oos_max_drawdown_pct), f"<= {limit}")
    if inp.holdout_mean_r is None:
        c["i_holdout"] = _missing("holdout mean R")
    elif not holdout_opened:
        c["i_holdout"] = _c(False, "holdout result without a recorded opening (57.1)", "> 0")
    else:
        c["i_holdout"] = _c(_d(inp.holdout_mean_r) > 0, _d(inp.holdout_mean_r), "> 0")
    if inp.oos_by_regime is None:
        c["regime"] = _missing("OOS results by regime")
    else:
        losing = sorted(name for name, rs in inp.oos_by_regime.items()
                        if len(rs) >= A.REGIME_MIN_TRADES and _mean([_d(r) for r in rs]) < 0)
        c["regime"] = _c(not losing, f"losing regimes: {losing}",
                         f"no regime with >= {A.REGIME_MIN_TRADES} trades and mean R < 0")
    return c


def evaluate(path, hypothesis_id: str, inputs: G8Inputs, seed: int, at: int) -> dict:
    reg = registry.registration(path, hypothesis_id)   # no pre-registration, no test (57.3)
    n = registry.n_variants(path, reg["family"])
    c = criteria(inputs, n, seed, registry.holdout_opened_by(path, hypothesis_id))
    status = {"PASS": "VALID", "FAIL": "INVALID", "MISSING": "MISSING"}
    result = {
        "G8": gate_result([status[x["status"]] for x in c.values()]),
        "criteria": c, "n_variants": n, "seed": seed, "bootstrap_resamples": A.BOOTSTRAP_RESAMPLES,
        "thresholds": registry.THRESHOLDS, "python": platform.python_version(),
    }
    registry.record_g8(path, hypothesis_id, result, at)
    return result
