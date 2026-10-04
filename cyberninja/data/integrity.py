"""G1 — Data Integrity checks on klines (doctrine §15).

Every check reports what it found; nothing is repaired, filled or dropped.
A missing candle is MISSING, never zero and never interpolated.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .binance_vision import INTERVAL_MS, Kline


@dataclass
class Report:
    interval: str
    candles: int = 0
    first_open_time: int | None = None
    last_open_time: int | None = None
    duplicates: list[int] = field(default_factory=list)       # open_times seen more than once
    gaps: list[tuple[int, int]] = field(default_factory=list)  # (first missing open_time, count)
    misaligned: list[int] = field(default_factory=list)       # open_time not on a UTC interval boundary
    bad_close_time: list[int] = field(default_factory=list)
    invalid_ohlc: list[tuple[int, str]] = field(default_factory=list)
    invalid_volume: list[tuple[int, str]] = field(default_factory=list)
    zero_volume: list[int] = field(default_factory=list)      # reported, not invalidating
    stale: bool = False

    @property
    def missing_candles(self) -> int:
        return sum(n for _, n in self.gaps)

    @property
    def status(self) -> str:
        if self.candles == 0:
            return "MISSING"
        if self.duplicates or self.misaligned or self.bad_close_time or self.invalid_ohlc or self.invalid_volume:
            return "INVALID"
        if self.gaps:
            return "MISSING"
        if self.stale:
            return "STALE"
        return "VALID"


def _dec(s: str) -> Decimal:
    d = Decimal(s)
    if not d.is_finite():
        raise InvalidOperation(s)
    return d


def check(klines: list[Kline], interval: str, as_of_ms: int | None = None) -> Report:
    step = INTERVAL_MS[interval]
    rep = Report(interval=interval, candles=len(klines))
    if not klines:
        return rep

    ordered = sorted(klines, key=lambda k: k.open_time)
    rep.first_open_time = ordered[0].open_time
    rep.last_open_time = ordered[-1].open_time

    prev = None
    for k in ordered:
        t = k.open_time
        if prev is not None:
            if t == prev:
                rep.duplicates.append(t)
            elif t - prev > step:
                rep.gaps.append((prev + step, (t - prev) // step - 1))
        prev = t

        # Epoch-ms timestamps in UTC; a shifted timezone or a seconds/microseconds
        # unit shows up here as misalignment.
        if t % step:
            rep.misaligned.append(t)
        if k.close_time != t + step - 1:
            rep.bad_close_time.append(t)

        try:
            o, h, l, c = (_dec(x) for x in (k.open, k.high, k.low, k.close))
            if not (l > 0 and l <= min(o, c) and h >= max(o, c)):
                rep.invalid_ohlc.append((t, f"O={k.open} H={k.high} L={k.low} C={k.close}"))
        except InvalidOperation:
            rep.invalid_ohlc.append((t, "non-numeric price"))

        try:
            v, tb = _dec(k.volume), _dec(k.taker_buy_volume)
            if v < 0 or tb < 0 or tb > v or k.count < 0:
                rep.invalid_volume.append((t, f"volume={k.volume} taker_buy={k.taker_buy_volume} count={k.count}"))
            elif v == 0:
                rep.zero_volume.append(t)
        except InvalidOperation:
            rep.invalid_volume.append((t, "non-numeric volume"))

    if as_of_ms is not None:
        # The newest candle that could be closed at as_of is the one opening at
        # floor(as_of/step)*step - step; anything older means the feed stopped.
        rep.stale = rep.last_open_time < (as_of_ms // step) * step - step
    return rep


def compare_sources(a: list[Kline], b: list[Kline]) -> list[tuple[int, str]]:
    """§10 secondary validation: CONFLICT rows where two sources disagree on an overlapping candle."""
    fields = ("open", "high", "low", "close", "volume", "close_time")
    bmap = {k.open_time: k for k in b}
    conflicts = []
    for k in a:
        other = bmap.get(k.open_time)
        if other is None:
            continue
        diff = [f for f in fields if _norm(getattr(k, f)) != _norm(getattr(other, f))]
        if diff:
            conflicts.append((k.open_time, ",".join(diff)))
    return conflicts


def _norm(x):
    # "42000.10" and "42000.1" are the same number; compare values, not spelling.
    return _dec(x) if isinstance(x, str) else x
