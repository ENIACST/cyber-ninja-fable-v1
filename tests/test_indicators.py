from decimal import Decimal as D
from fractions import Fraction as F

import pytest

from cyberninja.data.fetch import period_end_ms
from cyberninja.eec.timeline import EventView
from cyberninja.indicators import (
    WARMUP, camarilla, ema, ema_raw, macd, macd_raw, rsi, rsi_raw)
from test_data_g0_g1 import H4, T0, klines, row

DAY = 86_400_000
PRICES = [str(60000 + (i * 7919) % 1000 - 500 + (i % 7) * 13) + ".5" for i in range(400)]  # deterministic, uneven


# --- T21: hand-computed reference series (α chosen so every value is exact) ---------

def test_ema_31_1_hand_computed():
    assert ema_raw(["1", "2", "3", "4", "5", "6"], 3) == [None, None, D(2), D(3), D(4), D(5)]
    assert ema_raw(["10", "12", "14", "13", "17"], 3) == [None, None, D(12), D("12.5"), D("14.75")]
    assert ema_raw(["1", "2"], 3) == [None, None]


def test_rsi_31_2_wilder_hand_computed():
    # changes +3 −1 +2 −1.5 0; n = 2 → α = 1/2; seed avg gain 1.5, avg loss 0.5
    assert rsi_raw(["10", "13", "12", "14", "12.5", "12.5"], 2) == [None, None, D(75), D("87.5"), D(50), D(50)]
    assert rsi_raw(["1", "2", "3"], 2)[2] == D(100)          # avg_loss = 0 → 100


def test_macd_31_3_hand_computed():
    m, s, h = macd_raw(["2", "4", "6", "10", "12", "8", "6", "10"], fast=1, slow=3, signal=3)
    assert m == [None, None, D(2), D(3), D("2.5"), D("-0.75"), D("-1.375"), D("1.3125")]
    assert s == [None, None, None, None, D("2.5"), D("0.875"), D("-0.25"), D("0.53125")]
    assert h == [None, None, None, None, D(0), D("-1.625"), D("-1.125"), D("0.78125")]


def ema_exact(xs, n):
    xs = [F(x) for x in xs]
    a, e, out = F(2, n + 1), sum(xs[:n]) / n, {}
    out[n - 1] = e
    for i in range(n, len(xs)):
        e = a * xs[i] + (1 - a) * e
        out[i] = e
    return out


def rsi_exact(cs, n):
    cs = [F(x) for x in cs]
    ch = [cs[i] - cs[i - 1] for i in range(1, len(cs))]
    g = sum(max(d, 0) for d in ch[:n]) / n
    l = sum(max(-d, 0) for d in ch[:n]) / n
    out = {n: 100 if l == 0 else 100 - F(100) / (1 + g / l)}
    for i in range(n + 1, len(cs)):
        g = F(1, n) * max(ch[i - 1], 0) + (1 - F(1, n)) * g
        l = F(1, n) * max(-ch[i - 1], 0) + (1 - F(1, n)) * l
        out[i] = 100 if l == 0 else 100 - F(100) / (1 + g / l)
    return out


@pytest.mark.parametrize("n", [8, 14, 33])
def test_decimal_results_agree_with_exact_fractions(n):
    for got, ref in ((ema_raw(PRICES, n), ema_exact(PRICES, n)), (rsi_raw(PRICES, n), rsi_exact(PRICES, n))):
        assert all(v is None for i, v in enumerate(got) if i not in ref)
        assert max(abs(F(got[i]) - ref[i]) for i in ref) < F(1, 10 ** 18)


# --- 31.4 warm-up ---------------------------------------------------------------

def test_warmup_masks_the_first_10n_bars():
    out, raw = ema(PRICES[:35], 3), ema_raw(PRICES[:35], 3)
    assert out[:30] == [WARMUP] * 30 and out[30:] == raw[30:]
    r = rsi(PRICES[:150])                                   # default n = 14 → 140 bars
    assert r[139] == WARMUP and isinstance(r[140], D)
    for series in macd(PRICES[:340]):                       # default 8/33/5 → 10 × 33 = 330 bars
        assert series[:330] == [WARMUP] * 330 and all(isinstance(v, D) for v in series[330:])
    assert ema(PRICES[:20], 3) == [WARMUP] * 20             # too short: nothing usable


def test_warmup_value_cannot_be_used_as_a_number():
    with pytest.raises(TypeError):
        ema(PRICES[:35], 3)[0] + D(1)


def test_floats_are_refused():
    with pytest.raises(TypeError):
        ema_raw([1.5, 2.5, 3.5], 2)


# --- T22: Camarilla -----------------------------------------------------------------

def test_t22_camarilla_exact_levels_and_availability():
    lv = camarilla(klines([row(T0, o="41600.00", h="42000.00", l="41000.00", c="41500.00", step=DAY)])[0])
    assert (lv["R3"], lv["R4"], lv["S3"], lv["S4"]) == (D("41775"), D("42050"), D("41225"), D("40950"))
    assert lv["available_from"] == period_end_ms("2024-01-01") == T0 + DAY   # next 00:00 UTC
    lv = camarilla(klines([row(T0, o="62500.00", h="63123.45", l="62001.10", c="62555.55", step=DAY)])[0])
    h, l, c = F("63123.45"), F("62001.10"), F("62555.55")
    exact = (c + F("1.1") * (h - l) / 4, c + F("1.1") * (h - l) / 2, c - F("1.1") * (h - l) / 4, c - F("1.1") * (h - l) / 2)
    assert tuple(F(lv[k]) for k in ("R3", "R4", "S3", "S4")) == exact             # no rounding at all
    assert tuple(lv[k] for k in ("R3", "R4", "S3", "S4")) == (
        D("62864.19625"), D("63172.8425"), D("62246.90375"), D("61938.2575"))


def test_t22_levels_are_not_visible_before_the_next_day():
    lv = camarilla(klines([row(T0, step=DAY)])[0])
    view = EventView([{"event_id": "camarilla", "available_from": lv["available_from"]}])
    assert view.at(T0 + DAY - 1) == [] and len(view.at(T0 + DAY)) == 1


def test_camarilla_refuses_a_non_d1_candle():
    with pytest.raises(ValueError):
        camarilla(klines([row(T0)])[0])                      # 4H
    with pytest.raises(ValueError):
        camarilla(klines([row(T0 + H4, step=DAY)])[0])       # not on 00:00 UTC
