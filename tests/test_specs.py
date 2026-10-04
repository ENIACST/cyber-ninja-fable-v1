import json
from decimal import Decimal
from pathlib import Path

import pytest

from cyberninja.specs import FIELDS, g3, parse

TEMPLATE = Path(__file__).resolve().parents[1] / "specs/TEMPLATE.md"
ROWS = [[1704067200000, "100", "110", "95", "105", "1", 1704081599999, "1", 1, "0.5", "0.5", "0"],
        [1704081600000, "105", "106", "99", "100", "1", 1704095999999, "1", 1, "0.5", "0.5", "0"]]


def bull_count(klines):            # a trivial stand-in detector, for the tests only
    return [{"bull": sum(Decimal(k.close) > Decimal(k.open) for k in klines)}]


def write_spec(tmp_path, vectors=None, **over):
    fields = {f: "x" for f in FIELDS} | {"ORIGIN": "research", "CANDLE_BASIS": "RAW",
                                        "GOLDEN_VECTORS": "golden/test.json"} | over
    (tmp_path / "test.md").write_text("\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n\nnotes\n", "utf-8")
    if vectors is not None:
        (tmp_path / "golden").mkdir(exist_ok=True)
        (tmp_path / "golden/test.json").write_text(json.dumps(vectors), "utf-8")
    return tmp_path / "test.md"


GOOD = [{"name": "one bull of two", "input": ROWS, "expected": [{"bull": 1}]}]


def test_template_is_complete_and_missing_by_construction():
    assert set(parse(TEMPLATE)) == set(FIELDS) and not any(parse(TEMPLATE).values())
    status, problems = g3(TEMPLATE, bull_count)
    assert status == "MISSING" and len([p for p in problems if p.endswith("is empty")]) == len(FIELDS)


def test_t20_spec_without_golden_vectors_is_missing(tmp_path):
    assert g3(write_spec(tmp_path), bull_count)[0] == "MISSING"                     # file absent
    assert g3(write_spec(tmp_path, vectors=[]), bull_count)[0] == "MISSING"         # empty list
    assert g3(write_spec(tmp_path, vectors=GOOD, GOLDEN_VECTORS=""), bull_count)[0] == "MISSING"


def test_golden_vectors_pass_and_fail(tmp_path):
    spec = write_spec(tmp_path, vectors=GOOD)
    assert g3(spec, bull_count) == ("PASS", [])
    assert g3(spec, lambda k: [{"bull": 2}])[0] == "FAIL"
    assert g3(spec, None)[0] == "MISSING"                                            # nothing to check


@pytest.mark.parametrize("over", [{"CANDLE_BASIS": "HEIKIN"}, {"ORIGIN": "AI"}])
def test_invalid_enumerations_fail(tmp_path, over):
    assert g3(write_spec(tmp_path, vectors=GOOD, **over), bull_count)[0] == "FAIL"


def test_malformed_vectors_fail_and_absent_spec_is_missing(tmp_path):
    assert g3(write_spec(tmp_path, vectors=[{"name": "no input"}]), bull_count)[0] == "FAIL"
    (tmp_path / "golden/test.json").write_text("not json", "utf-8")
    assert g3(tmp_path / "test.md", bull_count)[0] == "FAIL"
    assert g3(tmp_path / "absent.md", bull_count)[0] == "MISSING"
