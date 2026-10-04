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
