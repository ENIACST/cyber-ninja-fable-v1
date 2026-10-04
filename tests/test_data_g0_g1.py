import hashlib
import io
import json
import urllib.error
import zipfile

import pytest

from cyberninja.data import binance_vision as bv
from cyberninja.data.fetch import month_end_ms, months, run
from cyberninja.data.integrity import beyond_period, check, compare_sources
from cyberninja.gates import gate_result

H4 = bv.INTERVAL_MS["4h"]
T0 = 1704067200000  # 2024-01-01 00:00:00 UTC
HEADER = ",".join(bv.COLUMNS)


def row(t, o="42000.10", h="42100.00", l="41900.00", c="42050.00", v="10.5", tb="5.0", step=H4, count=100):
    return f"{t},{o},{h},{l},{c},{v},{t + step - 1},441000.0,{count},{tb},210000.0,0"


def make_zip(lines, name="BTCUSDT-4h-2024-01.csv", header=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, "\n".join(([HEADER] if header else []) + lines) + "\n")
    return buf.getvalue()


def klines(lines):
    return bv.parse_zip(make_zip(lines))


# --- G0 ---------------------------------------------------------------------

def test_archive_url_points_at_usdm_last_price_klines():
    assert bv.archive_url("BTCUSDT", "4h", "2024-01") == (
        "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/4h/BTCUSDT-4h-2024-01.zip")


def test_checksum_mismatch_is_rejected_before_parsing():
    z = make_zip([row(T0)])
    good = f"{hashlib.sha256(z).hexdigest()}  BTCUSDT-4h-2024-01.zip\n"
    assert bv.verify_zip(z, good, "BTCUSDT-4h-2024-01.zip") == hashlib.sha256(z).hexdigest()
    with pytest.raises(bv.ChecksumMismatch):
        bv.verify_zip(z + b"x", good, "BTCUSDT-4h-2024-01.zip")
    with pytest.raises(bv.ChecksumMismatch):  # checksum published for another file
        bv.verify_zip(z, good.replace("2024-01", "2024-02"), "BTCUSDT-4h-2024-01.zip")


def test_contract_identity():
    assert bv.contract_type_from_symbol("BTCUSDT") == "PERPETUAL"
    assert bv.contract_type_from_symbol("BTCUSDT_240628") == "QUARTERLY"
    info = {"symbols": [{"symbol": "BTCUSDT", "contractType": "PERPETUAL"},
                        {"symbol": "BTCUSDT_240628", "contractType": "CURRENT_QUARTER"}]}
    assert bv.verify_contract(info, "BTCUSDT") == "VALID"
    assert bv.verify_contract(info, "BTCUSDT_240628") == "INVALID"
    assert bv.verify_contract(info, "ETHUSDT") == "MISSING"


def test_parse_with_and_without_header_keeps_source_strings():
    a = bv.parse_zip(make_zip([row(T0)], header=True))
    b = bv.parse_zip(make_zip([row(T0)], header=False))
    assert a == b and a[0].open == "42000.10"  # trailing zero preserved, no float


# --- DATASET_HASH (CN-CP-001 §52.1, T6) -----------------------------------

def test_dataset_hash_recipe_and_determinism():
    ks = klines([row(T0), row(T0 + H4)])
    expected = "ds_" + hashlib.sha256(
        (f"{T0}|42000.10|42100.00|41900.00|42050.00|10.5|{T0 + H4 - 1}\n"
         f"{T0 + H4}|42000.10|42100.00|41900.00|42050.00|10.5|{T0 + 2 * H4 - 1}\n").encode()).hexdigest()
    assert bv.dataset_hash(ks) == expected
    assert bv.dataset_hash(list(reversed(ks))) == expected        # order-independent
    assert bv.dataset_hash(ks + ks[:1]) == expected               # identical duplicate counted once
    assert bv.dataset_hash(klines([row(T0, c="42050.01"), row(T0 + H4)])) != expected


