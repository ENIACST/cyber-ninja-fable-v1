import json

import pytest

from decimal import Decimal

from cyberninja import chain
from cyberninja.data.fetch import period_start_ms
from cyberninja.research import registry as R
from cyberninja.research.g8 import G8Inputs, evaluate, lower_bound_rank
from test_data_g0_g1 import T0, klines, row

DS = "ds_" + "c" * 64
DAY, WEEK = 86_400_000, 604_800_000
HOLDOUT = period_start_ms("2024-02")          # small synthetic holdout boundary


def window(wins, losses, win="2", loss="-1"):
    return [win] * wins + [loss] * losses


WINDOWS = [window(7, 8)] * 6 + [window(6, 9)]  # 105 trades, mean 39/105, PF 96/57


def register(path, hid="h1", family="fam", **kw):
    return R.register(path, hypothesis_id=hid, family=family, params={"left": 2, "right": 2},
                      dataset_hash=DS, holdout_start=kw.get("holdout_start", HOLDOUT), at=kw.get("at", 1))


def full_inputs(**over):
    base = dict(oos_windows=WINDOWS, is_mean_r="0.6", neighbour_mean_r=["0.3", "0.25", "0.2", "0.1", "-0.05"],
                oos_mean_r_costs_x2="0.2", oos_max_drawdown_pct="15", holdout_mean_r="0.1",
                oos_by_regime={"TRENDING": ["1"] * 40, "RANGE": window(15, 20), "COMPRESSION": ["-1"] * 29})
    return G8Inputs(**(base | over))


def opened(path, hid="h1"):
    R.open_holdout(path, hid, [], R.FINAL_G8, at=2)


def status(result, key):
    return result["criteria"][key]["status"]


@pytest.fixture
def reg(tmp_path):
    path = tmp_path / "registry.jsonl"
    register(path)
    return path


# --- T16: no pre-registration, no test ------------------------------------------------

def test_t16_unregistered_test_is_refused(tmp_path):
    path = tmp_path / "registry.jsonl"
    with pytest.raises(R.NotRegistered):
        evaluate(path, "h1", full_inputs(), seed=7, at=3)
    with pytest.raises(R.NotRegistered):
        R.research_view(path, "h1", [])
    register(path, "other")
    with pytest.raises(R.NotRegistered):
        evaluate(path, "h1", full_inputs(), seed=7, at=3)
    assert records(path, "G8_RESULT") == []


def test_registration_is_unique_and_counts_every_variant(reg):
    with pytest.raises(ValueError):
        register(reg)                                           # same hypothesis twice
    register(reg, "h2")
    register(reg, "x1", family="other")
    assert R.n_variants(reg, "fam") == 2 and R.n_variants(reg, "other") == 1


# --- T18: holdout guard ------------------------------------------------------------------

def records(path, kind):
    return [r for r in chain.read(path, R._check) if r["type"] == kind]


def test_t18_holdout_access_outside_final_g8_is_refused_and_recorded(reg):
    with pytest.raises(R.HoldoutRefused):
        R.open_holdout(reg, "h1", [], "RESEARCH", at=2)
    with pytest.raises(R.HoldoutRefused):
        R.open_holdout(reg, "ghost", [], R.FINAL_G8, at=3)    # unregistered
    refused = records(reg, "HOLDOUT_REFUSED")
    assert [(r["hypothesis_id"], r["purpose"]) for r in refused] == [("h1", "RESEARCH"), ("ghost", R.FINAL_G8)]
    assert all("57" in r["reason"] for r in refused) and records(reg, "HOLDOUT_OPENED") == []


def test_holdout_opens_once_per_family(reg):
    candles = klines([row(T0 + i * DAY, step=DAY) for i in range(40)])   # 2024-01-01 … 2024-02-09
    holdout = R.open_holdout(reg, "h1", candles, R.FINAL_G8, at=2)
    assert holdout and all(k.open_time >= HOLDOUT for k in holdout) and len(holdout) == 9
    assert R.holdout_opened_by(reg, "h1")
    register(reg, "h2")
    for hid in ("h1", "h2"):                                    # again, or another candidate of the family
        with pytest.raises(R.HoldoutRefused, match="already opened"):
            R.open_holdout(reg, hid, candles, R.FINAL_G8, at=3)
    assert len(records(reg, "HOLDOUT_REFUSED")) == 2 and len(records(reg, "HOLDOUT_OPENED")) == 1


def test_research_view_never_contains_holdout_candles(reg):
    daily = klines([row(T0 + i * DAY, step=DAY) for i in range(40)])
    view = R.research_view(reg, "h1", daily)
    assert len(view) == 31 and all(k.close_time < HOLDOUT for k in view)
    weekly = klines([row(T0 + 4 * WEEK, step=WEEK)])           # 2024-01-29 … 2024-02-04 straddles the boundary
    assert R.research_view(reg, "h1", weekly) == []
    register(reg, "h9", family="w")
    assert R.open_holdout(reg, "h9", weekly, R.FINAL_G8, at=5) == []   # in neither part


def test_holdout_start_is_the_last_twelve_complete_months():
    assert R.holdout_start("2026-09") == period_start_ms("2025-10")
    assert R.holdout_start("2026-12") == period_start_ms("2026-01")


# --- T17: G8 against the approved thresholds ---------------------------------------------

def test_t17_known_series_passes_every_criterion(reg):
    opened(reg)
    result = evaluate(reg, "h1", full_inputs(), seed=7, at=3)
    assert result["G8"] == "PASS", json.dumps(result["criteria"], indent=1)
    assert result["n_variants"] == 1 and result["seed"] == 7 and result["bootstrap_resamples"] == 10_000
    assert result["criteria"]["c_profit_factor"]["value"].startswith("1.684")   # 96/57
    assert records(reg, "G8_RESULT")[0]["result"] == result                       # recorded with N and seed


