"""EVENT_ID per CN-CP-001 §51.1–51.2.

EVENT_ID = "ev_" + hex(SHA-256(CANONICAL_PAYLOAD)), where the payload is the
eleven fields below as UTF-8 JSON with sorted keys and no whitespace. Receive
times, statuses and DATASET_HASH are not part of it: the ID names the event,
not an observation of it.
"""

import hashlib
import json

PAYLOAD_FIELDS = (
    "event_type", "detector_id", "detector_version", "params", "source", "symbol",
    "contract_type", "price_type", "timeframe", "source_open_times", "direction",
)
STRING_FIELDS = ("event_type", "detector_id", "detector_version", "source", "symbol", "contract_type", "timeframe")
PRICE_TYPES = ("LAST", "MARK")
DIRECTIONS = ("BULL", "BEAR", None)


def reject_floats(x, where):
    # 51.2: numbers that are not integers travel as decimal strings, so the
    # hash never depends on how a language prints a float.
    if isinstance(x, float):
        raise ValueError(f"{where}: float {x!r}; use a decimal string")
    if isinstance(x, dict):
        for k, v in x.items():
            reject_floats(v, f"{where}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            reject_floats(v, f"{where}[{i}]")


def canonical_payload(payload: dict) -> bytes:
    if set(payload) != set(PAYLOAD_FIELDS):
        missing, extra = set(PAYLOAD_FIELDS) - set(payload), set(payload) - set(PAYLOAD_FIELDS)
        raise ValueError(f"payload fields: missing {sorted(missing)}, not allowed {sorted(extra)}")
    for f in STRING_FIELDS:
        if not isinstance(payload[f], str) or not payload[f]:
            raise ValueError(f"{f} must be a non-empty string")
    if payload["price_type"] not in PRICE_TYPES:
        raise ValueError(f"price_type must be one of {PRICE_TYPES}")
    if payload["direction"] not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}")
    if not isinstance(payload["params"], dict):
        raise ValueError("params must be an object")
    reject_floats(payload["params"], "params")
    times = payload["source_open_times"]
    if (not isinstance(times, list) or not times
            or any(type(t) is not int for t in times)
            or any(a >= b for a, b in zip(times, times[1:]))):
        raise ValueError("source_open_times must be a non-empty, strictly ascending list of integers")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def event_id(payload: dict) -> str:
    return "ev_" + hashlib.sha256(canonical_payload(payload)).hexdigest()


def event_id_of(record: dict) -> str:
    """EVENT_ID of a full ledger record: only the payload fields count."""
    return event_id({k: record[k] for k in PAYLOAD_FIELDS})