def test_conflicting_duplicate_never_hashes_like_a_clean_dataset():
    clean = klines([row(T0)])
    dirty = klines([row(T0), row(T0, c="42060.00")])
    assert bv.dataset_hash(dirty) != bv.dataset_hash(clean)
    assert bv.dataset_hash(dirty) != bv.dataset_hash(klines([row(T0, c="42060.00")]))
    assert check(dirty, "4h").status == "INVALID"


# --- G1 ---------------------------------------------------------------------

def test_clean_series_is_valid():
    rep = check(klines([row(T0 + i * H4) for i in range(6)]), "4h")
    assert rep.status == "VALID" and rep.candles == 6 and rep.missing_candles == 0


def test_gap_is_missing_not_filled():
    rep = check(klines([row(T0), row(T0 + 3 * H4)]), "4h")
    assert rep.gaps == [(T0 + H4, 2)] and rep.status == "MISSING" and rep.candles == 2
    assert check(klines([row(T0), row(T0 + 2 * H4)]), "4h").gaps == [(T0 + H4, 1)]


def test_duplicate_is_invalid():
    rep = check(klines([row(T0), row(T0), row(T0 + H4)]), "4h")
    assert rep.duplicates == [T0] and rep.status == "INVALID"


@pytest.mark.parametrize("o,h,l,c", [
    ("100", "99", "90", "95"),    # high below open
    ("100", "110", "101", "105"),  # low above open
    ("100", "110", "120", "105"),  # low above high
    ("100", "110", "0", "105"),    # non-positive low
    ("100", "110", "NaN", "105"),
])
def test_invalid_ohlc(o, h, l, c):
    assert check(klines([row(T0, o=o, h=h, l=l, c=c)]), "4h").status == "INVALID"


def test_volume_rules():
    assert check(klines([row(T0, v="-1")]), "4h").status == "INVALID"
    assert check(klines([row(T0, v="5", tb="6")]), "4h").status == "INVALID"  # taker buy > total
    rep = check(klines([row(T0, v="0", tb="0", count=0)]), "4h")
    assert rep.zero_volume == [T0] and rep.status == "VALID"  # reported, not invalidating


def test_timestamp_unit_and_timezone_shift_are_caught():
    shifted = T0 + 3_600_000  # e.g. a UTC+1 local timestamp
    assert check(klines([row(shifted)]), "4h").misaligned == [shifted]
    micro = klines([row(T0)])[0].__class__(**{**klines([row(T0)])[0].__dict__, "open_time": T0 * 1000})
    assert check([micro], "4h").status == "INVALID"
    assert check(klines([row(T0, step=H4 - 1000)]), "4h").bad_close_time == [T0]


def test_stale():
    ks = klines([row(T0), row(T0 + H4)])
    assert check(ks, "4h", as_of_ms=T0 + 2 * H4).status == "VALID"       # newest closed candle present
    assert check(ks, "4h", as_of_ms=T0 + 3 * H4).status == "STALE"


def test_compare_sources_flags_conflict_by_value_not_spelling():
    a = klines([row(T0, c="42050.00"), row(T0 + H4)])
    b = klines([row(T0, c="42050.0"), row(T0 + H4, c="42051.00")])
    assert compare_sources(a, b) == [(T0 + H4, "close")]


# --- pipeline (offline: fetch is injected) -----------------------------------

def fake_fetcher(archives, online=True):
    def fetch(url):
        for name, z in archives.items():
            if url.endswith(name + ".CHECKSUM"):
                return f"{hashlib.sha256(z).hexdigest()}  {name}\n".encode()
            if url.endswith(name):
                return z
        if online and url.endswith("exchangeInfo"):
            return json.dumps({"symbols": [{"symbol": "BTCUSDT", "contractType": "PERPETUAL"}]}).encode()
        if online and url.endswith("/time"):
            return b'{"serverTime": 1735689600123}'
        raise urllib.error.URLError("offline")
    return fetch


def test_months():
    assert months("2023-11", "2024-02") == ["2023-11", "2023-12", "2024-01", "2024-02"]