def test_t17_missing_metric_is_missing(reg):
    opened(reg)
    assert evaluate(reg, "h1", G8Inputs(), seed=7, at=3)["G8"] == "MISSING"
    for field in ("is_mean_r", "neighbour_mean_r", "oos_mean_r_costs_x2", "oos_max_drawdown_pct",
                  "holdout_mean_r", "oos_by_regime"):
        assert evaluate(reg, "h1", full_inputs(**{field: None}), seed=7, at=3)["G8"] == "MISSING", field


def test_t17_multiple_testing_correction_turns_the_same_series_into_fail(reg):
    for i in range(49):
        register(reg, f"v{i}")                                  # N = 50 variants in the family
    opened(reg)
    result = evaluate(reg, "h1", full_inputs(), seed=7, at=3)
    assert result["n_variants"] == 50 and status(result, "b_mean_r_ci") == "FAIL" and result["G8"] == "FAIL"


@pytest.mark.parametrize("over,key,expected", [
    ({"oos_windows": WINDOWS[:5]}, "a_trades", "FAIL"),                                   # 5 windows < 6
    ({"oos_windows": [window(7, 8)] * 6 + [window(4, 5)]}, "a_trades", "FAIL"),            # a window with 9
    ({"oos_windows": [window(7, 8)] * 4 + [window(10, 10)] * 2}, "a_trades", "PASS"),      # exactly 100
    ({"oos_windows": [window(7, 8)] * 4 + [window(10, 10), window(10, 9)]}, "a_trades", "FAIL"),  # 99
    ({"oos_windows": [window(10, 10, "1.2", "-1")] * 6}, "c_profit_factor", "PASS"),       # PF exactly 1.2
    ({"oos_windows": [window(10, 10, "1.19", "-1")] * 6}, "c_profit_factor", "FAIL"),
    ({"oos_windows": [window(7, 8)] * 4 + [window(1, 14)] * 2}, "e_positive_windows", "PASS"),  # 4/6
    ({"oos_windows": [window(7, 8)] * 3 + [window(1, 14)] * 3}, "e_positive_windows", "FAIL"),  # 3/6
    ({"oos_windows": [window(7, 8)] * 3 + [window(10, 20)] + [window(1, 14)] * 2},
     "e_positive_windows", "FAIL"),                                                        # a flat window is not positive
    ({"neighbour_mean_r": ["0.1", "0.1", "0.1", "-0.1", "-0.1"]}, "f_parameter_robustness", "FAIL"),  # 3/5
    ({"oos_mean_r_costs_x2": "0"}, "g_costs_x2", "FAIL"),
    ({"oos_max_drawdown_pct": "15.01"}, "h_max_drawdown", "FAIL"),
    ({"holdout_mean_r": "0"}, "i_holdout", "FAIL"),
    ({"oos_by_regime": {"RANGE": window(10, 20, "1", "-1")}}, "regime", "FAIL"),           # 30 trades, mean < 0
    ({"oos_by_regime": {"RANGE": window(10, 19, "1", "-1")}}, "regime", "PASS"),           # 29 trades: not judged
    ({"oos_by_regime": {"RANGE": window(15, 15, "1", "-1")}}, "regime", "PASS"),           # mean exactly 0
])
def test_t17_each_threshold_is_applied_exactly(reg, over, key, expected):
    opened(reg)
    assert status(evaluate(reg, "h1", full_inputs(**over), seed=7, at=3), key) == expected


def test_t17_oos_vs_is_boundary(reg):
    opened(reg)
    # OOS mean is 39/105; with IS mean 78/105 the floor 0.5 × IS equals it exactly.
    is_exact = Decimal(78) / Decimal(105)
    assert status(evaluate(reg, "h1", full_inputs(is_mean_r=is_exact), seed=7, at=3), "d_oos_vs_is") == "PASS"
    assert status(evaluate(reg, "h1", full_inputs(is_mean_r=is_exact + Decimal("1e-20")), seed=7, at=3),
                  "d_oos_vs_is") == "FAIL"


def test_lower_bound_is_two_sided_at_level_1_minus_alpha_over_n():
    # k-th smallest of 10 000 means, k = max(1, floor(0.05 / N / 2 × 10 000))
    assert [lower_bound_rank(n) for n in (1, 5, 50, 1000)] == [250, 50, 5, 1]


def test_t17_holdout_result_without_a_recorded_opening_fails(reg):
    result = evaluate(reg, "h1", full_inputs(), seed=7, at=3)
    assert status(result, "i_holdout") == "FAIL" and result["G8"] == "FAIL"


def test_g8_is_reproducible_from_the_same_seed(tmp_path):
    out = []
    for name in ("a", "b"):
        path = tmp_path / name
        register(path)
        opened(path)
        out.append(evaluate(path, "h1", full_inputs(), seed=11, at=3))
    assert out[0] == out[1]


def test_registry_is_a_verified_hash_chain(reg):
    opened(reg)
    evaluate(reg, "h1", full_inputs(), seed=7, at=3)
    assert R.verify(reg) == ("VALID", [])
    lines = reg.read_text("utf-8").splitlines()
    first = json.loads(lines[0])
    first["holdout_start"] += DAY                                # move the holdout after the fact
    lines[0] = json.dumps(first)
    reg.write_text("\n".join(lines) + "\n", "utf-8")
    assert R.verify(reg)[0] == "INVALID"
    with pytest.raises(ValueError):
        register(reg, "h7")
