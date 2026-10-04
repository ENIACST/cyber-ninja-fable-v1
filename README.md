# cyber-ninja-fable-v1

Doctrine: `docs/CYBER_NINJA_v5.2_HYBRID.md`. Working rules: `CLAUDE.md`.

## G0/G1 market data (Binance USD-M futures klines)

Standard library only; tests need `pytest`.

```
python -m cyberninja.data.fetch --symbol BTCUSDT --interval 4h --start 2024-01 --end 2024-06
python -m pytest -q
```

Writes the verified zips, `*.manifest.json` (G0 fingerprint, DATASET_HASH) and
`*.integrity.json` (G1 report) to `data/binance_um/` (git-ignored). Exit code 0
only when G0=PASS and G1=VALID. Gaps are reported as MISSING and never filled.
