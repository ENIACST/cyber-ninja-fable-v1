"""Gate results per CN-CP-002 XVI.1–XVI.2.

A gate is the worst of its inputs: FAIL > MISSING > UNKNOWN > PASS.
A gate with no inputs did not run, and a gate that did not run is MISSING.
"""

TO_GATE = {
    "VALID": "PASS",
    "INVALID": "FAIL",
    "CONFLICT": "FAIL",
    "STALE": "FAIL",
    "MISSING": "MISSING",
    "UNKNOWN": "UNKNOWN",
}
ORDER = ("FAIL", "MISSING", "UNKNOWN", "PASS")


def gate_result(statuses) -> str:
    results = {TO_GATE[s] for s in statuses}  # an unknown status word raises, it is never guessed
    return next((r for r in ORDER if r in results), "MISSING")
