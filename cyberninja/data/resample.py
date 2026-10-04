"""W1 candles built from complete D1 weeks (CN-CP-001 18.2: W1 opens Monday 00:00 UTC).

A week is built only when all seven D1 candles are present. Otherwise the
week is reported as incomplete (MISSING) and no candle is made for it:
nothing is filled. The input is expected to have passed G1.

Prices stay the source strings: high and low are the strings of the
extreme daily values, sums are written in plain decimal notation.
"""

from decimal import Decimal

from .binance_vision import ALIGN_OFFSET_MS, INTERVAL_MS, Kline

DAY = INTERVAL_MS["1d"]
WEEK = INTERVAL_MS["1w"]


def week_open(t: int) -> int:
    return t - (t - ALIGN_OFFSET_MS["1w"]) % WEEK


def _sum(values) -> str:
    return format(sum((Decimal(v) for v in values), Decimal(0)), "f")


def weekly_from_daily(daily: list[Kline]) -> tuple[list[Kline], list[int]]:
    """Returns (weekly candles, open_times of incomplete weeks)."""
    by_week: dict[int, list[Kline]] = {}
    for k in daily:
        by_week.setdefault(week_open(k.open_time), []).append(k)

    weeks, incomplete = [], []
    for w in sorted(by_week):
        days = sorted(by_week[w], key=lambda k: k.open_time)
        if [k.open_time for k in days] != [w + i * DAY for i in range(7)]:
            incomplete.append(w)
            continue
        weeks.append(Kline(
            open_time=w,
            open=days[0].open,
            high=max((k.high for k in days), key=Decimal),
            low=min((k.low for k in days), key=Decimal),
            close=days[-1].close,
            volume=_sum(k.volume for k in days),
            close_time=w + WEEK - 1,
            quote_volume=_sum(k.quote_volume for k in days),
            count=sum(k.count for k in days),
            taker_buy_volume=_sum(k.taker_buy_volume for k in days),
            taker_buy_quote_volume=_sum(k.taker_buy_quote_volume for k in days),
        ))
    return weeks, incomplete
