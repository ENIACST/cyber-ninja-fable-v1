"""G0 — Data Source Identity for Binance USD-M Futures klines (doctrine §10–§13).

Source: the public archive at data.binance.vision. Each monthly zip ships with
a .CHECKSUM file (sha256); a zip whose hash does not match is INVALID and is
never parsed.

Prices are kept as the exact strings Binance publishes. Nothing is rounded.
"""

import csv
import hashlib
import io
import re
import urllib.request
import zipfile
from dataclasses import dataclass

HOST = "data.binance.vision"
# um = USD-M futures, klines = last price; {kind} is "monthly" or "daily"
MARKET_PATH = "data/futures/um/{kind}/klines"
PRICE_TYPE = "LAST"  # markPriceKlines would be MARK; not used here

COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
    "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000,
}
# CN-CP-001 18.2: W1 opens Monday 00:00 UTC. The epoch (1970-01-01) was a
# Thursday, so weekly open_times sit 4 days after a multiple of 7 days.
ALIGN_OFFSET_MS = {"1w": 4 * 86_400_000}


@dataclass(frozen=True)
class Kline:
    open_time: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    close_time: int
    quote_volume: str
    count: int
    taker_buy_volume: str
    taker_buy_quote_volume: str


class ChecksumMismatch(Exception):
    pass


class MalformedArchive(Exception):
    """The archive passed its checksum but its content is not a kline CSV."""


def archive_name(symbol: str, interval: str, month: str) -> str:
    return f"{symbol}-{interval}-{month}.zip"


def period_kind(period: str) -> str:
    """"YYYY-MM" is a monthly archive, "YYYY-MM-DD" a daily one."""
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return "monthly"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return "daily"
    raise ValueError(f"period must be YYYY-MM or YYYY-MM-DD, got {period!r}")


def archive_url(symbol: str, interval: str, period: str) -> str:
    path = MARKET_PATH.format(kind=period_kind(period))
    return f"https://{HOST}/{path}/{symbol}/{interval}/{archive_name(symbol, interval, period)}"


def contract_type_from_symbol(symbol: str) -> str:
    """USD-M naming: BTCUSDT is the perpetual, BTCUSDT_240628 a quarterly.

    This is a naming convention, not exchange confirmation; the fingerprint
    marks it UNKNOWN until exchangeInfo has been checked (see verify_contract).
    """
    return "QUARTERLY" if re.search(r"_\d{6}$", symbol) else "PERPETUAL"


def verify_contract(exchange_info: dict, symbol: str) -> str:
    """G0 check against a /fapi/v1/exchangeInfo payload. Returns a doctrine status."""
    for s in exchange_info.get("symbols", []):
        if s.get("symbol") == symbol:
            return "VALID" if s.get("contractType") == "PERPETUAL" else "INVALID"
    return "MISSING"


def fetch(url: str, timeout: float = 30.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def parse_checksum(text: str, expected_name: str) -> str:
    # Format: "<sha256>  <file name>"
    digest, _, name = text.strip().partition(" ")
    if name.strip() != expected_name or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ChecksumMismatch(f"malformed CHECKSUM for {expected_name}: {text!r}")
    return digest


def verify_zip(zip_bytes: bytes, checksum_text: str, name: str) -> str:
    expected = parse_checksum(checksum_text, name)
    actual = hashlib.sha256(zip_bytes).hexdigest()
    if actual != expected:
        raise ChecksumMismatch(f"{name}: sha256 {actual} != published {expected}")
    return actual


def parse_zip(zip_bytes: bytes) -> list[Kline]:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            members = zf.namelist()
            if len(members) != 1:
                raise MalformedArchive(f"expected 1 file in archive, found {len(members)}")
            text = zf.read(members[0]).decode("ascii")
    except (zipfile.BadZipFile, UnicodeDecodeError) as e:
        raise MalformedArchive(str(e)) from e
    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0] and rows[0][0] == "open_time":  # newer files carry a header
        rows = rows[1:]
    out = []
    for r in rows:
        if not r:
            continue
        if len(r) != len(COLUMNS):
            raise MalformedArchive(f"expected {len(COLUMNS)} columns, got {len(r)}: {r}")
        try:
            out.append(Kline(
                open_time=int(r[0]), open=r[1], high=r[2], low=r[3], close=r[4],
                volume=r[5], close_time=int(r[6]), quote_volume=r[7], count=int(r[8]),
                taker_buy_volume=r[9], taker_buy_quote_volume=r[10],
            ))
        except ValueError as e:
            raise MalformedArchive(f"non-integer time or count: {r}") from e
    return out


def parse_rest(rows: list) -> list[Kline]:
    """/fapi/v1/klines rows: [open_time, "o", "h", "l", "c", "v", close_time, "qv", count, "tbv", "tbqv", "ignore"]."""
    return [Kline(
        open_time=int(r[0]), open=str(r[1]), high=str(r[2]), low=str(r[3]), close=str(r[4]),
        volume=str(r[5]), close_time=int(r[6]), quote_volume=str(r[7]), count=int(r[8]),
        taker_buy_volume=str(r[9]), taker_buy_quote_volume=str(r[10]),
    ) for r in rows]


def dataset_hash(klines: list[Kline]) -> str:
    """DATASET_HASH per CN-CP-001 §52.1 (approved 2026-10-04).

    Line = open_time|open|high|low|close|volume|close_time, Binance strings,
    sorted by open_time, identical lines once. Two different lines with the same
    open_time both stay in: that dataset is already G1 INVALID and must not hash
    like either clean version.
    """
    lines = sorted({
        (k.open_time, f"{k.open_time}|{k.open}|{k.high}|{k.low}|{k.close}|{k.volume}|{k.close_time}\n")
        for k in klines
    })
    h = hashlib.sha256()
    for _, line in lines:
        h.update(line.encode("utf-8"))
    return "ds_" + h.hexdigest()
