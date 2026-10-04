import hashlib
import json
import os
import subprocess
import sys

import pytest

from cyberninja.eec.event_id import PAYLOAD_FIELDS, canonical_payload, event_id, event_id_of
from cyberninja.eec.timeline import (
    EventView, available_from, g2, htf_visible, is_closed, lookahead_violations, pivot_confirmation_time)
from test_data_g0_g1 import H4, T0, klines, row

DAY = 86_400_000


def payload(**over):
    p = {
        "event_type": "TEST_EVENT", "detector_id": "test_detector", "detector_version": "1.0.0",
        "params": {"b": 2, "a": "x", "note": "ш"}, "source": "BINANCE_FUTURES_UM", "symbol": "BTCUSDT",
        "contract_type": "PERPETUAL", "price_type": "LAST", "timeframe": "4h",
        "source_open_times": [T0, T0 + H4, T0 + 2 * H4], "direction": "BULL",
    }
    return p | over


# --- §51.1 recipe ------------------------------------------------------------------

def test_canonical_payload_is_exactly_the_recipe():
    expected = ('{"contract_type":"PERPETUAL","detector_id":"test_detector","detector_version":"1.0.0",'
                '"direction":"BULL","event_type":"TEST_EVENT","params":{"a":"x","b":2,"note":"ш"},'
                '"price_type":"LAST","source":"BINANCE_FUTURES_UM",'
                '"source_open_times":[1704067200000,1704081600000,1704096000000],'
                '"symbol":"BTCUSDT","timeframe":"4h"}').encode("utf-8")   # sorted keys, no spaces, raw UTF-8
    assert canonical_payload(payload()) == expected
    assert event_id(payload()) == "ev_" + hashlib.sha256(expected).hexdigest()


def test_t1_determinism_across_independent_processes():
    code = ("import json,sys;from cyberninja.eec.event_id import event_id;"
            "p=json.loads(sys.argv[1]);print(event_id(dict(reversed(list(p.items())))))")
    ids = set()
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", code, json.dumps(payload())], env=env,
                             capture_output=True, text=True, check=True, cwd=os.path.dirname(os.path.dirname(__file__)))
        ids.add(out.stdout.strip())
    assert ids == {event_id(payload())}


@pytest.mark.parametrize("change", [
    {"detector_version": "1.0.1"},
    {"params": {"b": 3, "a": "x", "note": "ш"}},
    {"params": {"b": 2, "a": "x", "note": "ш", "extra": 1}},
    {"source_open_times": [T0, T0 + H4]},
    {"direction": "BEAR"},
    {"price_type": "MARK"},
    {"timeframe": "1h"},
])
def test_t2_sensitivity(change):
    assert event_id(payload(**change)) != event_id(payload())


def test_t3_neutrality_to_observation_fields():
    record = payload() | {"receive_time": 1, "local_receive_time": 2, "server_time_offset": 3,
                          "validation_status": "VALID", "mitigation_status": "OPEN", "dataset_hash": "ds_a"}
    changed = record | {"receive_time": 99, "local_receive_time": 98, "server_time_offset": -5,
                        "validation_status": "CONFLICT", "mitigation_status": "MITIGATED", "dataset_hash": "ds_b"}
    assert event_id_of(record) == event_id_of(changed) == event_id(payload())


@pytest.mark.parametrize("bad", [
    {"params": {"gap": 0.5}},                       # 51.2: float
    {"params": {"nested": [1, 2.0]}},
    {"price_type": "HA"},
    {"direction": "UP"},
    {"source_open_times": []},
    {"source_open_times": [T0 + H4, T0]},           # not ascending
    {"source_open_times": [T0, T0]},                # duplicate candle
    {"source_open_times": [str(T0)]},
    {"source_open_times": [True]},
    {"symbol": ""},
    {"params": "length=3"},
])
def test_invalid_payload_is_rejected(bad):
    with pytest.raises(ValueError):
        event_id(payload(**bad))


def test_missing_or_extra_field_is_rejected():
    p = payload()
    del p["direction"]
    with pytest.raises(ValueError):
        event_id(p)
    with pytest.raises(ValueError):
        event_id(payload(receive_time=1))
    assert len(PAYLOAD_FIELDS) == 11


# --- 18.1 / 18.3 ---------------------------------------------------------------------

def test_closed_bar_law():
    k = klines([row(T0)])[0]
    assert not is_closed(k.close_time, server_time=k.close_time)
    assert is_closed(k.close_time, server_time=k.close_time + 1)
    assert available_from(k.close_time) == T0 + H4


def test_pivot_confirmation_needs_the_right_bars():
    candles = klines([row(T0 + i * H4) for i in range(5)])
    assert pivot_confirmation_time(candles, pivot_index=1, right_bars=2) == candles[3].close_time
    assert pivot_confirmation_time(candles, pivot_index=3, right_bars=2) is None   # not confirmed yet


def test_t4_candle_by_candle_replay_never_reads_the_future():
    candles = klines([row(T0 + i * H4) for i in range(12)])
    # a pivot at candle 2 with R = 2 is confirmed at the close of candle 4
    conf = pivot_confirmation_time(candles, 2, 2)
    events = [{"event_id": f"ev_{i}", "formation_time": candles[i].open_time,
               "confirmation_time": candles[i + 2].close_time,
               "available_from": available_from(candles[i + 2].close_time)} for i in range(0, 9, 2)]
    view, reads = EventView(events), []
    for k in candles:
        decision_time = k.close_time + 1          # deciding once candle k has closed
        for e in view.at(decision_time):
            reads.append((decision_time, e))
        if k.close_time < conf:
            assert "ev_2" not in {e["event_id"] for e in view.at(decision_time)}
    assert any(e["event_id"] == "ev_2" for _, e in reads)
    assert g2(reads) == "PASS"

    cheating = [(candles[0].close_time + 1, e) for e in events]   # reads everything at the first candle
    assert g2(cheating) == "FAIL" and len(lookahead_violations(cheating)) == len(events)
    assert g2([]) == "MISSING"


def test_event_is_visible_exactly_from_available_from():
    e = {"event_id": "ev_x", "available_from": T0 + H4}
    assert EventView([e]).at(T0 + H4 - 1) == []
    assert EventView([e]).at(T0 + H4) == [e]
    assert lookahead_violations([(T0 + H4, e)]) == [] and lookahead_violations([(T0 + H4 - 1, e)]) == [(T0 + H4 - 1, "ev_x")]


def test_t5_d1_value_is_not_visible_on_4h_before_the_d1_candle_closes():
    d1 = klines([row(T0, step=DAY)])
    h4 = klines([row(T0 + i * H4) for i in range(7)])     # six 4H candles of day 1, then 00:00 of day 2
    seen = [len(htf_visible(d1, k.open_time)) for k in h4]
    assert seen == [0, 0, 0, 0, 0, 0, 1]
