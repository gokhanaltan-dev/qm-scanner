"""The list of US common stocks to scan.

Sources are tried in order and the first one that answers is used:
  1. Nasdaq's stock screener (all NYSE / Nasdaq / AMEX listings, with sector and last price)
  2. a public daily mirror of the same screener on GitHub
  3. the Nasdaq Trader symbol directory (symbols only)
  4. the list saved by the last successful run
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd
import requests

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
SCREENER = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25000&download=true"
MIRROR = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/{ex}/{ex}_full_tickers.json"
SYMDIR = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

NOT_COMMON = re.compile(
    r"\b(?:warrants?|units?|rights?|preferred|preference|notes?|debentures?|bonds?|"
    r"etf|etn|fund)\b|%",
    re.I,
)


def _log(msg: str) -> None:
    print(f"[universe] {msg}", flush=True)


def _num(x) -> float:
    try:
        return float(str(x).replace("$", "").replace(",", "").replace("%", "").strip() or "nan")
    except ValueError:
        return float("nan")


def _from_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)

    def col(name: str) -> pd.Series:   # a missing column becomes blanks, not an error
        return df[name] if name in df else pd.Series("", index=df.index, dtype=object)

    out = pd.DataFrame({
        "symbol": col("symbol").astype(str).str.strip(),
        "name": col("name").fillna("").astype(str).str.strip(),
        "sector": col("sector").fillna("").astype(str).str.strip(),
        "industry": col("industry").fillna("").astype(str).str.strip(),
        "last": col("lastsale").map(_num),
        "volume": col("volume").map(_num),
        "mcap": col("marketCap").map(_num),
    })
    return out


def _screener() -> pd.DataFrame:
    r = requests.get(SCREENER, headers=UA, timeout=30)
    r.raise_for_status()
    rows = r.json()["data"]["rows"]
    return _from_rows(rows)


def _mirror() -> pd.DataFrame:
    rows = []
    for ex in ("nasdaq", "nyse", "amex"):
        r = requests.get(MIRROR.format(ex=ex), headers=UA, timeout=30)
        r.raise_for_status()
        rows.extend(r.json())
    return _from_rows(rows)


def _symdir() -> pd.DataFrame:
    r = requests.get(SYMDIR, headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="|")
    df = df[(df["ETF"] == "N") & (df["Test Issue"] == "N") & (df["Nasdaq Traded"] == "Y")]
    return pd.DataFrame({
        "symbol": df["Symbol"].astype(str), "name": df["Security Name"].astype(str),
        "sector": "", "industry": "", "last": float("nan"), "volume": float("nan"),
        "mcap": float("nan"),
    })


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Keep ordinary shares only and convert symbols to Yahoo's format."""
    df = df.dropna(subset=["symbol"]).copy()
    df["symbol"] = df["symbol"].str.upper().str.strip()
    ok = df["symbol"].str.fullmatch(r"[A-Z]{1,5}([./][A-Z])?")
    df = df[ok]
    df = df[~df["name"].str.contains(NOT_COMMON)]
    # Nasdaq five-letter suffixes: W warrant, R right, U unit
    five = df["symbol"].str.fullmatch(r"[A-Z]{5}") & df["symbol"].str[-1].isin(list("WRU"))
    df = df[~five]
    df["symbol"] = df["symbol"].str.replace(r"[./]", "-", regex=True)
    return df.drop_duplicates("symbol").reset_index(drop=True)


def prefilter(df: pd.DataFrame, min_price: float, min_dollar_volume: float) -> pd.DataFrame:
    """Loose cut on the screener's last price and volume, so obviously untradeable names
    are not downloaded. The real filters run later on 20-day averages."""
    if df["last"].notna().mean() < 0.5:
        return df
    keep = df["last"].isna() | (
        (df["last"] >= 0.7 * min_price)
        & ((df["last"] * df["volume"].fillna(0)) >= 0.1 * min_dollar_volume)
    )
    return df[keep].reset_index(drop=True)


def load(cache: Path, min_price: float, min_dollar_volume: float) -> tuple[pd.DataFrame, str]:
    for name, fn in (("nasdaq screener", _screener), ("github mirror", _mirror),
                     ("nasdaq trader directory", _symdir)):
        try:
            df = clean(fn())
            if len(df) < 2000:
                raise ValueError(f"only {len(df)} symbols")
            cache.parent.mkdir(parents=True, exist_ok=True)
            # Save names only (no prices), so the file changes only when listings do
            keep = df[["symbol", "name", "sector", "industry"]].sort_values("symbol")
            cache.write_text(json.dumps(keep.to_dict("records"), indent=0))
            df = prefilter(df, min_price, min_dollar_volume)
            _log(f"{name}: {len(df)} stocks after the loose price / volume cut")
            return df, name
        except Exception as e:
            _log(f"{name} failed: {e}")
    if cache.exists():
        df = pd.DataFrame(json.loads(cache.read_text()))
        for col in ("last", "volume", "mcap"):   # not saved: every stock gets downloaded
            df[col] = float("nan")
        _log(f"using the saved list: {len(df)} stocks")
        return prefilter(df, min_price, min_dollar_volume), "saved list"
    raise RuntimeError("No stock list could be loaded from any source.")
