"""Immutable Event Ledger (§50–§52, §5), one JSON record per line, built on
cyberninja.chain (hash chain, anchor, refusal to extend a broken chain).

Rules on append:
- event_id must equal the EVENT_ID recomputed from the payload (§51.1).
- available_from = confirmation_time + 1 and formation_time <= confirmation_time (18.3).
- An event_id already in the ledger may appear again only as a new
  observation with parent_event_id set (CN-CP-002 51.3); a parent must exist.
- No floats anywhere (51.2): non-integer numbers are decimal strings.

Single writer assumed; there is no file locking.
"""

import re
from pathlib import Path

from .. import chain
from ..chain import GENESIS, canonical as _canonical, record_hash as _hash  # noqa: F401 (used by tests)
from .event_id import PAYLOAD_FIELDS, event_id_of, reject_floats

STATUSES = ("VALID", "INVALID", "MISSING", "STALE", "CONFLICT", "UNKNOWN")
INT_FIELDS = ("event_time", "formation_time", "confirmation_time", "available_from", "receive_time")
RECORD_FIELDS = PAYLOAD_FIELDS + INT_FIELDS + (
    "event_id", "dataset_hash", "feature_version", "validation_status", "parent_event_id", "data")


def _check_record(r: dict, known_ids: set) -> None:
    if set(r) != set(RECORD_FIELDS):
        raise ValueError(f"record fields: missing {sorted(set(RECORD_FIELDS) - set(r))}, "
                         f"not allowed {sorted(set(r) - set(RECORD_FIELDS))}")
    for f in INT_FIELDS:
        if type(r[f]) is not int:
            raise ValueError(f"{f} must be an integer (ms)")
    if r["event_id"] != event_id_of(r):
        raise ValueError("event_id does not match the EVENT_ID recomputed from the payload")
    if r["formation_time"] > r["confirmation_time"]:
        raise ValueError("formation_time after confirmation_time")
    if r["available_from"] != r["confirmation_time"] + 1:
        raise ValueError("available_from must be confirmation_time + 1 (18.3)")
    if not re.fullmatch(r"ds_[0-9a-f]{64}", str(r["dataset_hash"])):
        raise ValueError("dataset_hash must be ds_ + 64 hex")
    if not isinstance(r["feature_version"], str) or not r["feature_version"]:
        raise ValueError("feature_version must be a non-empty string")
    if r["validation_status"] not in STATUSES:
        raise ValueError(f"validation_status must be one of {STATUSES}")
    if not isinstance(r["data"], dict):
        raise ValueError("data must be an object")
    reject_floats(r["data"], "data")
    parent = r["parent_event_id"]
    if parent is not None and parent not in known_ids:
        raise ValueError(f"parent_event_id {parent} is not in the ledger")
    if r["event_id"] in known_ids and parent is None:
        raise ValueError("event_id already in the ledger; a new observation needs parent_event_id (CN-CP-002 51.3)")


def _check(r: dict, state: dict) -> None:
    known = state.setdefault("event_ids", set())
    try:
        _check_record(r, known)
    finally:
        known.add(r.get("event_id"))


def verify(path: Path, anchor: dict | None = None) -> tuple[str, list[str]]:
    """Returns (status, problems): VALID, INVALID or MISSING (no ledger / empty)."""
    return chain.verify(path, _check, anchor)


def read(path: Path) -> list[dict]:
    return chain.read(path, _check)


def head(path: Path) -> dict | None:
    return chain.head(path, _check)


def append(path: Path, record: dict) -> dict:
    return chain.append(path, record, _check)
