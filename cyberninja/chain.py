"""Append-only, hash-chained JSON-lines files (§5). The Event Ledger and the
research registry are both built on this.

Every line carries seq, prev_hash and record_hash; record_hash is the sha256
of the line's canonical JSON without record_hash. Editing, deleting,
reordering or truncating any line breaks the chain and verify() reports
INVALID; append() refuses to extend a chain that does not verify.

Limit of any hash chain: dropping whole lines from the END leaves a valid
shorter chain. head() gives an anchor (last seq and record_hash) to keep
outside the file, e.g. in a run manifest or a commit; verify(path, check,
anchor) then also fails when the file no longer reaches that anchor.

check(record, state) validates one record (without the three chain fields)
against the records before it and raises ValueError; it keeps whatever it
needs in the dict `state`. Single writer assumed; there is no file locking.
"""

import hashlib
import json
import os
from pathlib import Path

GENESIS = "0" * 64
META = ("seq", "prev_hash", "record_hash")


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_hash(record: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in record.items() if k != "record_hash"})).hexdigest()


def scan(path: Path, check, anchor: dict | None = None):
    """Returns (status, problems, records, state); status is VALID, INVALID or MISSING."""
    path, state = Path(path), {}
    if not path.exists() or path.stat().st_size == 0:
        if anchor:
            return "INVALID", [f"ledger empty but anchor is seq {anchor['seq']}"], [], state
        return "MISSING", [], [], state
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        return "INVALID", ["last line is incomplete (truncated write)"], [], state
    problems, prev, records = [], GENESIS, []
    for i, line in enumerate(raw.decode("utf-8").splitlines()):
        try:
            full = json.loads(line)
        except ValueError:
            problems.append(f"line {i}: not JSON")
            break
        if not isinstance(full, dict):
            problems.append(f"line {i}: not a JSON object")
            break
        rec = {k: v for k, v in full.items() if k not in META}
        seq, prev_hash, rh = (full.get(k) for k in META)
        if seq != i:
            problems.append(f"line {i}: seq {seq}")
        if prev_hash != prev:
            problems.append(f"line {i}: prev_hash does not link to line {i - 1}")
        if rh != record_hash(rec | {"seq": seq, "prev_hash": prev_hash}):
            problems.append(f"line {i}: record_hash does not match its content")
        try:
            check(rec, state)
        except ValueError as e:
            problems.append(f"line {i}: {e}")
        prev = rh
        records.append(full)
    if anchor is not None:
        n = anchor["seq"]
        if n >= len(records):
            problems.append(f"ledger ends at seq {len(records) - 1}, anchor is seq {n} (lines removed from the end)")
        elif records[n].get("record_hash") != anchor["record_hash"]:
            problems.append(f"seq {n} does not match the anchor's record_hash")
    return ("INVALID" if problems else "VALID"), problems, records, state


def verify(path: Path, check, anchor: dict | None = None) -> tuple[str, list[str]]:
    status, problems, _, _ = scan(path, check, anchor)
    return status, problems


def read(path: Path, check) -> list[dict]:
    status, problems, records, _ = scan(path, check)
    if status == "INVALID":
        raise ValueError(f"ledger INVALID: {problems}")
    return records


def head(path: Path, check) -> dict | None:
    records = read(path, check)
    return {"seq": records[-1]["seq"], "record_hash": records[-1]["record_hash"]} if records else None


def append(path: Path, record: dict, check) -> dict:
    path = Path(path)
    status, problems, records, state = scan(path, check)
    if status == "INVALID":
        raise ValueError(f"ledger INVALID: {problems}")  # never extend a broken chain
    check(record, state)
    full = dict(record) | {"seq": len(records), "prev_hash": records[-1]["record_hash"] if records else GENESIS}
    full["record_hash"] = record_hash(full)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write(canonical(full) + b"\n")
        f.flush()
        os.fsync(f.fileno())
    return full
