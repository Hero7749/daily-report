#!/usr/bin/env python3
"""每日投資市場摘要的資料腳本（只用 Python 標準函式庫）。

用法（在 repo 根目錄執行）：
  python3 _scripts/market_data.py             日報：印出可直接貼進報告的表格
  python3 _scripts/market_data.py --days 60   籌碼趨勢表的交易日數（預設 30）
  python3 _scripts/market_data.py --review    週六：本週「個股觀察」成績單

除了更新籌碼歷史檔 assets/data/tw-chips.csv（網頁週線／月線讀這個檔），只印到標準輸出、不寫其他檔案。
設 MD_NO_WRITE=1（或 MD_CACHE）時連歷史檔也不寫。

資料備援順序（前一個拿不到就自動換下一個，全部失敗才標「未取得」）：
  台股收盤／ETF   FinMind → 證交所 STOCK_DAY → Yahoo
  加權指數        證交所 FMTQIK → Yahoo
  三大法人        FinMind → 證交所 BFI82U
  融資餘額        FinMind → 證交所 MI_MARGN
  台指期夜盤      FinMind → 期交所 OpenAPI
  外資期貨未平倉  FinMind → 期交所 OpenAPI
  美股／指數／期貨 Yahoo query1 → Yahoo query2（都失敗時標「未取得」，由排程改用網路搜尋補）
  加密貨幣        CoinGecko（24 小時）→ Yahoo

FinMind 金鑰：每個請求都附上 Authorization 佔位字串，由環境代理換成真正的金鑰；
佔位字串本身不是有效金鑰，所以「請求成功」就代表金鑰確實有帶上，不會有匿名請求。
遇到 401/402/403 或金鑰錯誤：等 60 秒重試一次，仍失敗就停用 FinMind、改走官方來源。

測試用環境變數：
  MD_CACHE=<目錄>   把 HTTP 回應快取到該目錄（開發時避免重複呼叫 API）
  MD_FAIL=finmind,yahoo,twse,taifex,coingecko   模擬來源失效
  MD_NO_SLEEP=1     略過 FinMind 失敗後的 60 秒等待
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")
UA = "Mozilla/5.0 (X11; Linux x86_64) daily-report/1.0"
TIMEOUT = 25
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_PLACEHOLDER = "Bearer placeholder-replaced-by-proxy"  # 不是真金鑰，代理會換掉
FAIL = set(filter(None, os.environ.get("MD_FAIL", "").replace(" ", "").split(",")))
CACHE_DIR = Path(os.environ["MD_CACHE"]) if os.environ.get("MD_CACHE") else None
HERE = Path(__file__).resolve().parent
WEEKDAY = "一二三四五六日"

HIST = HERE.parent / "assets" / "data" / "tw-chips.csv"
HIST_HEAD = ["日期", "加權指數", "外資買賣超（億）", "外資台指期淨空單（口）", "融資餘額（億）"]
TW_ETFS = ["00929", "00919", "00946", "00962"]
OVERVIEW = [("道瓊", "^DJI"), ("S&P 500", "^GSPC"), ("那斯達克", "^IXIC"), ("費半 SOX", "^SOX"),
            ("KOSPI", "^KS11"), ("黃金期貨", "GC=F"), ("原油期貨", "CL=F"), ("BTC", "BTC-USD"),
            ("ETH", "ETH-USD"), ("SOL", "SOL-USD"), ("10 年期美債", "^TNX"), ("NQ 期貨", "NQ=F"),
            ("VIX", "^VIX")]
COINGECKO = {"BTC-USD": "bitcoin", "ETH-USD": "ethereum", "SOL-USD": "solana"}
BULLISH = ("可分批佈局", "留意買點", "續抱觀察")
CAUTIOUS = ("暫不追高", "減碼觀望")


# ── 小工具 ────────────────────────────────────────────────
class SourceError(Exception):
    pass


class HTTPStatusError(Exception):
    def __init__(self, code, body=None):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.body = body


def roc_to_date(s):
    y, m, d = (int(x) for x in s.strip().split("/"))
    return dt.date(y + 1911, m, d)


def num(s):
    """'1,234.5'、'+5.00'、'-' → float 或 None。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = re.sub(r"[,\s$＄%]", "", str(s)).replace("−", "-").replace("X", "")
    try:
        return float(t)
    except ValueError:
        return None


def fmt_num(v, d=2):
    return "未取得" if v is None else f"{v:,.{d}f}"


def fmt_signed(v, d=2):
    return "未取得" if v is None else f"{v:+,.{d}f}"


def fmt_pct(v):
    return "未取得" if v is None else f"{v:+.2f}%"


def fmt_price(v):
    if v is None:
        return "未取得"
    return f"{v:,.0f}" if v >= 1000 and abs(v - round(v)) < 1e-9 else f"{v:,.2f}"


def side_text(now, before=None, span=""):
    """外資期貨淨部位：-99,438 → 「淨空單 99,438 口」；有前值時附上空單（或多單）增減。"""
    side = "淨空單" if now < 0 else "淨多單"
    s = f"{side} {abs(now):,.0f} 口"
    if before is None:
        return s
    if (now < 0) == (before < 0):
        return s + f"（{span}{abs(now) - abs(before):+,.0f}）"
    return s + f"（{span}淨部位 {now - before:+,.0f}）"


def mmdd(d):
    return f"{d.month:02d}/{d.day:02d}"


def mmdd_wd(d):
    return f"{mmdd(d)}（{WEEKDAY[d.weekday()]}）"


def ma(values, n):
    vals = [v for v in (values or []) if v is not None]
    return sum(vals[-n:]) / n if len(vals) >= n else None


def pct_change(a, b):
    return None if a is None or b in (None, 0) else (a / b - 1) * 100


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


# ── HTTP（含快取與失效模擬）──────────────────────────────
def _cache_path(url):
    return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json") if CACHE_DIR else None


def is_cached(url):
    p = _cache_path(url)
    return bool(p and p.exists())