def test_run_end_to_end(tmp_path):
    feb = T0 + 31 * 86_400_000
    archives = {
        "BTCUSDT-4h-2024-01.zip": make_zip([row(T0 + i * H4) for i in range(31 * 6)]),
        "BTCUSDT-4h-2024-02.zip": make_zip([row(feb + i * H4) for i in range(29 * 6)]),
    }
    manifest, rep = run("BTCUSDT", "4h", "2024-01", "2024-02", tmp_path,
                        fetch=fake_fetcher(archives), now_ms=1735689600000)
    assert manifest["G0"] == "PASS" and manifest["G1"] == "PASS"
    assert rep["candles"] == 60 * 6 and rep["beyond_period"] == []
    fp = manifest["fingerprint"]
    assert fp["CONTRACT_TYPE_STATUS"] == "VALID" and fp["SERVER_TIME_OFFSET"] == 123
    first = tmp_path / "runs/BTCUSDT-4h-2024-01-2024-02/1735689600000.manifest.json"
    assert json.loads(first.read_text()) == manifest

    # T6: same range again (now from cache) → same hash; the first run's record is kept
    again, _ = run("BTCUSDT", "4h", "2024-01", "2024-02", tmp_path,
                   fetch=fake_fetcher(archives), now_ms=1735689600001)
    assert again["fingerprint"]["DATASET_HASH"] == fp["DATASET_HASH"]
    assert again["fingerprint"]["DATA_VERSION"] == fp["DATA_VERSION"]
    assert json.loads(first.read_text()) == manifest


def test_run_refuses_to_overwrite_a_run_record(tmp_path):
    archives = {"BTCUSDT-4h-2024-01.zip": make_zip([row(T0)])}
    run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path, fetch=fake_fetcher(archives), now_ms=5)
    with pytest.raises(FileExistsError):
        run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path, fetch=fake_fetcher(archives), now_ms=5)


def test_run_offline_contract_is_unknown_not_assumed(tmp_path):
    archives = {"BTCUSDT-4h-2024-01.zip": make_zip([row(T0)])}
    manifest, _ = run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path,
                      fetch=fake_fetcher(archives, online=False), now_ms=0)
    assert manifest["fingerprint"]["CONTRACT_TYPE_STATUS"] == "UNKNOWN"
    assert manifest["G0"] == "UNKNOWN"


def test_run_corrupt_archive_fails_g0_and_unreachable_hash_is_null(tmp_path):
    z = make_zip([row(T0)])
    def fetch(url):
        if url.endswith(".CHECKSUM"):
            return f"{'0' * 64}  BTCUSDT-4h-2024-01.zip\n".encode()
        if url.endswith(".zip"):
            return z
        raise urllib.error.URLError("offline")
    manifest, _ = run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path, fetch=fetch, now_ms=0)
    assert manifest["G0"] == "FAIL" and manifest["files"][0]["checksum_status"] == "INVALID"
    assert manifest["fingerprint"]["DATASET_HASH"] is None
    assert not (tmp_path / "BTCUSDT-4h-2024-01.zip").exists()  # unverified bytes never stored


def test_run_missing_month_and_corrupt_cache(tmp_path):
    good = make_zip([row(T0)])
    cached = tmp_path / "archives/BTCUSDT-4h-2024-01" / f"{hashlib.sha256(good).hexdigest()}.zip"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"corrupt")  # bytes do not match the name: re-downloaded, not trusted
    manifest, _ = run("BTCUSDT", "4h", "2024-01", "2024-02", tmp_path,
                      fetch=fake_fetcher({"BTCUSDT-4h-2024-01.zip": good}), now_ms=0)
    statuses = {f["file"]: f["checksum_status"] for f in manifest["files"]}
    assert statuses == {"BTCUSDT-4h-2024-01.zip": "VALID", "BTCUSDT-4h-2024-02.zip": "MISSING"}
    assert manifest["G0"] == "MISSING"
    assert cached.read_bytes() == good


# --- CN-CP-002 ----------------------------------------------------------------

