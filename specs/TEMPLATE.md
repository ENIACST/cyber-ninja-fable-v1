DETECTOR_ID:
VERSION:
ORIGIN:
CANDLE_BASIS:
TIMEFRAMES:
INPUTS:
PARAMS:
DEFINITION:
TIMING:
MITIGATION_INVALIDATION:
OUTPUT_FIELDS:
GOLDEN_VECTORS:
LIMITATIONS:

Copy to specs/<detector_id>.md and fill every field above (CN-CP-003 C, 26.1).
G3 stays MISSING while any field is empty (cyberninja/specs.py).

- ORIGIN: `v5.1` (definition copied from the v5.1 original, never written by AI,
  26.2) or `research`.
- CANDLE_BASIS: `RAW` or `HA` (CN-CP-002 32.1). It also goes into the EVENT_ID
  params as "candle_basis".
- PARAMS: concrete values, e.g. `{"left": 2, "right": 2}`.
- DEFINITION: exact, as pseudocode. If it does not fit on one line, write
  `below` and put the pseudocode after this header.
- TIMING: FORMATION_TIME / CONFIRMATION_TIME rule (CN-CP-001 18.3).
- GOLDEN_VECTORS: path relative to this file, e.g. `golden/<detector_id>.json`,
  a JSON list of {"name", "input": [Binance kline rows], "expected": [output]}.
  For a v5.1 detector, "expected" comes from the output of the original (26.3).