def fetch_json(url, headers=None, source="", retries=1):
    if source in FAIL:
        raise SourceError(f"{source} 模擬失效")
    cp = _cache_path(url)
    if cp and cp.exists():
        return json.loads(cp.read_text("utf-8"))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8-sig"))
            break
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                body = json.loads(raw.decode("utf-8-sig"))
            except ValueError:
                body = {"msg": raw[:200].decode("utf-8", "replace")}
            if attempt < retries and (e.code == 429 or e.code >= 500):
                time.sleep(2)
                continue
            raise HTTPStatusError(e.code, body)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                time.sleep(2)
                continue
            raise SourceError(f"{source} 連線失敗：{e}")
    if cp:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return data


# ── FinMind：一律帶金鑰、記錄每次呼叫 ───────────────────
class FinMind:
    def __init__(self):
        self.calls = []
        self.disabled = False
        self.last_error = ""
        self._retried = False

    def get(self, dataset, data_id=None, start=None, end=None):
        if "finmind" in FAIL:
            self.disabled, self.last_error = True, "模擬失效"
            return None
        if self.disabled:
            return None
        params = {"dataset": dataset}
        if data_id:
            params["data_id"] = data_id
        if start:
            params["start_date"] = str(start)
        if end:
            params["end_date"] = str(end)
        url = FINMIND_URL + "?" + urllib.parse.urlencode(params)
        for attempt in (1, 2):
            cached = is_cached(url)
            status, msg, data = self._request(url)
            self.calls.append({"time": dt.datetime.now(TPE).strftime("%H:%M:%S"), "dataset": dataset,
                               "data_id": data_id or "", "status": status, "rows": len(data or []),
                               "msg": msg if status != 200 else "", "cached": cached})
            if status == 200:
                return data
            auth = status in (401, 402, 403) or "token" in msg.lower() or "違法" in msg or "limit" in msg.lower()
            if auth and attempt == 1 and not self._retried:
                self._retried = True
                if not os.environ.get("MD_NO_SLEEP"):
                    time.sleep(60)
                continue
            self.last_error = f"{status} {msg}".strip()
            if auth:
                self.disabled = True
            return None
        return None

    @staticmethod
    def _request(url):
        try:
            d = fetch_json(url, headers={"Authorization": FINMIND_PLACEHOLDER}, source="finmind", retries=0)
        except HTTPStatusError as e:
            body = e.body if isinstance(e.body, dict) else {}
            return e.code, str(body.get("msg", "")), None
        except Exception as e:  # noqa: BLE001 — 任何錯誤都只記錄，不中斷
            return 0, f"{type(e).__name__}: {e}", None
        if not isinstance(d, dict):
            return 0, "格式錯誤", None
        status = d.get("status", 200)
        if status == 200 and isinstance(d.get("data"), list):
            return 200, str(d.get("msg", "")), d["data"]
        return status, str(d.get("msg", "")), None

    def summary(self):
        real = [c for c in self.calls if not c["cached"]]
        ok = sum(1 for c in real if c["status"] == 200)
        return len(real), ok, len(real) - ok, sum(1 for c in self.calls if c["cached"])


# ── 證交所／期交所 ───────────────────────────────────────
_twse_lock = threading.Lock()
_twse_last = [0.0]


def twse(path, **params):
    """證交所 JSON；請求間隔 2.2 秒，被擋（回非 JSON）時等 6 秒重試一次。"""
    url = _twse_url(path, params)
    for attempt in (1, 2):
        with _twse_lock:
            wait = 2.2 - (time.time() - _twse_last[0])
            if wait > 0 and not is_cached(url):
                time.sleep(wait)
            try:
                d = fetch_json(url, source="twse")
            except ValueError:
                d = None
            finally:
                _twse_last[0] = time.time()
        if d is None and attempt == 1:
            time.sleep(6)
            continue
        break
    if not isinstance(d, dict) or d.get("stat") != "OK":
        raise SourceError(f"證交所 {path}：{(d or {}).get('stat') if isinstance(d, dict) else '被擋或格式錯誤'}")
    return d


def _twse_url(path, params):
    return "https://www.twse.com.tw" + path + "?" + urllib.parse.urlencode({**params, "response": "json"})


def taifex(name):
    return fetch_json(f"https://openapi.taifex.com.tw/v1/{name}", source="taifex")


# ── Yahoo／CoinGecko 行情 ────────────────────────
def yahoo_chart(sym, rng="6mo"):
    last_err = None
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym, safe='')}"
               f"?range={rng}&interval=1d&includePrePost=false")
        try:
            res = fetch_json(url, source="yahoo")["chart"]["result"][0]
            return _parse_yahoo(res)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise SourceError(f"Yahoo {sym}：{last_err}")


def _parse_yahoo(res):
    meta = res.get("meta", {})
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    pts = [(dt.datetime.fromtimestamp(t, tz).date(), c) for t, c in zip(res.get("timestamp") or [], closes) if c is not None]
    price, rmt = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if price is not None and rmt:
        d0 = dt.datetime.fromtimestamp(rmt, tz).date()
        if not pts or d0 > pts[-1][0]:
            pts.append((d0, float(price)))
        elif d0 == pts[-1][0]:
            pts[-1] = (d0, float(price))
    if not pts:
        raise SourceError("沒有價格資料")
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    intraday = bool(rmt and reg and reg.get("start", 0) <= rmt < reg.get("end", 0))
    closes = [c for _, c in pts]
    return {"price": closes[-1], "chg": pct_change(closes[-1], closes[-2]) if len(closes) > 1 else None,
            "date": pts[-1][0], "dates": [d for d, _ in pts], "closes": closes,
            "intraday": intraday, "source": "Yahoo"}


def coingecko_quotes():
    ids = ",".join(COINGECKO.values())
    d = fetch_json(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true",
                   source="coingecko")
    out = {}
    for sym, cid in COINGECKO.items():
        if cid in d:
            out[sym] = {"price": d[cid]["usd"], "chg": d[cid].get("usd_24h_change"), "date": None, "dates": None,
                        "closes": None, "intraday": True, "source": "CoinGecko（24 小時）"}
    return out


def fetch_quotes(symbols):
    quotes, errors = {}, {}

    def one(sym):
        try:
            return sym, yahoo_chart(sym)
        except Exception as e:  # noqa: BLE001
            return sym, e

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for sym, res in ex.map(one, symbols):
            if isinstance(res, Exception):
                errors[sym] = res
            else:
                quotes[sym] = res
    missing = [s for s in symbols if s not in quotes]
    crypto = [s for s in missing if s in COINGECKO]
    if crypto:
        try:
            quotes.update({k: v for k, v in coingecko_quotes().items() if k in crypto})
        except Exception:  # noqa: BLE001
            pass
    return quotes


