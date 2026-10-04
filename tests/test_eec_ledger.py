import json

import pytest

from cyberninja.eec import ledger
from cyberninja.eec.event_id import event_id
from test_data_g0_g1 import H4, T0
from test_eec_event_id_timeline import payload

DS = "ds_" + "a" * 64


def rec(i=0, **over):
    p = payload(source_open_times=[T0 + i * H4, T0 + (i + 1) * H4, T0 + (i + 2) * H4])
    conf = T0 + (i + 3) * H4 - 1
    r = p | {"event_id": event_id(p), "event_time": conf, "formation_time": T0 + i * H4,
             "confirmation_time": conf, "available_from": conf + 1, "receive_time": 5,
             "dataset_hash": DS, "feature_version": "f1", "validation_status": "VALID",
             "parent_event_id": None, "data": {"top": "42100.00", "bottom": "41950.50"}}
    return r | over


def test_append_and_verify(tmp_path):
    path = tmp_path / "ledger.jsonl"
    assert ledger.verify(path) == ("MISSING", [])
    a = ledger.append(path, rec(0))
    b = ledger.append(path, rec(1))
    assert (a["seq"], b["seq"]) == (0, 1) and b["prev_hash"] == a["record_hash"] and a["prev_hash"] == ledger.GENESIS
    assert ledger.verify(path) == ("VALID", [])
    assert [r["event_id"] for r in ledger.read(path)] == [rec(0)["event_id"], rec(1)["event_id"]]


def test_same_appends_give_identical_bytes(tmp_path):
    for name in ("x", "y"):
        for i in range(3):
            ledger.append(tmp_path / name, rec(i))
    assert (tmp_path / "x").read_bytes() == (tmp_path / "y").read_bytes()


@pytest.mark.parametrize("over,msg", [
    ({"event_id": "ev_" + "0" * 64}, "recomputed"),
    ({"available_from": 0}, "available_from"),
    ({"formation_time": T0 + 10 * H4}, "formation_time"),
    ({"dataset_hash": "abc"}, "dataset_hash"),
    ({"validation_status": "OK"}, "validation_status"),
    ({"data": {"rsi": 55.5}}, "float"),
    ({"receive_time": "5"}, "integer"),
    ({"parent_event_id": "ev_" + "1" * 64}, "not in the ledger"),
])
def test_invalid_record_is_refused(tmp_path, over, msg):
    with pytest.raises(ValueError, match=msg):
        ledger.append(tmp_path / "l", rec(0, **over))
    assert not (tmp_path / "l").exists()


def test_extra_or_missing_field_is_refused(tmp_path):
    with pytest.raises(ValueError):
        ledger.append(tmp_path / "l", rec(0) | {"note": "x"})
    r = rec(0)
    del r["data"]
    with pytest.raises(ValueError):
        ledger.append(tmp_path / "l", r)


def test_re_observation_needs_a_parent(tmp_path):
    path = tmp_path / "l"
    first = ledger.append(path, rec(0))
    with pytest.raises(ValueError, match="parent_event_id"):
        ledger.append(path, rec(0))                                 # silent duplicate
    # CN-CP-002 51.3: same EVENT_ID on revised data → new CONFLICT record pointing at the original
    conflict = ledger.append(path, rec(0, validation_status="CONFLICT", parent_event_id=first["event_id"],
                                       dataset_hash="ds_" + "b" * 64, data={"top": "42100.10", "bottom": "41950.50"}))
    assert conflict["event_id"] == first["event_id"] and ledger.verify(path)[0] == "VALID"
    assert ledger.read(path)[0] == first                            # original untouched


def lines(path):
    return path.read_text("utf-8").splitlines()


@pytest.fixture
def three(tmp_path):
    path = tmp_path / "l"
    for i in range(3):
        ledger.append(path, rec(i))
    return path


def test_edited_line_is_detected(three):
    ls = lines(three)
    r = json.loads(ls[1])
    r["data"]["top"] = "1.00"
    ls[1] = json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    three.write_text("\n".join(ls) + "\n", "utf-8")
    status, problems = ledger.verify(three)
    assert status == "INVALID" and any("record_hash" in p for p in problems)
    with pytest.raises(ValueError):
        ledger.append(three, rec(5))                                # never extend a broken chain


def test_rehashed_edit_breaks_the_next_link(three):
    ls = lines(three)
    r = json.loads(ls[1])
    r["data"]["top"] = "1.00"
    r["record_hash"] = ledger._hash(r)                             # forger recomputes this record's hash
    ls[1] = json.dumps(r)
    three.write_text("\n".join(ls) + "\n", "utf-8")
    assert any("line 2: prev_hash" in p for p in ledger.verify(three)[1])


def test_deleted_or_reordered_lines_are_invalid(three):
    ls = lines(three)
    for bad in (ls[:1] + ls[2:], [ls[1], ls[0], ls[2]]):
        three.write_text("\n".join(bad) + "\n", "utf-8")
        assert ledger.verify(three)[0] == "INVALID"


def test_truncated_write_is_invalid(three):
    three.write_bytes(three.read_bytes()[:-10])
    assert ledger.verify(three) == ("INVALID", ["last line is incomplete (truncated write)"])


def test_tail_truncation_needs_an_anchor(three):
    anchor = ledger.head(three)
    assert anchor["seq"] == 2 and ledger.verify(three, anchor) == ("VALID", [])
    three.write_text("\n".join(lines(three)[:2]) + "\n", "utf-8")   # whole last line removed
    assert ledger.verify(three)[0] == "VALID"                        # undetectable without an anchor
    status, problems = ledger.verify(three, anchor)
    assert status == "INVALID" and "lines removed from the end" in problems[0]
    three.write_text("", "utf-8")
    assert ledger.verify(three, anchor)[0] == "INVALID"


def test_anchor_still_holds_after_later_appends(three):
    anchor = ledger.head(three)
    ledger.append(three, rec(5))
    assert ledger.verify(three, anchor) == ("VALID", [])
    assert ledger.verify(three, {"seq": 1, "record_hash": "0" * 64})[0] == "INVALID"


def test_consistently_rehashed_chain_with_a_skipped_seq_is_invalid(tmp_path):
    path, prev, out = tmp_path / "l", ledger.GENESIS, []
    for seq, i in ((0, 0), (2, 1)):                              # seq 1 skipped, hashes and links all consistent
        full = rec(i) | {"seq": seq, "prev_hash": prev}
        full["record_hash"] = ledger._hash(full)
        out.append(ledger._canonical(full))
        prev = full["record_hash"]
    path.write_bytes(b"\n".join(out) + b"\n")
    status, problems = ledger.verify(path)
    assert status == "INVALID" and problems == ["line 1: seq 2"]
