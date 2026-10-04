import pytest

from cyberninja.data import binance_vision as bv
from cyberninja.data.fetch import days, period_end_ms, periods, run
from cyberninja.data.integrity import check
from cyberninja.data.resample import week_open, weekly_from_daily
from test_data_g0_g1 import T0, fake_fetcher, klines, make_zip, row

DAY = bv.INTERVAL_MS["1d"]
WEEK = bv.INTERVAL_MS["1w"]
H4 = bv.INTERVAL_MS["4h"]


# --- daily archives ------------------------------------------------------------

def test_daily_archive_url_and_periods():
    assert bv.archive_url("BTCUSDT", "4h", "2024-01-15") == (
        "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/4h/BTCUSDT-4h-2024-01-15.zip")
    assert days("2024-02-28", "2024-03-01") == ["2024-02-28", "2024-02-29", "2024-03-01"]  # leap year
    assert periods("2024-01", "2024-02") == ["2024-01", "2024-02"]
    assert periods("2024-12-31", "2025-01-01") == ["2024-12-31", "2025-01-01"]
    with pytest.raises(ValueError):
        periods("2024-01", "2024-01-31")
    with pytest.raises(ValueError):
        bv.period_kind("2024-1")


def test_daily_period_end():
    assert period_end_ms("2024-01-01") == T0 + DAY
    assert period_end_ms("2024-12-31") == 1735689600000


def test_run_on_daily_archives(tmp_path):
    archives = {f"BTCUSDT-4h-2024-01-0{d}.zip": make_zip([row(T0 + (d - 1) * DAY + i * H4) for i in range(6)])
                for d in (1, 2)}
    manifest, rep = run("BTCUSDT", "4h", "2024-01-01", "2024-01-02", tmp_path,
                        fetch=fake_fetcher(archives), now_ms=0)
    assert manifest["G0"] == "PASS" and manifest["G1"] == "PASS" and rep["candles"] == 12
    assert manifest["fingerprint"]["ENDPOINT"] == "/data/futures/um/daily/klines/BTCUSDT/4h/"
    # 18.4 per daily archive: the last 4H candle of a day closes before midnight
    assert rep["beyond_period"] == []


# --- W1 ------------------------------------------------------------------------

def test_w1_alignment_is_monday_utc():
    # 2024-01-01 was a Monday; 2024-01-04 a Thursday (an epoch multiple of 7 days)
    thursday = T0 + 3 * DAY
    assert thursday % WEEK == 0
    assert check(klines([row(T0, step=WEEK)]), "1w").misaligned == []
    assert check(klines([row(thursday, step=WEEK)]), "1w").misaligned == [thursday]
    assert week_open(T0 + 6 * DAY + 5) == T0 and week_open(T0 + 7 * DAY) == T0 + WEEK


def daily(n, start=T0):
    return [row(start + i * DAY, o=str(100 + i), h=str(110 + i), l=str(90 + i), c=str(101 + i),
                v="1.5", tb="0.5", step=DAY, count=10) for i in range(n)]


def test_weekly_from_complete_daily_weeks():
    weeks, incomplete = weekly_from_daily(klines(daily(14)))
    assert incomplete == [] and [w.open_time for w in weeks] == [T0, T0 + WEEK]
    w = weeks[0]
    assert (w.open, w.high, w.low, w.close) == ("100", "116", "90", "107")
    assert (w.volume, w.taker_buy_volume, w.count, w.close_time) == ("10.5", "3.5", 70, T0 + WEEK - 1)
    assert check(weeks, "1w").status == "VALID"


def test_incomplete_week_is_reported_and_never_built():
    rows = daily(14)
    del rows[9]                                   # a day missing in the second week
    weeks, incomplete = weekly_from_daily(klines(rows + daily(3, T0 + 2 * WEEK)))  # partial third week
    assert [w.open_time for w in weeks] == [T0]
    assert incomplete == [T0 + WEEK, T0 + 2 * WEEK]


def test_extremes_compare_by_value_and_keep_source_strings():
    # As text, "160" > "1000.0" and "120" < "99.50"; by value it is the other way round.
    highs = ["151", "152", "160", "153", "154", "1000.0", "156"]
    lows = ["149", "120", "99.50", "130", "140", "145", "148"]
    rows = [row(T0 + i * DAY, o="150", h=h, l=l, c="150", step=DAY) for i, (h, l) in enumerate(zip(highs, lows))]
    w = weekly_from_daily(klines(rows))[0][0]
    assert (w.high, w.low) == ("1000.0", "99.50")
