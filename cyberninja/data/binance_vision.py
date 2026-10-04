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
MARKET_PATH = "data/futures/um/monthly/klines"  # um = USD-M futures, klines = last price
PRICE_TYPE = "LAST"  # markPriceKlines would be MARK; not used here

COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
    "4h": 14_400_000, "1d": 86_400_000,
}
# 1w is deliberately absent: weekly candles open Monday 00:00 UTC, not on an
# epoch multiple, so the alignment check below would be wrong for them.


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


def archive_name(symbol: str, interval: str, month: str) -> str:
    return f"{symbol}-{interval}-{month}.zip"


def archive_url(symbol: str, interval: str, month: str) -> str:
    return f"https://{HOST}/{MARKET_PATH}/{symbol}/{interval}/{archive_name(symbol, interval, month)}"


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
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        (member,) = zf.namelist()
        text = zf.read(member).decode("ascii")
    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0] and rows[0][0] == "open_time":  # newer files carry a header
        rows = rows[1:]
    out = []
    for r in rows:
        if not r:
            continue
        if len(r) != len(COLUMNS):
            raise ValueError(f"expected {len(COLUMNS)} columns, got {len(r)}: {r}")
        out.append(Kline(
            open_time=int(r[0]), open=r[1], high=r[2], low=r[3], close=r[4],
            volume=r[5], close_time=int(r[6]), quote_volume=r[7], count=int(r[8]),
            taker_buy_volume=r[9], taker_buy_quote_volume=r[10],
        ))
    return out


def dataset_hash(klines: list[Kline]) -> str:
    """DATASET_HASH per CN-CP-001 §52.1 (proposal, approval PENDING).

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
