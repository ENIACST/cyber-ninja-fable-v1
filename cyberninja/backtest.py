"""Backtester: simulated trades from signals (Knowledge plane; EXECUTION = OFF).

Strategy-agnostic. A Signal says when a decision may be acted on (its
AVAILABLE_FROM, CN-CP-001 18.3), the side, the stop and an optional target.
Costs and limits are the approved CN-CP-003-R1 values (cyberninja.authority).
Prices are Decimal from the Binance strings; nothing is a float.

Fill model (documented assumptions, chosen to be conservative):
- Entry: market at the open of the first candle with open_time >= available_from.
- Exit: the stop or the target fills at its price when the candle's range
  touches it, or at the open when the candle opens beyond it (gap). If one
  candle touches both, the stop is assumed to come first.
- Every fill takes adverse slippage and the taker fee (42.2: taker on both sides).
- A position still open after the last candle is closed at the last close (END).
- One position at a time; a signal that cannot be taken is recorded with its
  reason (§40, §47 rejection attribution).
- Size: ENTRY-based risk (§44) = MAX_RISK_PER_TRADE of realized equity over
  the entry-to-stop distance, capped at MAX_LEVERAGE. 1R = the risk taken.
- Funding (42.2): actual funding events, paid on qty × mark price × rate. An
  event counts if the position was open at its time (after the entry fill, up
  to the exit); an event inside the exit candle, whose intrabar exit time is
  unknown, counts only if it is a cost. A trade not covered by funding data has
  funding_status MISSING, and its R is not "after funding".
- Daily loss limit: no new entry for the rest of the UTC day once realized
  equity is DAILY_LOSS_LIMIT below the day's start.
- Max drawdown: the highest equity at candle closes against the worst equity
  inside each candle (mark-to-market at the candle's adverse extreme).
- Liquidation (42.2): no margin mode or maintenance margin is approved, so the
  liquidation buffer cannot be checked; liquidation_status is UNKNOWN and G7
  cannot pass until a model is approved.
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext

from . import authority as A
from .data.binance_vision import INTERVAL_MS
from .data.fetch import months, period_end_ms, period_start_ms
from .research import registry

DAY = INTERVAL_MS["1d"]
PREC = 28
SIDES = {"LONG": 1, "SHORT": -1}
TOL = Decimal("1e-20")  # relative slack for 28-digit rounding when checking limits that are hit exactly


@dataclass(frozen=True)
class Signal:
    ref: str                   # the evidence the decision rests on, e.g. an EVENT_ID (§65)
    available_from: int        # CN-CP-001 18.3
    side: str                  # "LONG" or "SHORT"
    stop: str
    target: str | None = None


@dataclass(frozen=True)
class FundingSeries:
    events: tuple              # (funding_time ms, rate, mark_price), decimal strings
    covered_from: int          # the series is complete for [covered_from, covered_to]
    covered_to: int


def backtest(candles, signals, *, funding: FundingSeries | None = None, equity="10000",
             cost_multiplier=1, risk: dict = A.RISK) -> dict:
    with localcontext() as ctx:
        ctx.prec = PREC
        return _Run(candles, funding, Decimal(equity), Decimal(cost_multiplier), risk).run(signals)


class _Run:
    def __init__(self, candles, funding, equity, k, risk):
        if not candles:
            raise ValueError("no candles")
        if any(b.open_time != a.close_time + 1 for a, b in zip(candles, candles[1:])):
            raise ValueError("candles must be sorted and consecutive (G1 PASS before a backtest)")
        self.candles, self.funding, self.k, self.risk = candles, funding, k, risk
        self.fee = risk["FEE_TAKER_PCT"] / 100 * k
        self.slip = risk["SLIPPAGE_BPS_PER_SIDE"] / 10000 * k
        self.cash = self.start = equity
        self.pos, self.trades, self.rejected = None, [], []
        self.peak, self.max_dd = equity, Decimal(0)

    def run(self, signals) -> dict:
        pending = sorted(signals, key=lambda s: (s.available_from, s.ref))
        first = self.candles[0]
        si, day, day_start = 0, None, self.cash
        for idx, kd in enumerate(self.candles):
            if kd.open_time // DAY != day:
                day, day_start = kd.open_time // DAY, self.cash
            o, h, l, c = (Decimal(x) for x in (kd.open, kd.high, kd.low, kd.close))
            open_at_start = self.pos is not None
            troughs = []
            if self.pos:  # gap at the open of a later candle
                sgn = self.pos["sgn"]
                if (o - self.pos["stop"]) * sgn <= 0:
                    self._close(o, kd, "STOP_GAP", troughs)
                elif self.pos["target"] is not None and (o - self.pos["target"]) * sgn >= 0:
                    self._close(o, kd, "TARGET_GAP", troughs)
            while si < len(pending) and pending[si].available_from <= kd.open_time:
                s = pending[si]
                si += 1
                if s.available_from < first.open_time:
                    reason = "OUT_OF_RANGE"
                elif open_at_start or self.pos is not None:
                    reason = "POSITION_OPEN"
                elif day_start - self.cash >= self.risk["DAILY_LOSS_LIMIT_PCT"] / 100 * day_start:
                    reason = "DAILY_LOSS_LIMIT"
                else:
                    reason = self._open(s, o, kd, idx)
                if reason:
                    self.rejected.append({"ref": s.ref, "available_from": s.available_from, "reason": reason})
            if self.pos:  # intrabar, including the entry candle
                sgn = self.pos["sgn"]
                worst, best = (l, h) if sgn > 0 else (h, l)
                if (worst - self.pos["stop"]) * sgn <= 0:
                    self._close(self.pos["stop"], kd, "STOP", troughs)
                elif self.pos["target"] is not None and (best - self.pos["target"]) * sgn >= 0:
                    troughs.append(self._mtm(worst))
                    self._close(self.pos["target"], kd, "TARGET", troughs)
            if idx == len(self.candles) - 1 and self.pos:
                troughs.append(self._mtm(l if self.pos["sgn"] > 0 else h))
                self._close(c, kd, "END", troughs)
            if self.pos:
                troughs.append(self._mtm(l if self.pos["sgn"] > 0 else h))
                close_eq = self._mtm(c)
            else:
                close_eq = self.cash
            troughs.append(close_eq)
            self.max_dd = max(self.max_dd, (self.peak - min(troughs)) / self.peak)
            self.peak = max(self.peak, close_eq)
        for s in pending[si:]:
            self.rejected.append({"ref": s.ref, "available_from": s.available_from, "reason": "OUT_OF_RANGE"})
        statuses = {t["funding_status"] for t in self.trades}
        return {
            "trades": self.trades, "rejected": self.rejected,
            "equity_start": str(self.start), "equity_end": str(self.cash),
            "max_drawdown_pct": str(self.max_dd * 100),
            "costs": {"fee_taker_pct": str(self.risk["FEE_TAKER_PCT"] * self.k),
                      "slippage_bps": str(self.risk["SLIPPAGE_BPS_PER_SIDE"] * self.k),
                      "multiplier": str(self.k)},
            "funding_status": "VALID" if statuses == {"VALID"} else "MISSING",
            "liquidation_status": "UNKNOWN",
            "thresholds": registry.THRESHOLDS,
        }

    def _open(self, s, o, kd, idx):
        sgn = SIDES.get(s.side)
        if sgn is None:
            return "INVALID_SIDE"
        fill = o * (1 + sgn * self.slip)
        stop = Decimal(s.stop)
        target = None if s.target is None else Decimal(s.target)
        dist = (fill - stop) * sgn
        if (o - stop) * sgn <= 0:   # at or beyond the market at the open: it would trigger at once
            return "INVALID_STOP"
        if target is not None and (target - fill) * sgn <= 0:
            return "INVALID_TARGET"
        qty = self.cash * self.risk["MAX_RISK_PER_TRADE_PCT"] / 100 / dist
        cap = self.risk["MAX_LEVERAGE"] * self.cash / fill
        capped = qty > cap
        qty = min(qty, cap)
        self.pos = {"ref": s.ref, "sgn": sgn, "side": s.side, "fill": fill, "stop": stop, "target": target,
                    "qty": qty, "risk": qty * dist, "entry_fee": fill * qty * self.fee, "entry_time": kd.open_time,
                    "equity_before": self.cash, "leverage": qty * fill / self.cash, "capped": capped}
        return None

    def _mtm(self, px) -> Decimal:
        p = self.pos
        return self.cash - p["entry_fee"] + (px - p["fill"]) * p["qty"] * p["sgn"]

    def _funding(self, kd, how) -> tuple[Decimal, str]:
        p, f = self.pos, self.funding
        if f is None or not (f.covered_from <= p["entry_time"] and kd.close_time <= f.covered_to):
            return Decimal(0), "MISSING"
        cost = Decimal(0)
        for t, rate, mark in f.events:
            if t <= p["entry_time"] or t > kd.close_time:
                continue
            c = p["qty"] * Decimal(mark) * Decimal(rate) * p["sgn"]
            if t <= kd.open_time or how == "END":
                cost += c                       # held at that moment
            elif how in ("STOP_GAP", "TARGET_GAP"):
                continue                        # closed at this candle's open
            elif c > 0:
                cost += c                       # intrabar exit time unknown: count only a cost
        return cost, "VALID"

    def _close(self, trigger, kd, how, troughs):
        p = self.pos
        fill = trigger * (1 - p["sgn"] * self.slip)
        exit_fee = fill * p["qty"] * self.fee
        funding, f_status = self._funding(kd, how)
        net = (fill - p["fill"]) * p["qty"] * p["sgn"] - p["entry_fee"] - exit_fee - funding
        self.cash += net
        troughs.append(self.cash)
        self.trades.append({
            "ref": p["ref"], "side": p["side"], "entry_time": p["entry_time"], "entry_fill": str(p["fill"]),
            "stop": str(p["stop"]), "target": None if p["target"] is None else str(p["target"]),
            "qty": str(p["qty"]), "risk": str(p["risk"]), "equity_before": str(p["equity_before"]),
            "leverage": str(p["leverage"]), "leverage_capped": p["capped"],
            "exit_time": kd.open_time, "exit_fill": str(fill), "exit_reason": how,
            "fees": str(p["entry_fee"] + exit_fee), "funding": str(funding), "funding_status": f_status,
            "pnl": str(net), "r": str(net / p["risk"]),
        })
        self.pos = None


def trade_level_status(result: dict, risk: dict = A.RISK) -> str:
    """G7 trade-level input (§43): MISSING without trades, INVALID if a trade broke a
    limit, otherwise UNKNOWN while the liquidation buffer cannot be checked."""
    if not result["trades"]:
        return "MISSING"
    for t in result["trades"]:
        with localcontext() as ctx:
            ctx.prec = PREC
            if (Decimal(t["risk"]) > Decimal(t["equity_before"]) * risk["MAX_RISK_PER_TRADE_PCT"] / 100 * (1 + TOL)
                    or Decimal(t["leverage"]) > risk["MAX_LEVERAGE"] * (1 + TOL)):
                return "INVALID"
    return result["liquidation_status"]


def walk_forward(first_month: str, last_month: str) -> tuple[int, list[tuple[int, int]]]:
    """57.1–57.2: holdout start and the full OOS test windows before it, as [start, end) in ms."""
    ms = months(first_month, last_month)
    research = ms[:-A.HOLDOUT_MONTHS]
    windows = []
    i = A.WF_TRAIN_MONTHS
    while i + A.WF_TEST_MONTHS <= len(research):
        windows.append((period_start_ms(research[i]), period_end_ms(research[i + A.WF_TEST_MONTHS - 1])))
        i += A.WF_TEST_MONTHS
    return period_start_ms(ms[-A.HOLDOUT_MONTHS]), windows


def oos_windows(result: dict, windows) -> list | None:
    """G8 input 57.4: R per trade (by entry time) in each window; None (MISSING) unless every
    trade's R is after funding."""
    if any(t["funding_status"] != "VALID" for t in result["trades"]):
        return None
    return [[t["r"] for t in result["trades"] if start <= t["entry_time"] < end] for start, end in windows]


def mean_r(result: dict) -> str | None:
    rs = [Decimal(t["r"]) for t in result["trades"]]
    if not rs:
        return None
    with localcontext() as ctx:
        ctx.prec = PREC
        return str(sum(rs) / len(rs))


def backtest_research(registry_path, hypothesis_id: str, candles, signals, **kw) -> dict:
    """Backtest a registered hypothesis on research data only (57.1, 57.3)."""
    return backtest(registry.research_view(registry_path, hypothesis_id, candles), signals, **kw)
