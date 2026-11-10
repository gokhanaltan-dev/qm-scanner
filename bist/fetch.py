"""Download everything the BIST scanner and backtest need, from a machine with open internet
(GitHub Actions). Writes parquet/json files plus provenance.json into --out.

Sources
  universe   TradingView screener (current BIST listings, type=stock, sector labels)
  prices     Is Yatirim HisseTekil: raw + fully adjusted close/high/low/VWAP, TRY turnover,
             XU100 close and USD/TRY on the same dates (no open price)
  opens      Yahoo Finance daily OHLCV (.IS); used for the open and as a cross-check
  actions    Is Yatirim capital increases / dividends per share code
  indices    Yahoo Finance (XU100, XU030, sector indices), USDTRY, GBPTRY
"""
from __future__ import annotations

import argparse, json, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
IST = ZoneInfo("Europe/Istanbul")
ISY_KEEP = ["HG_KAPANIS", "HG_AOF", "HG_MIN", "HG_MAX", "HG_HACIM",
            "HGDG_KAPANIS", "HGDG_AOF", "HGDG_MIN", "HGDG_MAX", "END_DEGER", "DD_DEGER", "SERMAYE", "PD"]
INDICES = ["XU100", "XU030", "XU050", "XUTUM", "XBANK", "XUSIN", "XUMAL", "XUHIZ", "XUTEK", "XHOLD", "XGIDA",
           "XKMYA", "XMANA", "XMESY", "XTRZM", "XELKT", "XILTM", "XINSA", "XGMYO", "XTCRT", "XULAS", "XBLSM",
           "XSGRT", "XTAST", "XTEKS", "XKAGT", "XFINK", "XMADN", "XSPOR", "XYORT"]


def log(*a):
    print("[fetch]", *a, flush=True)


def get_universe():
    body = {"columns": ["name", "description", "type", "subtype", "sector", "industry", "close", "Value.Traded",
                        "market_cap_basic"],
            "range": [0, 2000], "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"}, "markets": ["turkey"]}
    r = requests.post("https://scanner.tradingview.com/turkey/scan", json=body, headers=UA, timeout=40)
    r.raise_for_status()
    rows = []
    for x in r.json()["data"]:
        d = x["d"]
        rows.append({"ticker": d[0], "name": d[1], "type": d[2], "subtype": d[3], "sector": d[4],
                     "industry": d[5], "tv_close": d[6], "tv_value_traded": d[7], "tv_mcap": d[8]})
    return rows


def isy_prices(code, start, end, session):
    u = ("https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
         f"?hisse={code}&startdate={start}&enddate={end}")
    last = None
    for attempt in range(4):
        try:
            r = session.get(u, headers=UA, timeout=60)
            if r.status_code == 200:
                v = r.json().get("value") or []
                if not v:
                    return code, None, "empty"
                df = pd.DataFrame(v)
                df["date"] = pd.to_datetime(df["HGDG_TARIH"], format="%d-%m-%Y")
                keep = [c for c in ISY_KEEP if c in df.columns]
                df = df[["date"] + keep].copy()
                df.insert(0, "ticker", code)
                return code, df, "ok"
            last = f"http {r.status_code}"
        except Exception as e:
            last = repr(e)[:120]
        time.sleep(3 * (attempt + 1))
    return code, None, last


def isy_actions(code, session):
    u = ("https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/StockInfo/CompanyInfoAjax.aspx/"
         "GetSermayeArttirimlari")
    body = {"hisseKodu": code, "hisseTanimKodu": "", "yil": 0, "zaman": "HEPSI", "endeksKodu": "09", "sektorKodu": ""}
    last = None
    for attempt in range(3):
        try:
            r = session.post(u, json=body, headers={**UA, "Content-Type": "application/json; charset=UTF-8",
                                                    "X-Requested-With": "XMLHttpRequest"}, timeout=40)
            if r.status_code == 200:
                rows = json.loads(r.json()["d"])
                keep = ["SHHE_TARIH", "SHT_KODU", "SHT_TANIMI_YD", "SHHE_BDLI_ORAN", "SHHE_BDLI_NOM_TUTAR",
                        "SHHE_RHK_ORAN", "SHHE_BDSZ_IK_ORAN", "SHHE_BDSZ_TM_ORAN", "SHHE_BDSZ_BU_ORAN",
                        "SHHE_NAKIT_TM_ORAN", "SHHE_NAKIT_TM_ORAN_NET", "SHHE_FIYAT_AYAR_OR", "SHHE_ACIKLAMA",
                        "HSP_BOLUNME_ONCESI_SERMAYE", "HSP_BOLUNME_SONRASI_SERMAYE", "SEKTOR_KODU"]
                out = [{"ticker": code, **{k: x.get(k) for k in keep}} for x in rows]
                return code, out, "ok"
            last = f"http {r.status_code}"
        except Exception as e:
            last = repr(e)[:120]
        time.sleep(2 * (attempt + 1))
    return code, None, last


