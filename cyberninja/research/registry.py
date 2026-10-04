"""Research registry (CN-CP-003 57.1, 57.3): pre-registrations, holdout guard
and G8 results, in an append-only hash chain (cyberninja.chain).

- 57.3: nothing is evaluated without a REGISTRATION written before the test;
  every registration in a family counts in N, including variants that fail.
- 57.1: the holdout (the last HOLDOUT_MONTHS months, fixed at registration)
  is opened only for the final G8 and only once per family; every other
  request is refused AND recorded as HOLDOUT_REFUSED.

This is an API guard with an audit trail, not a sandbox: code that reads the
archives directly bypasses it.
"""

import re
from pathlib import Path

from .. import chain
from ..authority import HOLDOUT_MONTHS
from ..data.fetch import period_start_ms
from ..eec.event_id import reject_floats

FINAL_G8 = "FINAL_G8"
THRESHOLDS = "CN-CP-003-R1"
FIELDS = {
    "REGISTRATION": {"type", "hypothesis_id", "family", "params", "dataset_hash", "holdout_start", "thresholds", "at"},
    "HOLDOUT_OPENED": {"type", "hypothesis_id", "purpose", "at"},
    "HOLDOUT_REFUSED": {"type", "hypothesis_id", "purpose", "reason", "at"},
    "G8_RESULT": {"type", "hypothesis_id", "result", "at"},
}


class NotRegistered(Exception):
    pass


class HoldoutRefused(Exception):
    pass


def _check(r: dict, state: dict) -> None:
    kind = r.get("type")
    if kind not in FIELDS:
        raise ValueError(f"type must be one of {sorted(FIELDS)}")
    if set(r) != FIELDS[kind]:
        raise ValueError(f"{kind} fields: missing {sorted(FIELDS[kind] - set(r))}, "
                         f"not allowed {sorted(set(r) - FIELDS[kind])}")
    if type(r["at"]) is not int:
        raise ValueError("at must be an integer (ms)")
    if not isinstance(r["hypothesis_id"], str) or not r["hypothesis_id"]:
        raise ValueError("hypothesis_id must be a non-empty string")
    reject_floats(r, "record")
    registered = state.setdefault("registered", {})   # hypothesis_id -> REGISTRATION
    opened = state.setdefault("opened", {})           # family -> hypothesis_id that opened it
    hid = r["hypothesis_id"]
    if kind == "REGISTRATION":
        if hid in registered:
            raise ValueError(f"{hid} is already registered")
        if not isinstance(r["family"], str) or not r["family"]:
            raise ValueError("family must be a non-empty string")
        if not isinstance(r["params"], dict):
            raise ValueError("params must be an object")
        if not re.fullmatch(r"ds_[0-9a-f]{64}", str(r["dataset_hash"])):
            raise ValueError("dataset_hash must be ds_ + 64 hex")
        if type(r["holdout_start"]) is not int:
            raise ValueError("holdout_start must be an integer (ms)")
        registered[hid] = r
    elif kind == "HOLDOUT_OPENED":
        if hid not in registered:
            raise ValueError(f"{hid} is not registered")
        if r["purpose"] != FINAL_G8:
            raise ValueError("the holdout is opened only for the final G8")
        family = registered[hid]["family"]
        if family in opened:
            raise ValueError(f"holdout already opened for family {family}")
        opened[family] = hid
    elif kind == "G8_RESULT" and hid not in registered:
        raise ValueError(f"{hid} is not registered")
    # HOLDOUT_REFUSED is accepted for any hypothesis_id: unregistered attempts are recorded too.


def _state(path: Path) -> dict:
    status, problems, _, state = chain.scan(path, _check)
    if status == "INVALID":
        raise ValueError(f"registry INVALID: {problems}")
    return state


def verify(path: Path, anchor: dict | None = None) -> tuple[str, list[str]]:
    return chain.verify(path, _check, anchor)


def holdout_start(last_complete_month: str, months: int = HOLDOUT_MONTHS) -> int:
    """Start (ms) of the holdout: the last `months` complete months up to `last_complete_month`."""
    y, m = map(int, last_complete_month.split("-"))
    first = y * 12 + (m - 1) - (months - 1)
    return period_start_ms(f"{first // 12:04d}-{first % 12 + 1:02d}")


def register(path: Path, *, hypothesis_id: str, family: str, params: dict, dataset_hash: str,
             holdout_start: int, at: int) -> dict:
    return chain.append(path, {
        "type": "REGISTRATION", "hypothesis_id": hypothesis_id, "family": family, "params": params,
        "dataset_hash": dataset_hash, "holdout_start": holdout_start, "thresholds": THRESHOLDS, "at": at,
    }, _check)


def registration(path: Path, hypothesis_id: str) -> dict:
    reg = _state(path).get("registered", {}).get(hypothesis_id)
    if reg is None:
        raise NotRegistered(f"{hypothesis_id} has no pre-registration (CN-CP-003 57.3)")
    return reg


def n_variants(path: Path, family: str) -> int:
    return sum(r["family"] == family for r in _state(path).get("registered", {}).values())


def holdout_opened_by(path: Path, hypothesis_id: str) -> bool:
    return hypothesis_id in _state(path).get("opened", {}).values()


def research_view(path: Path, hypothesis_id: str, klines: list) -> list:
    """The candles a registered hypothesis may use: those that closed before its holdout."""
    reg = registration(path, hypothesis_id)
    return [k for k in klines if k.close_time < reg["holdout_start"]]


def open_holdout(path: Path, hypothesis_id: str, klines: list, purpose: str, at: int) -> list:
    state = _state(path)
    reg = state.get("registered", {}).get(hypothesis_id)
    opened = state.get("opened", {})
    if reg is None:
        reason = "not registered (CN-CP-003 57.3)"
    elif purpose != FINAL_G8:
        reason = "the holdout is opened only for the final G8 (CN-CP-003 57.1)"
    elif reg["family"] in opened:
        reason = f"holdout already opened for family {reg['family']} by {opened[reg['family']]} (CN-CP-003 57.1)"
    else:
        reason = None
    if reason:
        chain.append(path, {"type": "HOLDOUT_REFUSED", "hypothesis_id": hypothesis_id, "purpose": purpose,
                            "reason": reason, "at": at}, _check)
        raise HoldoutRefused(reason)
    chain.append(path, {"type": "HOLDOUT_OPENED", "hypothesis_id": hypothesis_id, "purpose": FINAL_G8, "at": at}, _check)
    return [k for k in klines if k.open_time >= reg["holdout_start"]]


def record_g8(path: Path, hypothesis_id: str, result: dict, at: int) -> dict:
    return chain.append(path, {"type": "G8_RESULT", "hypothesis_id": hypothesis_id, "result": result, "at": at}, _check)
