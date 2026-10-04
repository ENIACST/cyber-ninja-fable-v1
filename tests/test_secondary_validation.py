import json

import cyberninja.data.fetch as fetch_mod
from cyberninja.data.fetch import run
from test_data_g0_g1 import H4, T0, fake_fetcher, make_zip, rest_rows, row

JAN = {"BTCUSDT-4h-2024-01.zip": make_zip([row(T0 + i * H4) for i in range(31 * 6)])}


def go(tmp_path, now_ms=0, **kw):
    return run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path, fetch=fake_fetcher(JAN, **kw), now_ms=now_ms)[0]


def test_matching_rest_confirms_every_candle(tmp_path):
    m = go(tmp_path)
    sv = m["SECONDARY_VALIDATION"]
    assert sv["status"] == "VALID" and sv["compared"] == 31 * 6 and not sv["conflicts"]
    assert m["G1_INTEGRITY"] == "PASS" and m["G1"] == "PASS"


def test_manifest_in_memory_equals_the_file(tmp_path):
    t = T0 + 5 * H4
    rest = rest_rows(JAN, {t: [t, "1", "2", "0.5", "1", "1", t + H4 - 1, "1", 1, "1", "1", "0"]})
    m = go(tmp_path, rest=rest)
    assert json.loads((tmp_path / "runs/BTCUSDT-4h-2024-01-2024-01/0.manifest.json").read_text()) == m


def test_value_disagreement_is_conflict(tmp_path):
    t = T0 + 5 * H4
    rest = rest_rows(JAN, {t: [t, "42000.10", "42100.00", "41900.00", "42049.99", "10.5", t + H4 - 1,
                                "441000.0", 100, "5.0", "210000.0", "0"]})
    m = go(tmp_path, rest=rest)
    assert m["SECONDARY_VALIDATION"]["conflicts"] == [[t, "close"]]
    assert m["SECONDARY_VALIDATION"]["status"] == "CONFLICT"
    assert m["G1_INTEGRITY"] == "PASS" and m["G1"] == "FAIL"


def test_candle_only_in_rest_is_conflict_and_only_in_archive_is_missing(tmp_path):
    t = T0 + 7 * H4
    m = go(tmp_path, rest=rest_rows(JAN, {t: None}))          # REST lacks one candle
    assert m["SECONDARY_VALIDATION"]["only_archive"] == [t]
    assert m["SECONDARY_VALIDATION"]["status"] == "MISSING" and m["G1"] == "MISSING"

    gappy = {"BTCUSDT-4h-2024-01.zip": make_zip([row(T0 + i * H4) for i in range(31 * 6) if i != 7])}
    m = run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path,
            fetch=fake_fetcher(gappy, rest=rest_rows(JAN)), now_ms=1)[0]
    assert m["SECONDARY_VALIDATION"]["only_rest"] == [t]      # archive gap that REST fills
    assert m["SECONDARY_VALIDATION"]["status"] == "CONFLICT" and m["G1"] == "FAIL"


def test_unclosed_rest_candle_is_ignored(tmp_path):
    last = T0 + (31 * 6 - 1) * H4
    m = go(tmp_path, server_time=last + H4 - 1)               # last candle still open per SERVER_TIME
    sv = m["SECONDARY_VALIDATION"]
    assert sv["only_archive"] == [last] and sv["only_rest"] == [] and sv["status"] == "MISSING"
    closed = go(tmp_path, now_ms=1, server_time=last + H4)    # closed at close_time + 1
    assert closed["SECONDARY_VALIDATION"]["status"] == "VALID"


def test_offline_secondary_is_missing_and_g1_cannot_pass(tmp_path):
    m = go(tmp_path, online=False)
    assert m["SECONDARY_VALIDATION"]["status"] == "MISSING"
    assert m["G1_INTEGRITY"] == "PASS" and m["G1"] == "MISSING"


def test_unreadable_rest_response_is_invalid(tmp_path):
    m = go(tmp_path, rest=[["not", "a", "kline"]])
    assert m["SECONDARY_VALIDATION"]["status"] == "INVALID" and m["G1"] == "FAIL"


def test_rest_is_paginated(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod, "REST_LIMIT", 50)
    calls = []
    base = fake_fetcher(JAN)
    def counting(url):
        if "/fapi/v1/klines" in url:
            calls.append(url)
        return base(url)
    m = run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path, fetch=counting, now_ms=0)[0]
    assert m["SECONDARY_VALIDATION"]["compared"] == 31 * 6 and len(calls) == 4   # 50+50+50+36