def yahoo_batch(symbols, start):
    import yfinance as yf
    out = []
    for attempt in range(3):
        try:
            df = yf.download(symbols, start=start, interval="1d", auto_adjust=False, progress=False,
                             threads=True, group_by="ticker", timeout=40)
            if df is None or df.empty:
                raise RuntimeError("empty")
            for s in symbols:
                if isinstance(df.columns, pd.MultiIndex):
                    if s not in df.columns.get_level_values(0):
                        continue
                    one = df[s]
                else:
                    one = df
                one = one.dropna(subset=["Close"])
                if one.empty:
                    continue
                one = one[["Open", "High", "Low", "Close", "Volume"]].copy()
                one.index = pd.to_datetime(one.index).tz_localize(None).normalize()
                one = one.reset_index().rename(columns={"index": "date", "Date": "date"})
                one.insert(0, "symbol", s)
                out.append(one)
            return out
        except Exception as e:
            log("yahoo batch retry", attempt, repr(e)[:100])
            time.sleep(5 * (attempt + 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data_out")
    ap.add_argument("--start", default="2014-01-01")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = datetime.now(timezone.utc)
    prov = {"retrieved_utc_start": t0.isoformat(timespec="seconds"),
            "retrieved_istanbul_start": t0.astimezone(IST).isoformat(timespec="seconds"),
            "start": a.start, "sources": {}}

    uni = get_universe()
    with open(os.path.join(a.out, "universe.json"), "w", encoding="utf-8") as f:
        json.dump(uni, f, ensure_ascii=False)
    stocks = [u["ticker"] for u in uni if u["type"] == "stock"]
    if a.limit:
        stocks = stocks[:a.limit]
    prov["sources"]["universe"] = {"source": "TradingView screener (scanner.tradingview.com/turkey/scan)",
                                   "listed_rows": len(uni), "stocks": len(stocks)}
    log("universe", len(uni), "stocks", len(stocks))

    sd = datetime.strptime(a.start, "%Y-%m-%d").strftime("%d-%m-%Y")
    ed = datetime.now(IST).strftime("%d-%m-%Y")
    frames, fails = [], {}
    sess = requests.Session()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(isy_prices, c, sd, ed, sess) for c in stocks]
        for n, fu in enumerate(as_completed(futs), 1):
            code, df, st = fu.result()
            if df is None:
                fails[code] = st
            else:
                frames.append(df)
            if n % 100 == 0:
                log("isy prices", n, "/", len(stocks), "fails", len(fails))
    isy = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    isy.to_parquet(os.path.join(a.out, "isy_prices.parquet"), index=False)
    prov["sources"]["isy_prices"] = {
        "source": "Is Yatirim HisseTekil (raw HG_* and adjusted HGDG_* series)", "requested": len(stocks),
        "received": int(isy["ticker"].nunique()) if len(isy) else 0, "failed": fails,
        "first_date": str(isy["date"].min().date()) if len(isy) else None,
        "last_date": str(isy["date"].max().date()) if len(isy) else None, "rows": int(len(isy))}
    log("isy prices done", prov["sources"]["isy_prices"]["received"], "fails", len(fails))

    acts, afails = [], {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(isy_actions, c, sess) for c in stocks]
        for fu in as_completed(futs):
            code, rows, st = fu.result()
            if rows is None:
                afails[code] = st
            else:
                acts.extend(rows)
    pd.DataFrame(acts).to_parquet(os.path.join(a.out, "isy_actions.parquet"), index=False)
    prov["sources"]["isy_actions"] = {"source": "Is Yatirim GetSermayeArttirimlari", "rows": len(acts),
                                      "failed": afails}
    log("actions", len(acts), "fails", len(afails))

    ysyms = [f"{c}.IS" for c in stocks]
    yfr = []
    for i in range(0, len(ysyms), 100):
        yfr.extend(yahoo_batch(ysyms[i:i + 100], a.start))
        log("yahoo", min(i + 100, len(ysyms)), "/", len(ysyms), "frames", len(yfr))
        time.sleep(1)
    ya = pd.concat(yfr, ignore_index=True) if yfr else pd.DataFrame()
    ya.to_parquet(os.path.join(a.out, "yahoo_prices.parquet"), index=False)
    got = set(ya["symbol"].unique()) if len(ya) else set()
    prov["sources"]["yahoo_prices"] = {"source": "Yahoo Finance via yfinance, auto_adjust=False",
                                       "requested": len(ysyms), "received": len(got),
                                       "missing": sorted(s for s in ysyms if s not in got),
                                       "last_date": str(ya["date"].max().date()) if len(ya) else None}

    isyms = [f"{c}.IS" for c in INDICES] + ["USDTRY=X", "GBPTRY=X"]
    ifr = yahoo_batch(isyms, a.start)
    ix = pd.concat(ifr, ignore_index=True) if ifr else pd.DataFrame()
    ix.to_parquet(os.path.join(a.out, "yahoo_indices.parquet"), index=False)
    igot = sorted(ix["symbol"].unique()) if len(ix) else []
    prov["sources"]["yahoo_indices"] = {"received": igot, "missing": sorted(s for s in isyms if s not in igot)}

    t1 = datetime.now(timezone.utc)
    prov["retrieved_utc_end"] = t1.isoformat(timespec="seconds")
    prov["retrieved_istanbul_end"] = t1.astimezone(IST).isoformat(timespec="seconds")
    prov["delay_note"] = ("End-of-day data. Is Yatirim and Yahoo publish BIST prices with at least a 15-minute "
                          "delay; a bar dated today is only complete after the 18:10 Istanbul close.")
    with open(os.path.join(a.out, "provenance.json"), "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=1)
    log("done", json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk not in ("failed", "missing")}) for k, v in prov["sources"].items()})[:1500])
    if prov["sources"]["isy_prices"]["received"] < 0.8 * len(stocks):
        raise SystemExit("Too few price histories downloaded; treating this as a data outage.")


if __name__ == "__main__":
    main()