# ── 台股資料（含備援）────────────────────────────────────
def month_starts(today, n=3):
    m = today.replace(day=1)
    out = [m]
    for _ in range(n - 1):
        m = (m - dt.timedelta(days=1)).replace(day=1)
        out.append(m)
    return sorted(out)


def taiex_series(today, sources):
    s = {}
    try:
        for m in month_starts(today):
            try:
                for r in twse("/rwd/zh/afterTrading/FMTQIK", date=m.strftime("%Y%m%d"))["data"]:
                    s[roc_to_date(r[0])] = num(r[4])
            except Exception:  # noqa: BLE001
                continue
        if not s:
            raise SourceError("FMTQIK 沒有資料")
        sources["加權指數"] = "證交所 FMTQIK"
    except Exception:  # noqa: BLE001
        try:
            q = yahoo_chart("^TWII")
            s = dict(zip(q["dates"], q["closes"]))
            sources["加權指數"] = "Yahoo ^TWII（證交所失敗）"
        except Exception:  # noqa: BLE001
            sources["加權指數"] = "未取得"
    return dict(sorted(s.items()))


def inst_series(fm, start, today, recent_dates, sources):
    rows = fm.get("TaiwanStockTotalInstitutionalInvestors", start=start, end=today)
    out = {}
    if rows:
        for r in rows:
            d = dt.date.fromisoformat(r["date"])
            v = out.setdefault(d, {"外資": 0.0, "投信": 0.0, "自營商": 0.0})
            net = (r.get("buy", 0) - r.get("sell", 0)) / 1e8
            name = r.get("name", "")
            if name in ("Foreign_Investor", "Foreign_Dealer_Self"):
                v["外資"] += net
            elif name == "Investment_Trust":
                v["投信"] += net
            elif name in ("Dealer_self", "Dealer_Hedging"):
                v["自營商"] += net
        sources["三大法人"] = "FinMind"
        return out
    for d in recent_dates:
        try:
            data = twse("/rwd/zh/fund/BFI82U", type="day", dayDate=d.strftime("%Y%m%d"))["data"]
        except Exception:  # noqa: BLE001
            continue
        v = {"外資": 0.0, "投信": 0.0, "自營商": 0.0}
        for name, *_rest, diff in data:
            x = num(diff) / 1e8
            if name.startswith("外資"):
                v["外資"] += x
            elif name.startswith("投信"):
                v["投信"] += x
            elif name.startswith("自營商"):
                v["自營商"] += x
        out[d] = v
    sources["三大法人"] = "證交所 BFI82U（FinMind 失敗，只補近 6 日）" if out else "未取得"
    return out


def margin_series(fm, start, today, recent_dates, sources):
    rows = fm.get("TaiwanStockTotalMarginPurchaseShortSale", start=start, end=today)
    if rows:
        out = {dt.date.fromisoformat(r["date"]): r["TodayBalance"] / 1e8 for r in rows if r.get("name") == "MarginPurchaseMoney"}
        if out:
            sources["融資餘額"] = "FinMind"
            return out
    out = {}
    for d in recent_dates:
        try:
            tables = twse("/rwd/zh/marginTrading/MI_MARGN", date=d.strftime("%Y%m%d"), selectType="MS")["tables"]
        except Exception:  # noqa: BLE001
            continue
        for t in tables:
            for row in t.get("data", []):
                if str(row[0]).startswith("融資金額"):
                    out[d] = num(row[5]) / 1e5  # 仟元 → 億元
    sources["融資餘額"] = "證交所 MI_MARGN（FinMind 失敗，只補近 6 日）" if out else "未取得"
    return out


def fut_oi_series(fm, start, today, sources):
    rows = fm.get("TaiwanFuturesInstitutionalInvestors", "TX", start, today)
    if rows:
        out = {dt.date.fromisoformat(r["date"]): r["long_open_interest_balance_volume"] - r["short_open_interest_balance_volume"]
               for r in rows if r.get("institutional_investors") == "外資"}
        if out:
            sources["外資台指期未平倉"] = "FinMind"
            return out
    try:
        for r in taifex("MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"):
            if r.get("ContractCode") == "臺股期貨" and str(r.get("Item", "")).startswith("外資"):
                sources["外資台指期未平倉"] = "期交所 OpenAPI（FinMind 失敗，只有最新一日）"
                return {dt.datetime.strptime(r["Date"], "%Y%m%d").date(): int(num(r["OpenInterest(Net)"]))}
    except Exception:  # noqa: BLE001
        pass
    sources["外資台指期未平倉"] = "未取得"
    return {}


def pick_night(rows):
    """FinMind TaiwanFuturesDaily → 最新一個夜盤日、最近月份且有成交的合約。"""
    cand = [r for r in rows or [] if r.get("trading_session") == "after_market"
            and re.fullmatch(r"\d{6}", str(r.get("contract_date", ""))) and (r.get("close") or 0) > 0]
    if not cand:
        return None
    last = max(r["date"] for r in cand)
    r = min((r for r in cand if r["date"] == last), key=lambda r: r["contract_date"])
    return {"date": r["date"], "contract": r["contract_date"], "close": float(r["close"]),
            "spread": float(r.get("spread") or 0), "pct": float(r.get("spread_per") or 0)}


def night_session(fm, today, sources):
    got = pick_night(fm.get("TaiwanFuturesDaily", "TX", today - dt.timedelta(days=7), today))
    if got:
        sources["台指期夜盤"] = "FinMind"
        return got
    try:
        cand = [r for r in taifex("DailyMarketReportFut") if r.get("Contract") == "TX" and r.get("TradingSession") == "盤後"
                and re.fullmatch(r"\d{6}", str(r.get("ContractMonth(Week)", ""))) and (num(r.get("Last")) or 0) > 0]
        if cand:
            last = max(r["Date"] for r in cand)
            r = min((r for r in cand if r["Date"] == last), key=lambda r: r["ContractMonth(Week)"])
            sources["台指期夜盤"] = "期交所 OpenAPI（FinMind 失敗，可能是前一個夜盤）"
            d = dt.datetime.strptime(r["Date"], "%Y%m%d").date().isoformat()
            return {"date": d, "contract": r["ContractMonth(Week)"], "close": num(r["Last"]),
                    "spread": num(r["Change"]) or 0.0, "pct": num(r["%"]) or 0.0}
    except Exception:  # noqa: BLE001
        pass
    sources["台指期夜盤"] = "未取得"
    return None


