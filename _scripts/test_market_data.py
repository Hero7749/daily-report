"""market_data.py 的單元測試（只測純函式與 FinMind 金鑰／重試邏輯，不連網）。

執行：python3 -B -m unittest discover -s _scripts -v
"""
import datetime as dt
import os
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # 不在 repo 裡留下 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_data as md  # noqa: E402


class FormatTests(unittest.TestCase):
    def test_roc_date(self):
        self.assertEqual(md.roc_to_date("115/11/18"), dt.date(2026, 11, 18))

    def test_signed_and_pct(self):
        self.assertEqual(md.fmt_signed(1234.5, 2), "+1,234.50")
        self.assertEqual(md.fmt_signed(-99438, 0), "-99,438")
        self.assertEqual(md.fmt_pct(-0.456), "-0.46%")
        self.assertEqual(md.fmt_pct(0), "+0.00%")

    def test_side_text(self):
        self.assertEqual(md.side_text(-99438), "淨空單 99,438 口")
        self.assertEqual(md.side_text(-99438, -97896, "較前日 "), "淨空單 99,438 口（較前日 +1,542）")
        self.assertEqual(md.side_text(5000, -3000, "5 日 "), "淨多單 5,000 口（5 日 淨部位 +8,000）")

    def test_ex_dividend_day_is_flagged(self):
        class FakeFM:
            def get(self, *a, **k):
                return [{"date": "2026-11-17", "close": 23.89, "spread": -0.36},
                        {"date": "2026-11-18", "close": 23.6, "spread": 0.0}]
        p = md.tw_price(FakeFM(), "00929", dt.date(2026, 11, 19), {})
        self.assertTrue(p["x"])
        self.assertEqual(md.tw_chg(p), "-1.21%（除息/不比價）")

    def test_ma(self):
        self.assertAlmostEqual(md.ma(list(range(1, 26)), 20), sum(range(6, 26)) / 20)
        self.assertIsNone(md.ma([1, 2, 3], 20))


class ScorecardTests(unittest.TestCase):
    def test_light_thresholds(self):
        self.assertEqual(md.light(3)[1], "偏多")
        self.assertEqual(md.light(-3)[1], "保守")
        self.assertEqual(md.light(2)[1], "觀望")
        self.assertEqual(md.light(-2)[1], "觀望")

    def test_tw_scorecard_bearish_case(self):
        taiex = [42000.0] * 19 + [41000.0, 40000.0]   # 收在月線下 >1%
        fut = [-90000] * 5 + [-96000]                 # 5 日淨部位 -6,000（空單增加）
        foreign = [-200.0] * 5                        # 5 日合計 -1,000 億
        margin = [5000.0] * 5 + [5050.0]              # 融資 5 日 +1%
        idx5 = [41500.0] * 5 + [40000.0]              # 指數 5 日 -3.6%
        sox = [9000.0] * 20 + [9300.0]                # 費半站上月線 >1%
        rows = md.tw_scorecard({"taiex": taiex, "fut_net": fut, "foreign": foreign,
                                "margin": margin, "taiex5": idx5, "sox": sox, "night_pct": -0.86})
        scores = [r["score"] for r in rows]
        self.assertEqual(scores, [-1, -1, -1, -1, 1, -1])
        total, emoji, label, reasons = md.summarize(rows)
        self.assertEqual((total, label), (-4, "保守"))
        self.assertIn("外資空單增加", reasons)

    def test_missing_data_scores_zero(self):
        rows = md.us_scorecard({"spx": [], "sox": None, "vix": None, "tnx": [], "nq_pct": None})
        self.assertTrue(all(r["score"] == 0 for r in rows))
        self.assertTrue(all("未取得" in r["text"] for r in rows))

    def test_us_vix_and_yield(self):
        rows = md.us_scorecard({"spx": [7000.0] * 60 + [7400.0], "sox": [9000.0] * 21,
                                "vix": 25.0, "tnx": [4.80, 4.85, 4.90, 4.95, 5.0, 5.0], "nq_pct": 0.1})
        by = {r["name"]: r["score"] for r in rows}
        self.assertEqual(by["VIX"], -1)
        self.assertEqual(by["10 年期美債 5 日變化"], -1)
        self.assertEqual(by["S&P 500 vs 月線"], 1)


