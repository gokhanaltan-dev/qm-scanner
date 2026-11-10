"""One-off probe: which BIST data sources answer from a GitHub runner, and in what shape."""
import json, os, sys, time, traceback
import requests

OUT = "probe_out"
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
report = {}

def save(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text)

def step(name):
    def deco(fn):
        t = time.time()
        try:
            report[name] = {"ok": True, "info": fn()}
        except Exception as e:
            report[name] = {"ok": False, "error": repr(e)[:500], "tb": traceback.format_exc()[-800:]}
        report[name]["secs"] = round(time.time() - t, 1)
        print(name, json.dumps(report[name])[:600], flush=True)
    return deco

@step("yahoo_daily")
def _():
    import yfinance as yf
    info = {}
    for t in ["THYAO.IS", "XU100.IS", "ASELS.IS", "SASA.IS", "XBANK.IS", "XUSIN.IS", "XUTEK.IS", "XU030.IS", "KONTR.IS", "USDTRY=X", "GBPTRY=X"]:
        df = yf.Ticker(t).history(period="max", interval="1d", auto_adjust=False, actions=True)
        df.to_csv(os.path.join(OUT, f"yahoo_{t.replace('=','_')}.csv"))
        info[t] = {"rows": len(df), "first": str(df.index.min()), "last": str(df.index.max()),
                   "cols": list(df.columns),
                   "splits": int((df.get("Stock Splits", 0) != 0).sum()) if len(df) else 0,
                   "divs": int((df.get("Dividends", 0) != 0).sum()) if len(df) else 0}
    return info

@step("yahoo_batch")
def _():
    import yfinance as yf
    df = yf.download(["THYAO.IS", "GARAN.IS", "EREGL.IS", "TUPRS.IS", "FROTO.IS"], period="1mo", interval="1d",
                     auto_adjust=False, progress=False, group_by="column")
    save("yahoo_batch.csv", df.to_csv())
    return {"shape": list(df.shape), "last": str(df.index.max())}

@step("isy_hissetekil")
def _():
    u = ("https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
         "?hisse=THYAO&startdate=01-01-2016&enddate=10-11-2026")
    r = requests.get(u, headers=UA, timeout=40)
    save("isy_hissetekil_THYAO.json", r.text[:400000])
    j = r.json()
    v = j.get("value", [])
    return {"status": r.status_code, "n": len(v), "first": v[0] if v else None, "last": v[-1] if v else None}

@step("isy_sermaye")
def _():
    u = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/StockInfo/CompanyInfoAjax.aspx/GetSermayeArttirimlari"
    out = {}
    for code in ["THYAO", "SASA", "KONTR"]:
        body = {"hisseKodu": code, "hisseTanimKodu": "", "yil": 0, "zaman": "HEPSI", "endeksKodu": "09", "sektorKodu": ""}
        r = requests.post(u, json=body, headers={**UA, "Content-Type": "application/json; charset=UTF-8",
                                                "X-Requested-With": "XMLHttpRequest"}, timeout=40)
        save(f"isy_sermaye_{code}.json", r.text[:300000])
        out[code] = {"status": r.status_code, "len": len(r.text), "head": r.text[:300]}
    return out

@step("isy_index")
def _():
    u = ("https://www.isyatirim.com.tr/_Layouts/15/IsYatirim.Website/Common/ChartData.aspx/IndexHistoricalAll"
         "?period=1440&from=20160101000000&to=20261110235959&endeks=XU100")
    r = requests.get(u, headers=UA, timeout=40)
    save("isy_index_XU100.json", r.text[:200000])
    return {"status": r.status_code, "len": len(r.text), "head": r.text[:200]}

@step("isy_stocklist")
def _():
    u = "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/Temel-Degerler-Ve-Oranlar.aspx"
    r = requests.get(u, headers=UA, timeout=40)
    save("isy_stocklist.html", r.text[:1500000])
    return {"status": r.status_code, "len": len(r.text)}

@step("tv_scan")
def _():
    body = {"columns": ["name", "description", "type", "subtype", "typespecs", "sector", "industry", "close",
                        "volume", "Value.Traded", "average_volume_60d_calc", "market_cap_basic", "exchange"],
            "range": [0, 1500], "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
            "markets": ["turkey"]}
    r = requests.post("https://scanner.tradingview.com/turkey/scan", json=body, headers=UA, timeout=40)
    save("tv_scan.json", r.text)
    j = r.json()
    types = {}
    for row in j.get("data", []):
        k = f"{row['d'][2]}/{row['d'][3]}/{row['d'][4]}"
        types[k] = types.get(k, 0) + 1
    return {"status": r.status_code, "total": j.get("totalCount"), "types": types, "sample": j.get("data", [])[:3]}

@step("kap")
def _():
    out = {}
    for name, u in {"home": "https://www.kap.org.tr/en/", "bist": "https://www.kap.org.tr/tr/bist-sirketler",
                    "api": "https://www.kap.org.tr/tr/api/disclosures"}.items():
        try:
            r = requests.get(u, headers=UA, timeout=30)
            out[name] = {"status": r.status_code, "len": len(r.text), "head": r.text[:150]}
            save(f"kap_{name}.txt", r.text[:300000])
        except Exception as e:
            out[name] = {"error": repr(e)[:200]}
    return out

@step("borsa_istanbul")
def _():
    out = {}
    for name, u in {"home": "https://www.borsaistanbul.com/en",
                    "vbts": "https://www.borsaistanbul.com/tr/sayfa/3619/vbts-kapsamindaki-tedbirli-paylar"}.items():
        try:
            r = requests.get(u, headers=UA, timeout=30)
            out[name] = {"status": r.status_code, "len": len(r.text)}
            save(f"bi_{name}.html", r.text[:600000])
        except Exception as e:
            out[name] = {"error": repr(e)[:200]}
    return out

@step("stooq")
def _():
    r = requests.get("https://stooq.com/q/d/l/?s=thyao.tr&i=d", headers=UA, timeout=30)
    save("stooq_thyao.csv", r.text[:200000])
    return {"status": r.status_code, "len": len(r.text), "head": r.text[:120]}

report["_when_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
save("report.json", json.dumps(report, indent=1, ensure_ascii=False, default=str))