def tw_price(fm, sid, today, sources):
    """回傳 {date, close, chg, x}；x=True 表示證交所標「X 不比價」（多半是除權息），chg 為未還原的價差。"""
    rows = fm.get("TaiwanStockPrice", sid, today - dt.timedelta(days=15), today)
    if rows:
        r = rows[-1]
        close, spread = float(r["close"]), float(r.get("spread") or 0)
        prev = float(rows[-2]["close"]) if len(rows) > 1 else None
        sources.setdefault("台股收盤", set()).add("FinMind")
        x = abs(spread) < 1e-9 and prev is not None and abs(close - prev) > 1e-9
        return {"date": dt.date.fromisoformat(r["date"]), "close": close,
                "chg": pct_change(close, prev) if x else pct_change(close, close - spread), "x": x}
    try:
        pts = []
        for m in month_starts(today, 2):
            try:
                pts += [(roc_to_date(x[0]), num(x[6]), str(x[7]).startswith("X"))
                        for x in twse("/rwd/zh/afterTrading/STOCK_DAY", date=m.strftime("%Y%m%d"), stockNo=sid)["data"]]
            except Exception:  # noqa: BLE001
                continue
        pts = [p for p in pts if p[1]]
        if len(pts) >= 2:
            sources.setdefault("台股收盤", set()).add("證交所 STOCK_DAY")
            return {"date": pts[-1][0], "close": pts[-1][1], "chg": pct_change(pts[-1][1], pts[-2][1]), "x": pts[-1][2]}
    except Exception:  # noqa: BLE001
        pass
    try:
        q = yahoo_chart(f"{sid}.TW", "1mo")
        sources.setdefault("台股收盤", set()).add("Yahoo")
        return {"date": q["date"], "close": q["price"], "chg": q["chg"], "x": False, "intraday": q["intraday"]}
    except Exception:  # noqa: BLE001
        return None


def tw_chg(p):
    if not p:
        return "—"
    return fmt_pct(p["chg"]) + ("（除息/不比價）" if p.get("x") else "") + ("（Yahoo 盤中）" if p.get("intraday") else "")


def etf_dividend(fm, sid, today):
    rows = fm.get("TaiwanStockDividend", sid, today - dt.timedelta(days=400), today)
    if rows is None:
        return "未取得", None
    recs = []
    for r in rows:
        ex = str(r.get("CashExDividendTradingDate") or "")
        amt = (r.get("CashEarningsDistribution") or 0) + (r.get("CashStatutorySurplus") or 0)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ex) and amt:
            pay = str(r.get("CashDividendPaymentDate") or "")
            recs.append((dt.date.fromisoformat(ex), round(amt, 4), pay if re.fullmatch(r"\d{4}-\d{2}-\d{2}", pay) else ""))
    if not recs:
        return "—", None
    recs.sort()
    upcoming = [x for x in recs if x[0] >= today]
    ex, amt, pay = upcoming[0] if upcoming else recs[-1]
    pay_s = f"，{mmdd(dt.date.fromisoformat(pay))} 發放" if pay else ""
    amt_s = f"{amt:g}"
    if upcoming:
        return f"📅 {mmdd(ex)} 除息 {amt_s} 元（即將）{pay_s}", (ex, amt_s)
    return f"{mmdd(ex)} 除息 {amt_s} 元{pay_s}", None


def month_revenue(fm, today):
    rows = fm.get("TaiwanStockMonthRevenue", "2330", today - dt.timedelta(days=430), today)
    if not rows:
        return None
    by = {(r["revenue_year"], r["revenue_month"]): r for r in rows}
    last = max(by)
    rev = by[last]["revenue"] / 1e8
    y, m = last
    prev = by.get((y, m - 1) if m > 1 else (y - 1, 12))
    yoy = by.get((y - 1, m))
    parts = [f"台積電 {y} 年 {m} 月營收 {rev:,.0f} 億元"]
    if prev:
        parts.append(f"月增 {pct_change(rev, prev['revenue'] / 1e8):+.1f}%")
    if yoy:
        parts.append(f"年增 {pct_change(rev, yoy['revenue'] / 1e8):+.1f}%")
    return "，".join(parts)


# ── 燈號（固定規則）───────────────────────────────────────
def light(total):
    # 用天氣圖示，避免紅綠和台股「紅漲綠跌」打架
    if total >= 3:
        return "☀️", "偏多"
    if total <= -3:
        return "🌧️", "保守"
    return "⛅", "觀望"


def band_score(diff, band):
    if diff is None:
        return 0
    return 1 if diff > band else -1 if diff < -band else 0


def _ind(name, text, score, pos, neg):
    return {"name": name, "text": text, "score": score, "reason": pos if score > 0 else neg if score < 0 else ""}


def _vs_ma(name, closes, n, label, pos, neg):
    closes = [c for c in (closes or []) if c is not None]
    m = ma(closes, n)
    if m is None:
        return _ind(name, "未取得", 0, pos, neg)
    diff = pct_change(closes[-1], m)
    return _ind(name, f"{fmt_num(closes[-1])}／{label} {fmt_num(m)}（{fmt_pct(diff)}）", band_score(diff, 1.0), pos, neg)


