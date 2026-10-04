# cyber-ninja-fable-v1

Doctrine: `docs/CYBER_NINJA_v5.2_HYBRID.md`. Working rules: `CLAUDE.md`.

## G0/G1 market data (Binance USD-M futures klines)

Standard library only; tests need `pytest`.

```
python -m cyberninja.data.fetch --symbol BTCUSDT --interval 4h --start 2024-01 --end 2024-06
python -m cyberninja.data.fetch --interval 4h --start 2024-07-01 --end 2024-07-15   # daily archives
python -m pytest -q
```

YYYY-MM uses monthly archives, YYYY-MM-DD daily ones (for the current, not yet
archived month). W1 is not downloaded: `cyberninja.data.resample.weekly_from_daily`
builds it from complete D1 weeks (Monday 00:00 UTC) and reports incomplete weeks.

Writes to `data/binance_um/` (git-ignored) and never overwrites anything:
`archives/<archive>/<sha256>.zip` holds each verified published version, and
`runs/<dataset>/<run_id>.manifest.json` / `.integrity.json` hold the G0
fingerprint, DATASET_HASH, DATA_VERSION and the G1 report of each run.
Re-check a recorded run offline from the stored archives (§9):

```
python -m cyberninja.data.replay data/binance_um/runs/<dataset>/<run_id>.manifest.json
```

It recomputes DATASET_HASH, DATA_VERSION, the integrity report and G1_INTEGRITY and
writes `<now>.replay.json` with REPRODUCED=PASS|FAIL next to the manifest.

Gate results follow CN-CP-002 (PASS / FAIL / MISSING / UNKNOWN). G1 includes the
§10 secondary validation: the same range is fetched from `/fapi/v1/klines` and
compared candle by candle (closed candles only, per SERVER_TIME). Without
network G1 is therefore MISSING, never PASS; `G1_INTEGRITY` is the archive-only
part that replay recomputes. Exit code 0 only when G0=PASS and G1=PASS. Gaps
are reported as MISSING and never filled.

## EEC — Event Evidence (CN-CP-001)

- `cyberninja/eec/event_id.py` — EVENT_ID per §51.1 (canonical payload, `ev_` + sha256).
- `cyberninja/eec/timeline.py` — closed-bar law (18.1), AVAILABLE_FROM and pivot
  confirmation (18.3), HTF visibility, `EventView` that never returns an event
  before AVAILABLE_FROM, and G2 from the reads actually made.
- `cyberninja/eec/ledger.py` — append-only, hash-chained JSONL ledger (§50–§52).
  `verify(path, anchor)` detects edits, deletions, reordering and truncation;
  keep `head(path)` outside the ledger, because removing whole lines from the
  end is only detectable against that anchor.

No detector writes to the ledger yet: FVG waits for the original detector (§82).

## Research, gates and indicators (CN-CP-003)

- `cyberninja/authority.py` — approved CN-CP-003-R1 values; `tests/test_authority.py`
  fails if any value drifts from the latest approved value in the doctrine. G7 is
  MISSING for an empty risk field and cannot PASS until trade-level risk
  validation (§43) exists.
- `cyberninja/research/registry.py` — research ledger (hash-chained, `cyberninja/chain.py`):
  pre-registration before any test (57.3, every variant counts in N), holdout
  fixed at registration and opened only for the final G8, once per family;
  refused requests are recorded (57.1). An API guard, not a sandbox.
- `cyberninja/research/g8.py` — G8 against the approved thresholds (57.4–57.5),
  bootstrap lower bound at level 1 − 0.05/N with a recorded seed; a metric not
  given is MISSING. The backtester that will produce its inputs does not exist yet.
- `cyberninja/indicators.py` — EMA, Wilder RSI, MACD 8/33/5 in Decimal; warm-up
  values are the string `WARMUP`. Camarilla R3/R4/S3/S4 from the previous D1.
- `cyberninja/specs.py`, `specs/TEMPLATE.md` — detector spec format and G3:
  PASS only when the detector reproduces every golden vector.

## Backtester (EXECUTION = OFF)

`cyberninja/backtest.py` simulates trades from signals (`Signal`: evidence ref,
AVAILABLE_FROM, side, stop, optional target) with the approved R1 costs. Entry
at the first open at or after AVAILABLE_FROM; stop/target at their price or at
the open on a gap, stop first when a candle touches both; taker fee and adverse
slippage on every fill; actual funding events (`FundingSeries`); ENTRY-based
1 % risk capped at 3x; daily loss limit; drawdown against intrabar troughs.
Rejected signals keep their reason.

- `backtest_research(registry, hypothesis, candles, signals)` runs a registered
  hypothesis on research data only (the holdout is never passed in).
- `walk_forward`, `oos_windows`, `mean_r` build the G8 inputs; trades without
  funding data make the OOS input MISSING.
- `trade_level_status` is G7's trade-level input. It is UNKNOWN until a margin
  model and maintenance margin are approved: the liquidation buffer (42.2)
  cannot be checked without them.
