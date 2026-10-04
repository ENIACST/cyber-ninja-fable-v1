"""Immutable Event Ledger (§50–§52, §5), one JSON record per line.

Every record carries seq, prev_hash and record_hash; record_hash is the
sha256 of the record's canonical JSON (without record_hash), so editing,
deleting, reordering or truncating any line breaks the chain and verify()
reports INVALID. append() refuses to write to a ledger that does not verify.

Limit of any hash chain: dropping whole lines from the END leaves a valid
shorter chain. head() gives an anchor (last seq and record_hash) to keep
outside the ledger, e.g. in a run manifest or a commit; verify(path, anchor)
then also fails when the ledger no longer reaches that anchor.

Rules on append:
- event_id must equal the EVENT_ID recomputed from the payload (§51.1).
- available_from = confirmation_time + 1 and formation_time <= confirmation_time (18.3).
- An event_id already in the ledger may appear again only as a new
  observation with parent_event_id set (CN-CP-002 51.3); a parent must exist.
- No floats anywhere (51.2): non-integer numbers are decimal strings.

Single writer assumed; there is no file locking.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from .event_id import PAYLOAD_FIELDS, reject_floats, event_id_of

STATUSES = ("VALID", "INVALID", "MISSING", "STALE", "CONFLICT", "UNKNOWN")
INT_FIELDS = ("event_time", "formation_time", "confirmation_time", "available_from", "receive_time")
RECORD_FIELDS = PAYLOAD_FIELDS + INT_FIELDS + (
    "event_id", "dataset_hash", "feature_version", "validation_status", "parent_event_id", "data")
GENESIS = "0" * 64


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash(record: dict) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in record.items() if k != "record_hash"})).hexdigest()


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


def head(path: Path) -> dict | None:
    records = read(path)
    return {"seq": records[-1]["seq"], "record_hash": records[-1]["record_hash"]} if records else None


def verify(path: Path, anchor: dict | None = None) -> tuple[str, list[str]]:
    """Returns (status, problems): VALID, INVALID or MISSING (no ledger / empty)."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return ("INVALID", [f"ledger empty but anchor is seq {anchor['seq']}"]) if anchor else ("MISSING", [])
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        return "INVALID", ["last line is incomplete (truncated write)"]
    problems, prev, known, hashes = [], GENESIS, set(), []
    for i, line in enumerate(raw.decode("utf-8").splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            problems.append(f"line {i}: not JSON")
            break
        meta = {k: rec.pop(k, None) for k in ("seq", "prev_hash", "record_hash")}
        if meta["seq"] != i:
            problems.append(f"line {i}: seq {meta['seq']}")
        if meta["prev_hash"] != prev:
            problems.append(f"line {i}: prev_hash does not link to line {i - 1}")
        if meta["record_hash"] != _hash(rec | {"seq": meta["seq"], "prev_hash": meta["prev_hash"]}):
            problems.append(f"line {i}: record_hash does not match its content")
        try:
            _check_record(rec, known)
        except ValueError as e:
            problems.append(f"line {i}: {e}")
        known.add(rec.get("event_id"))
        prev = meta["record_hash"]
        hashes.append(prev)
    if anchor is not None:
        n = anchor["seq"]
        if n >= len(hashes):
            problems.append(f"ledger ends at seq {len(hashes) - 1}, anchor is seq {n} (lines removed from the end)")
        elif hashes[n] != anchor["record_hash"]:
            problems.append(f"seq {n} does not match the anchor's record_hash")
    return ("INVALID" if problems else "VALID"), problems


def read(path: Path) -> list[dict]:
    status, problems = verify(path)
    if status == "INVALID":
        raise ValueError(f"ledger INVALID: {problems}")
    if status == "MISSING":
        return []
    return [json.loads(line) for line in Path(path).read_text("utf-8").splitlines()]


def append(path: Path, record: dict) -> dict:
    path = Path(path)
    existing = read(path)  # raises on an INVALID ledger: never extend a broken chain
    _check_record(record, {r["event_id"] for r in existing})
    full = dict(record) | {"seq": len(existing), "prev_hash": existing[-1]["record_hash"] if existing else GENESIS}
    full["record_hash"] = _hash(full)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write(_canonical(full) + b"\n")
        f.flush()
        os.fsync(f.fileno())
    return full
