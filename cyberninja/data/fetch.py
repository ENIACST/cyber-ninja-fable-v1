"""Download, verify and integrity-check Binance USD-M futures klines.

    python -m cyberninja.data.fetch --symbol BTCUSDT --interval 4h --start 2024-01 --end 2024-06

Writes the raw zips, a manifest (G0 fingerprint) and an integrity report (G1)
under --out. Existing zips are reused only if they still match their checksum.
"""

import argparse
import json
import sys
import time
import urllib.error
from dataclasses import asdict
from pathlib import Path

from . import binance_vision as bv
from .integrity import check

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


def load_month(symbol, interval, month, out_dir: Path, fetch=bv.fetch):
    name = bv.archive_name(symbol, interval, month)
    url = bv.archive_url(symbol, interval, month)
    checksum = fetch(url + ".CHECKSUM").decode("ascii")
    path = out_dir / name
    data = path.read_bytes() if path.exists() else None
    try:
        if data is None:
            raise bv.ChecksumMismatch("not cached")
        sha = bv.verify_zip(data, checksum, name)
    except bv.ChecksumMismatch:
        data = fetch(url)
        sha = bv.verify_zip(data, checksum, name)  # raises: an unverified zip is never parsed
        path.write_bytes(data)
    return {"file": name, "url": url, "sha256": sha, "checksum_status": "VALID"}, bv.parse_zip(data)


def run(symbol, interval, start, end, out: Path, fetch=bv.fetch, now_ms=None):
    out.mkdir(parents=True, exist_ok=True)
    files, klines = [], []
    for month in months(start, end):
        try:
            entry, rows = load_month(symbol, interval, month, out, fetch)
        except bv.ChecksumMismatch as e:
            files.append({"file": bv.archive_name(symbol, interval, month), "checksum_status": "INVALID", "error": str(e)})
            continue
        except (urllib.error.URLError, OSError) as e:
            files.append({"file": bv.archive_name(symbol, interval, month), "checksum_status": "MISSING", "error": str(e)})
            continue
        files.append(entry)
        klines.extend(rows)

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
    archive_statuses = {f["checksum_status"] for f in files}
    if "INVALID" in archive_statuses or contract_status in ("INVALID", "MISSING"):
        g0 = "FAIL"
    elif archive_statuses != {"VALID"}:
        g0 = "MISSING"  # §7: an unreachable archive is MISSING, not FALSE
    elif contract_status == "UNKNOWN":
        g0 = "UNKNOWN"
    else:
        g0 = "PASS"
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
            "DATA_VERSION": "binance-vision-monthly",
            "DATASET_HASH": bv.dataset_hash(klines) if klines else None,
        },
        "files": files,
        "G0": g0,
        "G1": report.status,
    }
    rep = asdict(report) | {"status": report.status, "missing_candles": report.missing_candles}

    stem = f"{symbol}-{interval}-{start}-{end}"
    (out / f"{stem}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / f"{stem}.integrity.json").write_text(json.dumps(rep, indent=2) + "\n")
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
    return 0 if manifest["G0"] == "PASS" and manifest["G1"] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
