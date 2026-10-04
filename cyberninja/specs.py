"""Detector specifications and G3 FORMULA INTEGRITY (CN-CP-003 C, 26.1–26.3).

A spec is specs/<detector_id>.md: one `KEY: value` line per field (26.1), a
blank line, then free text (full pseudocode, notes). GOLDEN_VECTORS is a path
relative to the spec, to a JSON list of
{"name": ..., "input": [Binance kline rows], "expected": [detector output]}.

G3 is PASS only when every field is filled, the golden vectors exist and the
detector reproduces each expected output exactly. An empty field, absent
vectors or no detector to run is MISSING (26.2, 26.3); a mismatch is FAIL.
"""

import json
from pathlib import Path

from .data.binance_vision import parse_rest
from .gates import gate_result

FIELDS = (
    "DETECTOR_ID", "VERSION", "ORIGIN", "CANDLE_BASIS", "TIMEFRAMES", "INPUTS", "PARAMS",
    "DEFINITION", "TIMING", "MITIGATION_INVALIDATION", "OUTPUT_FIELDS", "GOLDEN_VECTORS", "LIMITATIONS",
)
ORIGINS = ("v5.1", "research")
CANDLE_BASES = ("RAW", "HA")  # CN-CP-002 32.1


def parse(path: Path) -> dict:
    header = {}
    for line in Path(path).read_text("utf-8").splitlines():
        if not line.strip():
            break
        key, sep, value = line.partition(":")
        if sep:
            header[key.strip()] = value.strip()
    return header


def g3(spec_path: Path, detector=None) -> tuple[str, list[str]]:
    path = Path(spec_path)
    if not path.exists():
        return "MISSING", [f"{path} not found"]
    spec = parse(path)
    statuses, problems = [], []

    def finding(status, text):
        statuses.append(status)
        problems.append(text)

    for f in FIELDS:
        if not spec.get(f):
            finding("MISSING", f"{f} is empty")
    if spec.get("ORIGIN") and spec["ORIGIN"] not in ORIGINS:
        finding("INVALID", f"ORIGIN must be one of {ORIGINS}")
    if spec.get("CANDLE_BASIS") and spec["CANDLE_BASIS"] not in CANDLE_BASES:
        finding("INVALID", f"CANDLE_BASIS must be one of {CANDLE_BASES}")

    vectors = []
    if spec.get("GOLDEN_VECTORS"):
        vpath = path.parent / spec["GOLDEN_VECTORS"]
        try:
            vectors = json.loads(vpath.read_text("utf-8"))
        except FileNotFoundError:
            finding("MISSING", f"golden vectors {vpath} not found")
        except ValueError:
            finding("INVALID", f"golden vectors {vpath} are not JSON")
        else:
            if not isinstance(vectors, list) or not vectors:
                finding("MISSING", "no golden vectors")
                vectors = []

    if detector is None:
        finding("MISSING", "no detector implementation to check against the golden vectors")
    for v in vectors:
        try:
            got = detector(parse_rest(v["input"])) if detector else None
            expected, name = v["expected"], v["name"]
        except (KeyError, TypeError, ValueError, IndexError) as e:
            finding("INVALID", f"malformed golden vector: {e!r}")
            continue
        if detector and got != expected:
            finding("INVALID", f"golden vector {name!r}: expected {expected!r}, got {got!r}")
    return gate_result(statuses or ["VALID"]), problems
