import json

import pytest

from cyberninja.data.fetch import run
from cyberninja.data.replay import replay
from test_data_g0_g1 import H4, T0, fake_fetcher, make_zip, row

FEB = T0 + 31 * 86_400_000


def record(tmp_path, archives, now_ms=1):
    manifest, _ = run("BTCUSDT", "4h", "2024-01", "2024-02", tmp_path, fetch=fake_fetcher(archives), now_ms=now_ms)
    return manifest, tmp_path / f"runs/BTCUSDT-4h-2024-01-2024-02/{now_ms}.manifest.json"


@pytest.fixture
def clean():
    return {"BTCUSDT-4h-2024-01.zip": make_zip([row(T0 + i * H4) for i in range(31 * 6)]),
            "BTCUSDT-4h-2024-02.zip": make_zip([row(FEB + i * H4) for i in range(29 * 6)])}


def archive_path(tmp_path, manifest, i=0):
    f = manifest["files"][i]
    return tmp_path / "archives" / f["file"].removesuffix(".zip") / f"{f['sha256']}.zip"


def test_offline_replay_reproduces_a_recorded_run(tmp_path, clean):
    manifest, path = record(tmp_path, clean)
    r = replay(path, now_ms=2)   # replay takes no fetch function: it cannot touch the network
    assert r["REPRODUCED"] == "PASS" and r["differences"] == []
    assert r["recomputed"]["DATASET_HASH"] == manifest["fingerprint"]["DATASET_HASH"]
    assert json.loads(path.with_name("2.replay.json").read_text()) == r


def test_tampered_archive_on_disk_is_detected(tmp_path, clean):
    manifest, path = record(tmp_path, clean)
    archive_path(tmp_path, manifest).write_bytes(make_zip([row(T0, c="1.00")]))
    r = replay(path, now_ms=2)
    assert r["REPRODUCED"] == "FAIL" and r["files"][0]["checksum_status"] == "INVALID"
    assert "DATASET_HASH" in r["differences"] and "DATA_VERSION" in r["differences"]


def test_missing_archive_is_missing_and_not_reproduced(tmp_path, clean):
    manifest, path = record(tmp_path, clean)
    archive_path(tmp_path, manifest, 1).unlink()
    r = replay(path, now_ms=2)
    assert r["files"][1]["checksum_status"] == "MISSING" and r["REPRODUCED"] == "FAIL"


def test_recorded_value_that_does_not_follow_from_the_data_is_reported(tmp_path, clean):
    _, path = record(tmp_path, clean)
    m = json.loads(path.read_text())
    m["fingerprint"]["DATASET_HASH"] = "ds_" + "0" * 64
    path.write_text(json.dumps(m))
    assert replay(path, now_ms=2)["differences"] == ["DATASET_HASH"]


def test_invalid_content_and_missing_months_are_reproduced_too(tmp_path):
    archives = {"BTCUSDT-4h-2024-01.zip": b"not a zip"}   # February unreachable
    manifest, path = record(tmp_path, archives)
    assert manifest["G1"] == "FAIL"
    r = replay(path, now_ms=2)
    assert r["REPRODUCED"] == "PASS" and r["recomputed"]["G1"] == "FAIL"


def test_replay_never_overwrites(tmp_path, clean):
    _, path = record(tmp_path, clean)
    replay(path, now_ms=2)
    with pytest.raises(FileExistsError):
        replay(path, now_ms=2)


def test_recorded_integrity_report_that_does_not_follow_from_the_data_is_reported(tmp_path, clean):
    _, path = record(tmp_path, clean)
    ipath = path.with_name("1.integrity.json")
    rep = json.loads(ipath.read_text())
    rep["zero_volume"] = [T0]
    ipath.write_text(json.dumps(rep))
    assert replay(path, now_ms=2)["differences"] == ["integrity"]