@pytest.mark.parametrize("statuses,expected", [
    (["VALID"], "PASS"),
    (["VALID", "VALID"], "PASS"),
    (["INVALID"], "FAIL"),
    (["CONFLICT"], "FAIL"),
    (["STALE"], "FAIL"),
    (["MISSING"], "MISSING"),
    (["UNKNOWN"], "UNKNOWN"),
    (["VALID", "UNKNOWN"], "UNKNOWN"),
    (["UNKNOWN", "MISSING"], "MISSING"),
    (["MISSING", "STALE"], "FAIL"),
    (["VALID", "UNKNOWN", "MISSING", "CONFLICT"], "FAIL"),
])
def test_t7_status_to_gate_mapping(statuses, expected):
    assert gate_result(statuses) == expected


def test_t7_unknown_status_word_is_rejected():
    with pytest.raises(KeyError):
        gate_result(["OK"])


def test_t8_gate_that_did_not_run_is_missing():
    assert gate_result([]) == "MISSING"


def test_t7_stale_with_gaps_is_reported_as_stale():
    rep = check(klines([row(T0), row(T0 + 2 * H4)]), "4h", as_of_ms=T0 + 10 * H4)
    assert rep.gaps and rep.stale and rep.status == "STALE" and gate_result([rep.status]) == "FAIL"


def test_g0_contract_not_listed_is_missing_not_fail(tmp_path):
    archives = {"BTCUSDT-4h-2024-01.zip": make_zip([row(T0)])}
    base = fake_fetcher(archives)
    def fetch(url):
        if url.endswith("exchangeInfo"):
            return b'{"symbols": []}'
        return base(url)
    manifest, _ = run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path, fetch=fetch, now_ms=0)
    assert manifest["fingerprint"]["CONTRACT_TYPE_STATUS"] == "MISSING" and manifest["G0"] == "MISSING"


def test_t10_republished_archive_is_a_new_version_and_the_old_one_is_kept(tmp_path):
    v1 = make_zip([row(T0)])
    v2 = make_zip([row(T0, c="42051.00")])
    m1, _ = run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path,
                fetch=fake_fetcher({"BTCUSDT-4h-2024-01.zip": v1}), now_ms=1)
    vdir = tmp_path / "archives/BTCUSDT-4h-2024-01"
    run1 = tmp_path / "runs/BTCUSDT-4h-2024-01-2024-01/1.manifest.json"
    run1_bytes = run1.read_bytes()

    m2, _ = run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path,
                fetch=fake_fetcher({"BTCUSDT-4h-2024-01.zip": v2}), now_ms=2)
    sha1, sha2 = hashlib.sha256(v1).hexdigest(), hashlib.sha256(v2).hexdigest()
    assert (vdir / f"{sha1}.zip").read_bytes() == v1           # old archive byte-identical
    assert (vdir / f"{sha2}.zip").read_bytes() == v2
    assert run1.read_bytes() == run1_bytes                      # old manifest byte-identical
    assert m2["fingerprint"]["DATA_VERSION"] != m1["fingerprint"]["DATA_VERSION"]
    assert m2["fingerprint"]["DATASET_HASH"] != m1["fingerprint"]["DATASET_HASH"]
    assert m2["files"][0]["other_versions_on_disk"] == [sha1]


def test_t11_candle_beyond_archive_period_is_invalid(tmp_path):
    end = month_end_ms("2024-01")
    assert end == T0 + 31 * 86_400_000
    assert month_end_ms("2024-12") == 1735689600000            # December rolls into the next year
    last = end - H4
    assert beyond_period(klines([row(last)]), end) == []
    assert beyond_period(klines([row(end)]), end) == [end]
    assert beyond_period(klines([row(last, step=H4 + 1)]), end) == [last]  # close_time == period end
    archives = {"BTCUSDT-4h-2024-01.zip": make_zip([row(last), row(end)])}
    manifest, rep = run("BTCUSDT", "4h", "2024-01", "2024-01", tmp_path,
                        fetch=fake_fetcher(archives), now_ms=0)
    assert rep["beyond_period"] == [end] and rep["status"] == "INVALID" and manifest["G1"] == "FAIL"
