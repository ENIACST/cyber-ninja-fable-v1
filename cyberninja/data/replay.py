"""Offline replay of a recorded run (§9 Reproducibility, §53).

    python -m cyberninja.data.replay data/binance_um/runs/<dataset>/<run_id>.manifest.json

Re-reads the archives the manifest names, by their recorded sha256, from the
same data directory, and recomputes everything that depends only on them:
content status, DATASET_HASH, DATA_VERSION, the integrity report and
G1_INTEGRITY. No network: G0 and the REST secondary validation in G1 need the
live API and are not replayed. The result is written
next to the manifest as <now>.replay.json; nothing is overwritten.
"""

import hashlib
import json
import sys
import time
from pathlib import Path

from . import binance_vision as bv
from ..gates import gate_result
from .fetch import data_version, g1_inputs, integrity_doc, period_end_ms, read_archive
from .integrity import check

SCOPE = ("DATASET_HASH", "DATA_VERSION", "G1_INTEGRITY", "integrity")


def replay(manifest_path: Path, now_ms=None):
    manifest_path = Path(manifest_path)
    out = manifest_path.parents[2]  # <out>/runs/<dataset>/<run_id>.manifest.json
    manifest = json.loads(manifest_path.read_text())
    integrity_path = manifest_path.with_name(manifest_path.name.replace(".manifest.json", ".integrity.json"))
    fp = manifest["fingerprint"]
    symbol, interval = fp["SYMBOL"], fp["INTERVAL"]

    files, klines, beyond = [], [], []
    for f in manifest["files"]:
        if f["checksum_status"] != "VALID":
            files.append(dict(f))  # nothing was stored for it; the same finding stands
            continue
        path = out / "archives" / f["file"].removesuffix(".zip") / f"{f['sha256']}.zip"
        entry = {"file": f["file"], "sha256": f["sha256"]}
        if not path.exists():
            files.append(entry | {"checksum_status": "MISSING", "error": f"{path} not on disk"})
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            files.append(entry | {"checksum_status": "INVALID", "error": "bytes on disk do not match recorded sha256"})
            continue
        entry["checksum_status"] = "VALID"
        period = f["file"].removeprefix(f"{symbol}-{interval}-").removesuffix(".zip")
        rows, outside = read_archive(entry, data, period_end_ms(period))
        files.append(entry)
        klines.extend(rows)
        beyond.extend(outside)

    report = check(klines, interval)
    report.beyond_period = beyond
    recomputed = {
        "DATASET_HASH": bv.dataset_hash(klines) if klines else None,
        "DATA_VERSION": data_version(files),
        "G1_INTEGRITY": gate_result(g1_inputs(report, files)),
        "integrity": integrity_doc(report),
    }
    recorded = {
        "DATASET_HASH": fp["DATASET_HASH"],
        "DATA_VERSION": fp["DATA_VERSION"],
        "G1_INTEGRITY": manifest["G1_INTEGRITY"],
        "integrity": json.loads(integrity_path.read_text()),
    }
    differences = [k for k in SCOPE if recorded[k] != recomputed[k]]
    replayed_at = now_ms if now_ms is not None else int(time.time() * 1000)
    result = {
        "manifest": manifest_path.name,
        "replayed_at": replayed_at,
        "scope": list(SCOPE),
        "REPRODUCED": "FAIL" if differences else "PASS",
        "differences": differences,
        "recomputed": recomputed,
        "files": files,
    }
    with open(manifest_path.with_name(f"{replayed_at}.replay.json"), "x") as fh:  # "x": refuse to overwrite
        fh.write(json.dumps(result, indent=2) + "\n")
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    r = replay(Path(argv[0]))
    print(f"REPRODUCED={r['REPRODUCED']} differences={r['differences']} "
          f"G1_INTEGRITY={r['recomputed']['G1_INTEGRITY']} hash={r['recomputed']['DATASET_HASH']}")
    return 0 if r["REPRODUCED"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
