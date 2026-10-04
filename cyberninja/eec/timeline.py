"""Closed-bar law and event time semantics (CN-CP-001 18.1, 18.3).

Events here are ledger records (dicts) carrying "available_from".
"""


def is_closed(close_time: int, server_time: int) -> bool:
    """18.1: a candle is closed when SERVER_TIME >= close_time + 1 ms."""
    return server_time >= close_time + 1


def available_from(confirmation_time: int) -> int:
    """18.3: the first moment the system may use an event."""
    return confirmation_time + 1


def pivot_confirmation_time(candles, pivot_index: int, right_bars: int) -> int | None:
    """18.3: close_time of the R-th candle after the pivot; None until that candle exists."""
    i = pivot_index + right_bars
    return candles[i].close_time if i < len(candles) else None


def htf_visible(htf_candles, ltf_open_time: int) -> list:
    """18.1: a higher-timeframe candle is visible from the first lower-timeframe
    candle whose open_time >= its close_time."""
    return [k for k in htf_candles if ltf_open_time >= k.close_time]


class EventView:
    """Read access to events as of a decision time; never returns an event before AVAILABLE_FROM."""

    def __init__(self, events):
        self._events = sorted(events, key=lambda e: e["available_from"])

    def at(self, decision_time: int) -> list:
        return [e for e in self._events if decision_time >= e["available_from"]]


def lookahead_violations(reads) -> list:
    """reads: (decision_time, event) pairs that were actually used."""
    return [(t, e["event_id"]) for t, e in reads if t < e["available_from"]]


def g2(reads) -> str:
    """18.3: any read before AVAILABLE_FROM is G2 FAIL. No reads means nothing was checked."""
    reads = list(reads)
    if not reads:
        return "MISSING"
    return "FAIL" if lookahead_violations(reads) else "PASS"
