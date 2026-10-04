"""Indicators per CN-CP-003 D (approved 2026-10-04): 31.1–31.4 and 30.1.

Decimal arithmetic in a fixed local context (28 significant digits), so values
do not depend on the caller's context or on float printing. Inputs are price
strings (as Binance publishes them), ints or Decimals; floats are refused.

The *_raw functions return None where an indicator is not yet defined. The
public functions replace every value inside the warm-up (31.4: before bar
10 × n, read conservatively as the first 10 × n bars) with the string WARMUP,
so it can never be used as a number by accident. For MACD the warm-up is
10 × the largest of its lengths. If v5.1 defines an indicator differently,
v5.1 wins through its own Governance Event.
"""

from decimal import Decimal, localcontext

from .authority import WARMUP_BARS_PER_LENGTH
from .data.binance_vision import INTERVAL_MS

WARMUP = "WARMUP"
PREC = 28
DAY = INTERVAL_MS["1d"]


def _num(x) -> Decimal:
    if isinstance(x, (float, bool)) or not isinstance(x, (str, int, Decimal)):
        raise TypeError(f"expected a decimal string, int or Decimal, got {x!r}")
    return Decimal(x)


def ema_raw(xs, n: int) -> list:
    """31.1: α = 2 / (n + 1); seed = SMA of the first n values."""
    if n < 1:
        raise ValueError("n must be >= 1")
    with localcontext() as ctx:
        ctx.prec = PREC
        xs = [_num(x) for x in xs]
        out = [None] * len(xs)
        if len(xs) < n:
            return out
        alpha = Decimal(2) / (n + 1)
        e = sum(xs[:n]) / n
        out[n - 1] = e
        for i in range(n, len(xs)):
            e = alpha * xs[i] + (1 - alpha) * e
            out[i] = e
        return out


def _rsi(gain: Decimal, loss: Decimal) -> Decimal:
    return Decimal(100) if loss == 0 else 100 - 100 / (1 + gain / loss)


def rsi_raw(closes, n: int = 14) -> list:
    """31.2 (Wilder): average gain and loss with RMA, α = 1 / n, seeded with the
    SMA of the first n changes; avg_loss = 0 → RSI = 100."""
    if n < 1:
        raise ValueError("n must be >= 1")
    with localcontext() as ctx:
        ctx.prec = PREC
        c = [_num(x) for x in closes]
        out = [None] * len(c)
        if len(c) < n + 1:
            return out
        changes = [c[i] - c[i - 1] for i in range(1, len(c))]
        gains = [d if d > 0 else Decimal(0) for d in changes]
        losses = [-d if d < 0 else Decimal(0) for d in changes]
        g, l = sum(gains[:n]) / n, sum(losses[:n]) / n
        out[n] = _rsi(g, l)
        alpha = Decimal(1) / n
        for i in range(n + 1, len(c)):
            g = alpha * gains[i - 1] + (1 - alpha) * g
            l = alpha * losses[i - 1] + (1 - alpha) * l
            out[i] = _rsi(g, l)
        return out


def macd_raw(closes, fast: int = 8, slow: int = 33, signal: int = 5) -> tuple[list, list, list]:
    """31.3: MACD = EMA_fast − EMA_slow; SIGNAL = EMA_signal(MACD); HIST = MACD − SIGNAL."""
    with localcontext() as ctx:
        ctx.prec = PREC
        f, s = ema_raw(closes, fast), ema_raw(closes, slow)
        macd = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
        sig = [None] * len(macd)
        start = next((i for i, v in enumerate(macd) if v is not None), None)
        if start is not None:
            sig[start:] = ema_raw(macd[start:], signal)
        hist = [a - b if a is not None and b is not None else None for a, b in zip(macd, sig)]
        return macd, sig, hist


def mask(values: list, warmup: int) -> list:
    return [WARMUP if i < warmup else v for i, v in enumerate(values)]


def ema(xs, n: int) -> list:
    return mask(ema_raw(xs, n), WARMUP_BARS_PER_LENGTH * n)


def rsi(closes, n: int = 14) -> list:
    return mask(rsi_raw(closes, n), WARMUP_BARS_PER_LENGTH * n)


def macd(closes, fast: int = 8, slow: int = 33, signal: int = 5) -> tuple[list, list, list]:
    w = WARMUP_BARS_PER_LENGTH * max(fast, slow, signal)
    return tuple(mask(series, w) for series in macd_raw(closes, fast, slow, signal))


def camarilla(prev_d1) -> dict:
    """30.1: R3/R4/S3/S4 from the previous CLOSED D1 candle (raw OHLC, LAST price,
    00:00 UTC boundary), usable from the open of the next D1 candle."""
    if prev_d1.open_time % DAY or prev_d1.close_time != prev_d1.open_time + DAY - 1:
        raise ValueError("not a D1 candle on the 00:00 UTC boundary")
    with localcontext() as ctx:
        ctx.prec = PREC
        h, l, c = _num(prev_d1.high), _num(prev_d1.low), _num(prev_d1.close)
        r = Decimal("1.1") * (h - l)
        return {
            "R3": c + r / 4, "R4": c + r / 2, "S3": c - r / 4, "S4": c - r / 2,
            "available_from": prev_d1.close_time + 1,  # CN-CP-001 18.3
        }