def tw_scorecard(d):
    rows = [_vs_ma("加權指數 vs 月線", d.get("taiex"), 20, "月線", "指數站上月線", "指數在月線下")]

    fut = [x for x in (d.get("fut_net") or []) if x is not None]
    if len(fut) >= 6:
        delta = fut[-1] - fut[-6]  # 淨部位變化：正＝偏多（空單回補）
        sc = 1 if delta >= 5000 else -1 if delta <= -5000 else 0
        rows.append(_ind("外資台指期淨部位 5 日變化", side_text(fut[-1], fut[-6], "5 日 "), sc, "外資空單回補", "外資空單增加"))
    else:
        rows.append(_ind("外資台指期淨部位 5 日變化", "未取得", 0, "", ""))

    flow = [x for x in (d.get("foreign") or []) if x is not None]
    if len(flow) >= 5:
        s5 = sum(flow[-5:])
        rows.append(_ind("外資現貨 5 日累計", f"{s5:+,.2f} 億", 1 if s5 >= 500 else -1 if s5 <= -500 else 0, "外資 5 日買超", "外資 5 日賣超"))
    else:
        rows.append(_ind("外資現貨 5 日累計", "未取得", 0, "", ""))

    mg = [x for x in (d.get("margin") or []) if x is not None]
    ix = [x for x in (d.get("taiex5") or d.get("taiex") or []) if x is not None]
    if len(mg) >= 6 and len(ix) >= 6:
        m5, i5 = pct_change(mg[-1], mg[-6]), pct_change(ix[-1], ix[-6])
        sc = -1 if (i5 < -1 and m5 > 0) else 1 if (i5 > 1 and m5 < 0) else 0
        rows.append(_ind("融資 vs 指數（5 日）", f"融資 {fmt_pct(m5)}／指數 {fmt_pct(i5)}", sc, "指數漲、融資減", "指數跌、融資增"))
    else:
        rows.append(_ind("融資 vs 指數（5 日）", "未取得", 0, "", ""))

    rows.append(_vs_ma("費半 vs 月線", d.get("sox"), 20, "月線", "費半站上月線", "費半在月線下"))

    np_ = d.get("night_pct")
    if np_ is None:
        rows.append(_ind("台指期夜盤", "未取得", 0, "", ""))
    else:
        rows.append(_ind("台指期夜盤", fmt_pct(np_), 1 if np_ >= 0.5 else -1 if np_ <= -0.5 else 0, "夜盤上漲", "夜盤下跌"))
    return rows


def us_scorecard(d):
    rows = [_vs_ma("S&P 500 vs 月線", d.get("spx"), 20, "月線", "S&P 站上月線", "S&P 在月線下"),
            _vs_ma("S&P 500 vs 季線", d.get("spx"), 60, "季線", "S&P 站上季線", "S&P 在季線下"),
            _vs_ma("費半 vs 月線", d.get("sox"), 20, "月線", "費半站上月線", "費半在月線下")]
    vix = d.get("vix")
    if vix is None:
        rows.append(_ind("VIX", "未取得", 0, "", ""))
    else:
        rows.append(_ind("VIX", f"{vix:.2f}", 1 if vix < 17 else -1 if vix > 22 else 0, "VIX 低檔", "VIX 偏高"))
    tnx = [x for x in (d.get("tnx") or []) if x is not None]
    if len(tnx) >= 6:
        bp = (tnx[-1] - tnx[-6]) * 100
        rows.append(_ind("10 年期美債 5 日變化", f"{tnx[-1]:.2f}%（5 日 {bp:+.0f} bp）",
                         1 if bp <= -15 else -1 if bp >= 15 else 0, "美債殖利率回落", "美債殖利率走升"))
    else:
        rows.append(_ind("10 年期美債 5 日變化", "未取得", 0, "", ""))
    nq = d.get("nq_pct")
    if nq is None:
        rows.append(_ind("NQ 期貨", "未取得", 0, "", ""))
    else:
        rows.append(_ind("NQ 期貨", fmt_pct(nq), 1 if nq >= 0.5 else -1 if nq <= -0.5 else 0, "NQ 期貨走高", "NQ 期貨走低"))
    return rows


def summarize(rows):
    total = sum(r["score"] for r in rows)
    emoji, label = light(total)
    pos = [r["reason"] for r in rows if r["score"] > 0]
    neg = [r["reason"] for r in rows if r["score"] < 0]
    if label == "偏多":
        reasons = "、".join(pos[:3])
    elif label == "保守":
        reasons = "、".join(neg[:3])
    else:
        bits = ([f"多：{'、'.join(pos[:2])}"] if pos else []) + ([f"空：{'、'.join(neg[:2])}"] if neg else [])
        reasons = "；".join(bits) or "訊號中性"
    return total, emoji, label, reasons


# ── 技術訊號（挑個股觀察用）──────────────────────────────
def tech(q):
    c = q.get("closes") or []
    if len(c) < 21:
        return None
    m20, m20p = ma(c, 20), ma(c[:-1], 20)
    sig = []
    if c[-2] < m20p and c[-1] > m20:
        sig.append("站上月線")
    if c[-2] > m20p and c[-1] < m20:
        sig.append("跌破月線")
    if c[-1] >= max(c[-20:]):
        sig.append("20 日新高")
    if c[-1] <= min(c[-20:]):
        sig.append("20 日新低")
    if q.get("chg") is not None and abs(q["chg"]) >= 5:
        sig.append("單日 ±5%")
    return {"ma20": m20, "ma60": ma(c, 60), "vs20": pct_change(c[-1], m20), "lo20": min(c[-20:]), "hi20": max(c[-20:]), "sig": sig}


# ── 成績單（週六）────────────────────────────────────────
def parse_watch_tables(text):
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if lines[i].lstrip().startswith("|") and "代號" in cells and "觀點" in cells and "參考價" in cells:
            ci, vi, ri = cells.index("代號"), cells.index("觀點"), cells.index("參考價")
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if len(row) > max(ci, vi, ri) and num(row[ri]) is not None:
                    out.append({"symbol": row[ci].split()[0], "view": row[vi], "ref": num(row[ri])})
                i += 1
            continue
        i += 1
    return out


def judge(view, ret):
    if any(k in view for k in BULLISH):
        return ret > 0
    if any(k in view for k in CAUTIOUS):
        return ret <= 0
    return None


