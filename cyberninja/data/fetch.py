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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import binance_vision as bv
from .integrity import beyond_period, check, compare_sources
from ..gates import gate_result

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
SERVER_TIME_URL = "https://fapi.binance.com/fapi/v1/time"
REST_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
REST_LIMIT = 1500


def months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def days(start: str, end: str) -> list[str]:
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def periods(start: str, end: str) -> list[str]:
    kinds = {bv.period_kind(start), bv.period_kind(end)}
    if len(kinds) != 1:
        raise ValueError("--start and --end must both be YYYY-MM (monthly) or both YYYY-MM-DD (daily)")
    return months(start, end) if kinds == {"monthly"} else days(start, end)


def period_start_ms(period: str) -> int:
    d = date.fromisoformat(period if bv.period_kind(period) == "daily" else period + "-01")
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def period_end_ms(period: str) -> int:
    if bv.period_kind(period) == "daily":
        d = date.fromisoformat(period) + timedelta(days=1)
        return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
    y, m = map(int, period.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def load_archive(symbol, interval, period, out_dir: Path, fetch=bv.fetch):
    name = bv.archive_name(symbol, interval, period)
    url = bv.archive_url(symbol, interval, period)
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


def read_archive(entry: dict, data: bytes, period_end: int):
    """Parse a verified archive. Identity (checksum, G0) and content (G1) are
    separate findings: unreadable content is kept as evidence and marked INVALID."""
    try:
        rows = bv.parse_zip(data)
        entry["content_status"] = "VALID"
    except bv.MalformedArchive as e:
        rows = []
        entry |= {"content_status": "INVALID", "error": str(e)}
    return rows, beyond_period(rows, period_end)


def data_version(files) -> str | None:
    # Derived from the archive hashes: a re-published archive is a new DATA_VERSION (CN-CP-002 52.3).
    verified = sorted(f"{f['file']}:{f['sha256']}" for f in files if f["checksum_status"] == "VALID")
    return "bv-" + hashlib.sha256("\n".join(verified).encode()).hexdigest() if verified else None


def g1_inputs(report, files) -> list[str]:
    """The archive-only part of G1: everything replay can recompute offline."""
    return [report.status] + [f["content_status"] for f in files if "content_status" in f]


def rest_klines(symbol, interval, start_ms, end_ms, fetch) -> list:
    out, t = [], start_ms
    while t < end_ms:
        batch = json.loads(fetch(f"{REST_KLINES_URL}?symbol={symbol}&interval={interval}"
                                 f"&startTime={t}&endTime={end_ms - 1}&limit={REST_LIMIT}"))
        out.extend(bv.parse_rest(batch))
        if len(batch) < REST_LIMIT:
            break
        t = int(batch[-1][6]) + 1
    return out


def secondary_validation(symbol, interval, start_ms, end_ms, archive, server_time, fetch) -> dict:
    """§10 / §15: the same range from the independent REST endpoint, compared candle by candle."""
    doc = {"source": REST_KLINES_URL, "compared": 0, "conflicts": [], "only_archive": [], "only_rest": []}
    try:
        rest = rest_klines(symbol, interval, start_ms, end_ms, fetch)
    except (urllib.error.URLError, OSError) as e:
        return doc | {"status": "MISSING", "error": str(e)}
    except (ValueError, KeyError, IndexError, TypeError) as e:
        return doc | {"status": "INVALID", "error": f"unreadable REST response: {e}"}
    if server_time is None:
        return doc | {"status": "UNKNOWN", "error": "no SERVER_TIME: closed-bar law (18.1) cannot be applied"}
    rest = [k for k in rest if server_time >= k.close_time + 1]  # CN-CP-001 18.1: closed candles only
    a, r = {k.open_time for k in archive}, {k.open_time for k in rest}
    doc |= {"compared": len(a & r), "conflicts": [list(c) for c in compare_sources(archive, rest)],
            "only_archive": sorted(a - r), "only_rest": sorted(r - a)}
    if doc["conflicts"] or doc["only_rest"]:
        status = "CONFLICT"   # the two sources disagree on a value or on whether a candle exists
    elif doc["only_archive"] or not a:
        status = "MISSING"    # nothing, or not everything, could be confirmed
    else:
        status = "VALID"
    return doc | {"status": status}


def integrity_doc(report) -> dict:
    # JSON round-trip so tuples compare equal to what a reader of the file sees.
    return json.loads(json.dumps(asdict(report) | {"status": report.status, "missing_candles": report.missing_candles}))


def run(symbol, interval, start, end, out: Path, fetch=bv.fetch, now_ms=None):
    out.mkdir(parents=True, exist_ok=True)
    files, klines, beyond = [], [], []
    plist = periods(start, end)
    for period in plist:
        try:
            entry, data = load_archive(symbol, interval, period, out, fetch)
        except bv.ChecksumMismatch as e:
            files.append({"file": bv.archive_name(symbol, interval, period), "checksum_status": "INVALID", "error": str(e)})
            continue
        except (urllib.error.URLError, OSError) as e:
            files.append({"file": bv.archive_name(symbol, interval, period), "checksum_status": "MISSING", "error": str(e)})
            continue
        rows, outside = read_archive(entry, data, period_end_ms(period))
        files.append(entry)
        klines.extend(rows)
        beyond.extend(outside)

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
    secondary = secondary_validation(symbol, interval, period_start_ms(plist[0]), period_end_ms(plist[-1]),
                                     klines, server_time, fetch)
    manifest = {
        "fingerprint": {
            "SOURCE": "Binance Futures (USD-M) public archive",
            "HOST": bv.HOST,
            "ENDPOINT": f"/{bv.MARKET_PATH.format(kind=bv.period_kind(start))}/{symbol}/{interval}/",
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
            "DATA_VERSION": data_version(files),
            "DATASET_HASH": bv.dataset_hash(klines) if klines else None,
        },
        "files": files,
        "G0": gate_result([f["checksum_status"] for f in files] + [contract_status]),
        "SECONDARY_VALIDATION": secondary,
        "G1_INTEGRITY": gate_result(g1_inputs(report, files)),
        "G1": gate_result(g1_inputs(report, files) + [secondary["status"]]),
    }
    rep = integrity_doc(report)

    run_dir = out / "runs" / f"{symbol}-{interval}-{start}-{end}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for suffix, doc in (("manifest", manifest), ("integrity", rep)):
        with open(run_dir / f"{local_receive_ms}.{suffix}.json", "x") as f:  # "x": refuse to overwrite
            f.write(json.dumps(doc, indent=2) + "\n")
    return manifest, rep


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbol", default="BTCUSDT")
    # No 1w: a weekly candle crossing a month end cannot satisfy CN-CP-002 18.4 in a
    # monthly archive. W1 is built from complete D1 weeks (cyberninja.data.resample).
    p.add_argument("--interval", default="4h", choices=sorted(set(bv.INTERVAL_MS) - {"1w"}))
    p.add_argument("--start", required=True, help="YYYY-MM (monthly archives) or YYYY-MM-DD (daily archives)")
    p.add_argument("--end", required=True, help="same form as --start, inclusive; completed periods only")
    p.add_argument("--out", type=Path, default=Path("data/binance_um"))
    a = p.parse_args(argv)
    manifest, rep = run(a.symbol, a.interval, a.start, a.end, a.out)
    print(f"G0={manifest['G0']} G1={manifest['G1']} candles={rep['candles']} "
          f"missing={rep['missing_candles']} duplicates={len(rep['duplicates'])} "
          f"invalid_ohlc={len(rep['invalid_ohlc'])} hash={manifest['fingerprint']['DATASET_HASH']}")
    return 0 if manifest["G0"] == "PASS" and manifest["G1"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
