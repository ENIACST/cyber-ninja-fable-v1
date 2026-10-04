"""Download, verify and integrity-check Binance USD-M futures klines.

    python -m cyberninja.data.fetch --symbol BTCUSDT --interval 4h --start 2024-01 --end 2024-06

Under --out, nothing is ever overwritten (§5, CN-CP-002 52.3):
  archives/<archive>/<sha256>.zip   verified zips, one file per published version
  runs/<dataset>/<run_id>.manifest.json   G0 fingerprint, DATASET_HASH, gate results
  runs/<dataset>/<run_id>.integrity.json  G1 report
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.error
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import binance_vision as bv
from .integrity import beyond_period, check
from ..gates import gate_result

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
SERVER_TIME_URL = "https://fapi.binance.com/fapi/v1/time"


def months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_end_ms(month: str) -> int:
    y, m = map(int, month.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def load_month(symbol, interval, month, out_dir: Path, fetch=bv.fetch):
    name = bv.archive_name(symbol, interval, month)
    url = bv.archive_url(symbol, interval, month)
    checksum = fetch(url + ".CHECKSUM").decode("ascii")
    expected = bv.parse_checksum(checksum, name)
    # Stored under its own hash: a re-published archive lands beside the old
    # version instead of replacing it (CN-CP-002 52.3).
    vdir = out_dir / "archives" / name.removesuffix(".zip")
    path = vdir / f"{expected}.zip"
    data = path.read_bytes() if path.exists() else None
    try:
        if data is None:
            raise bv.ChecksumMismatch("not cached")
        sha = bv.verify_zip(data, checksum, name)
    except bv.ChecksumMismatch:
        data = fetch(url)
        sha = bv.verify_zip(data, checksum, name)  # raises: an unverified zip is never stored or parsed
        vdir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)  # replaces only a file whose bytes did not match its own name
    others = sorted(p.stem for p in vdir.glob("*.zip") if p.stem != sha)
    entry = {"file": name, "url": url, "sha256": sha, "checksum_status": "VALID",
             "other_versions_on_disk": others}
    return entry, data


def run(symbol, interval, start, end, out: Path, fetch=bv.fetch, now_ms=None):
    out.mkdir(parents=True, exist_ok=True)
    files, klines, beyond = [], [], []
    for month in months(start, end):
        try:
            entry, data = load_month(symbol, interval, month, out, fetch)
        except bv.ChecksumMismatch as e:
            files.append({"file": bv.archive_name(symbol, interval, month), "checksum_status": "INVALID", "error": str(e)})
            continue
        except (urllib.error.URLError, OSError) as e:
            files.append({"file": bv.archive_name(symbol, interval, month), "checksum_status": "MISSING", "error": str(e)})
            continue
        # Identity (checksum, G0) and content (G1) are separate findings: a verified
        # archive with unreadable content is kept as evidence and marked INVALID.
        try:
            rows = bv.parse_zip(data)
            entry["content_status"] = "VALID"
        except bv.MalformedArchive as e:
            rows = []
            entry |= {"content_status": "INVALID", "error": str(e)}
        files.append(entry)
        klines.extend(rows)
        beyond.extend(beyond_period(rows, month_end_ms(month)))

    local_receive_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    # G0: confirm the contract and the server clock against the live API.
    # Failure to reach it leaves the field UNKNOWN; it is never assumed.
    contract_status, server_time, offset = "UNKNOWN", None, None
    try:
        contract_status = bv.verify_contract(json.loads(fetch(EXCHANGE_INFO_URL)), symbol)
        server_time = json.loads(fetch(SERVER_TIME_URL))["serverTime"]
        offset = server_time - local_receive_ms
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        pass

    report = check(klines, interval)
    report.beyond_period = beyond
    verified = sorted(f"{f['file']}:{f['sha256']}" for f in files if f["checksum_status"] == "VALID")
    manifest = {
        "fingerprint": {
            "SOURCE": "Binance Futures (USD-M) public archive",
            "HOST": bv.HOST,
            "ENDPOINT": f"/{bv.MARKET_PATH}/{symbol}/{interval}/",
            "SYMBOL": symbol,
            "PAIR": symbol,
            "CONTRACT_TYPE": bv.contract_type_from_symbol(symbol),
            "CONTRACT_TYPE_STATUS": contract_status,
            "PRICE_TYPE": bv.PRICE_TYPE,
            "INTERVAL": interval,
            "LOCAL_RECEIVE_TIME": local_receive_ms,
            "SERVER_TIME": server_time,
            "SERVER_TIME_OFFSET": offset,
            "DATASET_ID": f"binance-um-{symbol}-{interval}-{start}-{end}",
            # Derived from the archive hashes: a re-published archive is a new DATA_VERSION (CN-CP-002 52.3).
            "DATA_VERSION": "bv-" + hashlib.sha256("\n".join(verified).encode()).hexdigest() if verified else None,
            "DATASET_HASH": bv.dataset_hash(klines) if klines else None,
        },
        "files": files,
        "G0": gate_result([f["checksum_status"] for f in files] + [contract_status]),
        "G1": gate_result([report.status] + [f["content_status"] for f in files if "content_status" in f]),
    }
    rep = asdict(report) | {"status": report.status, "missing_candles": report.missing_candles}

    run_dir = out / "runs" / f"{symbol}-{interval}-{start}-{end}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for suffix, doc in (("manifest", manifest), ("integrity", rep)):
        with open(run_dir / f"{local_receive_ms}.{suffix}.json", "x") as f:  # "x": refuse to overwrite
            f.write(json.dumps(doc, indent=2) + "\n")
    return manifest, rep


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="4h", choices=sorted(bv.INTERVAL_MS))
    p.add_argument("--start", required=True, help="YYYY-MM")
    p.add_argument("--end", required=True, help="YYYY-MM (inclusive, completed months only)")
    p.add_argument("--out", type=Path, default=Path("data/binance_um"))
    a = p.parse_args(argv)
    manifest, rep = run(a.symbol, a.interval, a.start, a.end, a.out)
    print(f"G0={manifest['G0']} G1={manifest['G1']} candles={rep['candles']} "
          f"missing={rep['missing_candles']} duplicates={len(rep['duplicates'])} "
          f"invalid_ohlc={len(rep['invalid_ohlc'])} hash={manifest['fingerprint']['DATASET_HASH']}")
    return 0 if manifest["G0"] == "PASS" and manifest["G1"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