def run_review():
    today = dt.datetime.now(TPE).date()
    start = today - dt.timedelta(days=today.weekday())
    entries = []
    for p in sorted((HERE.parent / "_posts").glob("*-market.md")):
        try:
            d = dt.date.fromisoformat(p.name[:10])
        except ValueError:
            continue
        if start <= d <= today:
            entries += [{**e, "date": d} for e in parse_watch_tables(p.read_text("utf-8"))]
    print(f"## 📋 本週觀點成績單（{mmdd(start)}–{mmdd(today)}）\n")
    if not entries:
        print("本週沒有個股觀察紀錄。")
        return 0
    syms = sorted({(e["symbol"] + ".TW") if e["symbol"].isdigit() else e["symbol"] for e in entries})
    quotes = fetch_quotes(syms)
    rows, hits, judged = [], 0, 0
    for e in entries:
        q = quotes.get((e["symbol"] + ".TW") if e["symbol"].isdigit() else e["symbol"])
        last = q["price"] if q else None
        ret = pct_change(last, e["ref"])
        ok = judge(e["view"], ret) if ret is not None else None
        if ok is not None:
            judged += 1
            hits += ok
        rows.append([mmdd(e["date"]), e["symbol"], e["view"], fmt_price(e["ref"]), fmt_price(last), fmt_pct(ret),
                     "✅" if ok else "❌" if ok is False else "—"])
    print(md_table(["日期", "代號", "觀點", "參考價", "最新價", "漲跌", "結果"], rows))
    if judged:
        print(f"\n- 命中率 {hits}/{judged}（{hits / judged * 100:.0f}%）；命中定義：看多類（可分批佈局、留意買點、續抱觀察）上漲＝命中，保守類（暫不追高、減碼觀望）下跌或持平＝命中")
    return 0


# ── 籌碼歷史檔（每日一列，網頁週線／月線用）──────────
def load_history(path=None):
    """→ {date: [加權指數, 外資買賣超(億), 外資淨空單(口), 融資餘額(億)]}"""
    path = path or HIST
    out = {}
    if not path.exists():
        return out
    for line in path.read_text("utf-8").splitlines()[1:]:
        cells = line.split(",")
        if len(cells) == 5:
            try:
                out[dt.date.fromisoformat(cells[0])] = [num(c) if c else None for c in cells[1:]]
            except ValueError:
                continue
    return out


def merge_history(hist, taiex, inst, fut, margin):
    """把這次抓到的數字寫進 hist（只覆蓋有值的欄位），回傳新增或變動的列數。"""
    changed = 0
    for d in set(taiex) | set(inst) | set(fut) | set(margin):
        row = list(hist.get(d, [None] * 4))
        new = [taiex.get(d), inst.get(d, {}).get("外資"), (-fut[d] if d in fut else None), margin.get(d)]
        for i, v in enumerate(new):
            if v is not None:
                row[i] = round(float(v), 0 if i == 2 else 2)
        if row != hist.get(d):
            hist[d] = row
            changed += 1
    return changed


def save_history(hist, path=None):
    path = path or HIST
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(HIST_HEAD)]
    for d in sorted(hist):
        r = hist[d]
        cells = ["" if v is None else (f"{v:.0f}" if i == 2 else f"{v:.2f}") for i, v in enumerate(r)]
        lines.append(d.isoformat() + "," + ",".join(cells))
    path.write_text("\n".join(lines) + "\n", "utf-8")


# ── 日報 ─────────────────────────────────────────────────
def load_watchlist():
    d = json.loads((HERE / "watchlist.json").read_text("utf-8"))
    sector_of = {}
    for sec, syms in d["sectors"].items():
        for s in syms:
            sector_of.setdefault(s, sec)
    return d["core"], d["sectors"], sector_of