class NightSessionTests(unittest.TestCase):
    def test_pick_nearest_traded_contract_on_latest_night(self):
        rows = [
            {"date": "2026-11-18", "contract_date": "202611", "trading_session": "after_market", "close": 39792.0, "spread": -219.0, "spread_per": -0.55},
            {"date": "2026-11-19", "contract_date": "202612", "trading_session": "after_market", "close": 39987.0, "spread": -345.0, "spread_per": -0.86},
            {"date": "2026-11-19", "contract_date": "202706", "trading_session": "after_market", "close": 0.0, "spread": 0.0, "spread_per": 0.0},
            {"date": "2026-11-19", "contract_date": "202611/202612", "trading_session": "after_market", "close": 125.0, "spread": 0.0, "spread_per": 0.0},
            {"date": "2026-11-18", "contract_date": "202612", "trading_session": "position", "close": 40312.0, "spread": 205.0, "spread_per": 0.51},
        ]
        got = md.pick_night(rows)
        self.assertEqual((got["date"], got["contract"], got["close"], got["pct"]), ("2026-11-19", "202612", 39987.0, -0.86))


class ReviewTests(unittest.TestCase):
    POST = """## 🚦 操作觀點

| 代號 | 觀點 | 參考價 | 理由 | 成立條件 | 作廢條件 |
|---|---|---|---|---|---|
| AMD | 👀 留意買點 | 530.14 | 輪動 | 回測月線不破 | 跌破 490 |
| PANW | ⏸ 暫不追高 | 339.92 | 乖離大 | 回落整理 | — |

## 來源
"""

    def test_parse_watch_tables(self):
        got = md.parse_watch_tables(self.POST)
        self.assertEqual(got, [{"symbol": "AMD", "view": "👀 留意買點", "ref": 530.14},
                               {"symbol": "PANW", "view": "⏸ 暫不追高", "ref": 339.92}])

    def test_judge(self):
        self.assertTrue(md.judge("👀 留意買點", 2.0))
        self.assertFalse(md.judge("⏸ 暫不追高", 1.0))
        self.assertTrue(md.judge("🔻 減碼觀望", -0.5))
        self.assertIsNone(md.judge("隨便寫", 1.0))


class FinMindTests(unittest.TestCase):
    def setUp(self):
        self._fetch = md.fetch_json
        self._fail = set(md.FAIL)
        os.environ["MD_NO_SLEEP"] = "1"

    def tearDown(self):
        md.fetch_json = self._fetch
        md.FAIL.clear()
        md.FAIL.update(self._fail)

    def test_every_request_carries_placeholder_key(self):
        seen = []

        def fake(url, headers=None, source="", **kw):
            seen.append(headers or {})
            return {"msg": "success", "status": 200, "data": [{"x": 1}]}

        md.fetch_json = fake
        fm = md.FinMind()
        self.assertEqual(fm.get("TaiwanStockPrice", "2330", "2026-11-01", "2026-11-18"), [{"x": 1}])
        self.assertTrue(seen[0].get("Authorization", "").startswith("Bearer "))
        self.assertEqual(fm.calls[0]["status"], 200)

    def test_402_waits_retries_once_then_disables(self):
        def fake(url, headers=None, source="", **kw):
            raise md.HTTPStatusError(402, {"msg": "Requests reach the upper limit.", "status": 402})

        md.fetch_json = fake
        fm = md.FinMind()
        self.assertIsNone(fm.get("TaiwanStockPrice", "2330"))
        self.assertTrue(fm.disabled)
        self.assertEqual(len(fm.calls), 2)          # 原請求＋重試一次
        self.assertIsNone(fm.get("TaiwanStockPrice", "0050"))
        self.assertEqual(len(fm.calls), 2)          # 停用後不再發請求

    def test_forced_failure_sends_nothing(self):
        md.FAIL.add("finmind")
        calls = []
        md.fetch_json = lambda *a, **k: calls.append(1)
        fm = md.FinMind()
        self.assertIsNone(fm.get("TaiwanStockPrice", "2330"))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