def run_daily(days):
    now = dt.datetime.now(TPE)
    today = now.date()
    sources = {}
    fm = FinMind()
    core, sectors, sector_of = load_watchlist()
    hist = load_history()
    start = today - dt.timedelta(days=40 if len(hist) >= 120 else 110)  # 有歷史檔就只抓近期

    # 1) 台股（先跑：FinMind 若要等 60 秒重試，越早發現越好）
    taiex = taiex_series(today, sources)
    tdates = list(taiex)
    recent = tdates[-6:]
    inst = inst_series(fm, start, today, recent, sources)
    margin = margin_series(fm, start, today, recent, sources)
    fut = fut_oi_series(fm, start, today, sources)
    night = night_session(fm, today, sources)
    tsmc = tw_price(fm, "2330", today, sources)
    etf_px = {sid: tw_price(fm, sid, today, sources) for sid in TW_ETFS}
    etf_div = {sid: etf_dividend(fm, sid, today) for sid in TW_ETFS}
    revenue = month_revenue(fm, today)
    news = fm.get("TaiwanStockNews", "2330", (tdates[-1] if tdates else today)) or []  # 這個資料集只能給 start_date

    # 歷史檔：補進這次抓到的數字；趨勢表與燈號都改用歷史檔（FinMind 失敗時仍有完整歷史）
    hist_changed = merge_history(hist, taiex, inst, fut, margin)
    if not (os.environ.get("MD_NO_WRITE") or CACHE_DIR):
        save_history(hist)
    hd = sorted(d for d in hist if hist[d][0] is not None)
    h_taiex = {d: hist[d][0] for d in hd}
    h_flow = {d: hist[d][1] for d in hd if hist[d][1] is not None}
    h_fut = {d: -hist[d][2] for d in hd if hist[d][2] is not None}
    h_margin = {d: hist[d][3] for d in hd if hist[d][3] is not None}

    # 2) 美股與自選股
    symbols = list(dict.fromkeys([s for _, s in OVERVIEW] + [s for syms in sectors.values() for s in syms] + core))
    quotes = fetch_quotes(symbols)
    q = quotes.get

    us_date = q("^GSPC")["date"] if q("^GSPC") and q("^GSPC").get("date") else None
    print(f"# market_data.py｜{now:%Y-%m-%d %H:%M} 台灣時間（以下「## 」區塊可直接貼進報告；標【參考】的不要貼）\n")

    # 市場總覽：期貨取美國交易日那根日線（收盤）；NQ 期貨另給即時；加密貨幣用 24 小時漲跌
    def bar_on(x, d):
        if not x or not x.get("dates") or d not in x["dates"]:
            return None, None
        i = x["dates"].index(d)
        return x["closes"][i], (pct_change(x["closes"][i], x["closes"][i - 1]) if i > 0 else None)

    nq = q("NQ=F")
    nq_ref = bar_on(nq, us_date)[0] if us_date else None
    nq_pct = pct_change(nq["price"], nq_ref) if nq and nq_ref else (nq["chg"] if nq else None)
    try:
        cg = coingecko_quotes()
    except Exception:  # noqa: BLE001
        cg = {}
    title = f"（{mmdd(us_date)} 週{WEEKDAY[us_date.weekday()]}收盤）" if us_date else ""
    rows = []
    for label, sym in OVERVIEW:
        x = q(sym) or cg.get(sym)
        if not x:
            rows.append([label, "未取得", "—"])
            continue
        name, price, chg = label, x["price"], x["chg"]
        if sym == "^KS11" and x.get("date"):
            name = f"KOSPI（{mmdd(x['date'])}{' 盤中' if x['intraday'] else ''}）"
        elif sym in ("GC=F", "CL=F") and us_date:
            p, c = bar_on(x, us_date)
            if p is not None:
                price, chg = p, c
        elif sym == "NQ=F":
            name, chg = "NQ 期貨（即時）", nq_pct
        elif sym in cg:
            name, price, chg = f"{label}（24h）", cg[sym]["price"], cg[sym]["chg"]
        val = f"{price:.2f}%" if sym == "^TNX" else fmt_num(price)
        rows.append([name, val, fmt_pct(chg)])
    print(f"## 📊 市場總覽{title}\n\n" + md_table(["指標", "收盤", "漲跌幅"], rows) + "\n")

    # 核心個股
    rows = [[s, fmt_price(q(s)["price"]) if q(s) else "未取得", fmt_pct(q(s)["chg"]) if q(s) else "—", "—"] for s in core]
    print("## 🎯 核心個股\n\n" + md_table(["代號", "收盤", "漲跌幅", "重點"], rows) + "\n")

    # 自選股雷達
    movers = sorted([(s, quotes[s]) for s in sector_of if s in quotes and quotes[s]["chg"] is not None and abs(quotes[s]["chg"]) >= 5],
                    key=lambda t: -abs(t[1]["chg"]))
    print("## ⭐ 自選股雷達\n")
    if movers:
        print(md_table(["產業", "代號", "漲跌幅", "原因"], [[sector_of[s], s, fmt_pct(x["chg"]), "原因未取得"] for s, x in movers[:10]]))
        if len(movers) > 10:
            print(f"\n【參考】另有 {len(movers) - 10} 檔 ≥ ±5%：" + "、".join(f"{s} {fmt_pct(x['chg'])}" for s, x in movers[10:]))
    else:
        print("（今日沒有漲跌 ≥ ±5% 的自選股）")
    avg = []
    for sec, syms in sectors.items():
        ch = [quotes[s]["chg"] for s in syms if s in quotes and quotes[s]["chg"] is not None]
        if ch:
            best = max((s for s in syms if s in quotes and quotes[s]["chg"] is not None), key=lambda s: quotes[s]["chg"])
            worst = min((s for s in syms if s in quotes and quotes[s]["chg"] is not None), key=lambda s: quotes[s]["chg"])
            avg.append((sum(ch) / len(ch), sec, best, worst))
    if avg:
        avg.sort()
        hi, lo = avg[-1], avg[0]
        print(f"\n- 最強：{hi[1]}（平均 {fmt_pct(hi[0])}，{hi[2]} {fmt_pct(quotes[hi[2]]['chg'])}）；"
              f"最弱：{lo[1]}（平均 {fmt_pct(lo[0])}，{lo[3]} {fmt_pct(quotes[lo[3]]['chg'])}）\n")

    # 台股
    tw_date = tdates[-1] if tdates else None
    rows = []
    if tw_date:
        prev = taiex[tdates[-2]] if len(tdates) > 1 else None
        rows.append(["加權指數", fmt_num(taiex[tw_date]), fmt_pct(pct_change(taiex[tw_date], prev))])
    else:
        rows.append(["加權指數", "未取得", "—"])
    rows.append(["台積電 2330", fmt_price(tsmc["close"]) if tsmc else "未取得", tw_chg(tsmc)])
    li = max(inst) if inst else None
    for k in ("外資", "投信", "自營商"):
        lbl = k if (li == tw_date or li is None) else f"{k}（{mmdd(li)}）"
        rows.append([lbl, f"{inst[li][k]:+,.2f} 億" if li else "未取得", "—"])
    if night:
        nd = dt.date.fromisoformat(night["date"])
        rows.append([f"台指期夜盤（{int(night['contract'][4:])} 月，{mmdd(nd)} 盤後時段）", fmt_num(night["close"], 0), fmt_pct(night["pct"])])
    else:
        rows.append(["台指期夜盤", "未取得", "—"])
    if fut:
        fd = sorted(fut)
        v = fut[fd[-1]]
        side = "淨空單" if v < 0 else "淨多單"
        delta = "—"
        if len(fd) > 1:
            p = fut[fd[-2]]
            delta = f"較前日 {abs(v) - abs(p):+,.0f}" if (v < 0) == (p < 0) else f"淨部位 {v - p:+,.0f}"
        rows.append([f"外資台指期{side}" + ("" if fd[-1] == tw_date else f"（{mmdd(fd[-1])}）"), f"{abs(v):,.0f} 口", delta])
    else:
        rows.append(["外資台指期淨部位", "未取得", "—"])
    if margin:
        md_ = sorted(margin)
        v = margin[md_[-1]]
        delta = f"較前日 {v - margin[md_[-2]]:+,.2f} 億" if len(md_) > 1 else "—"
        rows.append(["融資餘額" + ("" if md_[-1] == tw_date else f"（{mmdd(md_[-1])}）"), f"{v:,.2f} 億", delta])
    else:
        rows.append(["融資餘額", "未取得", "—"])
    print(f"## 🇹🇼 台股{('（' + mmdd(tw_date) + ' 週' + WEEKDAY[tw_date.weekday()] + '）') if tw_date else ''}\n\n"
          + md_table(["項目", "數值", "漲跌"], rows) + "\n")

    # 高股息 ETF
    rows = []
    for sid in TW_ETFS:
        p = etf_px.get(sid)
        rows.append([sid, fmt_price(p["close"]) if p else "未取得", tw_chg(p), etf_div[sid][0]])
    print("## 💰 高股息 ETF\n\n" + md_table(["代號", "收盤", "漲跌幅", "除息／配息"], rows) + "\n")
    upcoming = [(sid, v[1]) for sid, v in etf_div.items() if v[1] and (v[1][0] - today).days <= 7]
    if upcoming:
        print("【參考】週曆 💰 事件：" + "、".join(f"💰 {sid} 除息 {a} 元（{mmdd(d)}）" for sid, (d, a) in upcoming) + "\n")

    # 籌碼趨勢
    tr_dates = hd[-days:]
    rows = []
    for d in tr_dates:
        f_ = h_flow.get(d)
        rows.append([mmdd(d), fmt_num(h_taiex.get(d)), fmt_signed(f_) if f_ is not None else "—",
                     fmt_num(-h_fut[d], 0) if d in h_fut else "—", fmt_num(h_margin[d]) if d in h_margin else "—"])
    print(f"## 📉 籌碼趨勢（近 {len(tr_dates)} 個交易日）\n\n"
          + md_table(["📉 日期", "加權指數", "外資買賣超（億）", "外資台指期淨空單（口）", "融資餘額（億）"], rows) + "\n")

    # 燈號
    def closes(sym):
        return q(sym)["closes"] if q(sym) and q(sym).get("closes") else None

    mdates = sorted(h_margin)[-6:]
    tw_rows = tw_scorecard({"taiex": [h_taiex[d] for d in hd], "fut_net": [h_fut[d] for d in sorted(h_fut)],
                            "foreign": [h_flow[d] for d in sorted(h_flow)], "margin": [h_margin[d] for d in mdates],
                            "taiex5": [h_taiex[d] for d in mdates if d in h_taiex], "sox": closes("^SOX"),
                            "night_pct": night["pct"] if night else None})
    us_rows = us_scorecard({"spx": closes("^GSPC"), "sox": closes("^SOX"), "vix": q("^VIX")["price"] if q("^VIX") else None,
                            "tnx": closes("^TNX"), "nq_pct": nq_pct})
    summ = []
    detail = []
    for flag, rows_ in (("🇹🇼 台股", tw_rows), ("🇺🇸 美股", us_rows)):
        total, emoji, label, reasons = summarize(rows_)
        summ.append([flag, f"{emoji} {label}", f"{total:+d}" if total else "0", reasons])
        detail += [[flag.split()[0], r["name"], r["text"], f"{r['score']:+d}" if r["score"] else "0"] for r in rows_]
    print("## 🧭 操作觀點\n\n" + md_table(["市場", "大盤天氣", "分數", "主因"], summ) + "\n")
    print('<details markdown="1"><summary>天氣怎麼算（6 項指標各 -1／0／+1；合計 ≥ +3 偏多、≤ -3 保守）</summary>\n\n'
          + md_table(["市場", "指標", "現況", "分數"], detail) + "\n\n</details>\n")

    # 技術訊號
    rows = []
    for s in list(dict.fromkeys(core + list(sector_of))):
        x = q(s)
        t = tech(x) if x else None
        if not t or (s not in core and not t["sig"]):
            continue
        rows.append([s, sector_of.get(s, ""), fmt_price(x["price"]), fmt_pct(x["chg"]), fmt_num(t["ma20"]),
                     fmt_pct(t["vs20"]), fmt_num(t["lo20"]), "、".join(t["sig"]) or "—"])
    print("## 【參考】技術訊號（挑「個股觀察」用，不要整張貼進報告）\n\n"
          + md_table(["代號", "產業", "收盤", "漲跌", "月線", "距月線", "20 日低", "訊號"], rows) + "\n")

    # 台積電月營收／新聞
    print("## 【參考】台積電月營收與新聞（FinMind）\n")
    print(f"- {revenue}" if revenue else "- 月營收未取得")
    for n in news[:5]:
        print(f"- {n.get('date', '')[:10]} {n.get('title', '')}（{n.get('source', '')}）{n.get('link', '')}")
    print()

    # 來源與 API 紀錄
    real, ok, bad, cached = fm.summary()
    tw_src = sources.get("台股收盤")
    sources["台股收盤"] = "、".join(sorted(tw_src)) if tw_src else "未取得"
    us_src = sorted({x["source"] for x in quotes.values()})
    print("## 【參考】資料來源與 API 紀錄\n")
    for k in ("加權指數", "台股收盤", "三大法人", "融資餘額", "外資台指期未平倉", "台指期夜盤"):
        print(f"- {k}：{sources.get(k, '未取得')}")
    print(f"- 籌碼歷史檔 assets/data/tw-chips.csv：共 {len(hist)} 列，本次新增或更新 {hist_changed} 列"
          + ("（MD_NO_WRITE／快取模式，未寫入）" if (os.environ.get("MD_NO_WRITE") or CACHE_DIR) else ""))
    print(f"- 美股／指數／期貨／加密：{'、'.join(us_src) or '未取得'}；未取得：{', '.join(s for s in symbols if s not in quotes) or '無'}")
    state = f"；停用原因：{fm.last_error}" if fm.disabled else ""
    print(f"- FinMind 實際呼叫 {real} 次（成功 {ok}、失敗 {bad}）{f'、快取 {cached} 次' if cached else ''}{state}；"
          "每次都附金鑰佔位字串、由代理換成你的金鑰，沒有匿名請求")
    for c in fm.calls:
        tag = "快取" if c["cached"] else str(c["status"])
        extra = f"（{c['rows']} 筆）" if c["status"] == 200 else f"（{c['msg']}）"
        print(f"  - {c['time']} {c['dataset']} {c['data_id']} → {tag}{extra}")
    fallback = [k for k in ("三大法人", "融資餘額", "外資台指期未平倉", "台指期夜盤", "加權指數") if "失敗" in sources.get(k, "")]
    line = f"FinMind {real} 次（成功 {ok}）" + (f"｜備援：{'、'.join(fallback)}改用官方" if fallback else "")
    print(f"\n【參考】報告來源行可寫：{line}")
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="每日投資市場摘要資料腳本")
    ap.add_argument("--days", type=int, default=30, help="籌碼趨勢表交易日數（預設 30）")
    ap.add_argument("--review", action="store_true", help="週六：本週個股觀察成績單")
    args = ap.parse_args(argv)
    return run_review() if args.review else run_daily(max(5, args.days))


if __name__ == "__main__":
    sys.exit(main())
