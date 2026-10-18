"""End-to-end smoke test for the 糊涂账 API (uses a throwaway data dir)."""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date as _date
from pathlib import Path
from urllib.parse import quote

os.environ["HUTUZHANG_HOME"] = tempfile.mkdtemp(prefix="hutuzhang-test-")
os.environ["HUTUZHANG_EXPORT_DIR"] = tempfile.mkdtemp(prefix="hutuzhang-export-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from openpyxl import Workbook  # noqa: E402

from app.main import app  # noqa: E402
from app import rules  # noqa: E402

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"
passed = 0
failed = 0


def check(cond, label, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {extra}")


def req(method: str, path: str, body=None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": "application/octet-stream" if raw is not None else "application/json"}
    r = urllib.request.Request(BASE + quote(path, safe="/?=&"), data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r) as resp:
            payload = resp.read()
            try:
                return resp.status, json.loads(payload)
            except json.JSONDecodeError:
                return resp.status, payload.decode()
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, payload.decode()


def xlsx_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "账单"
    header = ["日期", "收支类型", "金额", "类别", "子类", "账户", "账本", "报销账户",
              "报销金额", "退款金额", "备注", "标签", "多币种", "地址", "创建用户",
              "优惠", "其他", "附件1", "附件2", "附件3"]
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def alipay_bytes(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["------------------------------------------------------------------------------------"])
    w.writerow(["导出信息："])
    w.writerow([])
    w.writerow(["起始时间：[2026-09-17 00:00:00]    终止时间：[2026-10-17 23:59:59]"])
    w.writerow([])
    w.writerow(["交易时间", "交易分类", "交易对方", "对方账号", "商品说明", "收/支", "金额",
                "收/付款方式", "交易状态", "交易订单号", "商家订单号", "备注"])
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("gb18030")


def wechat_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["微信支付账单明细"])
    ws.append([])
    ws.append(["起始时间：[2026-09-18 00:00:00] 终止时间：[2026-10-18 00:49:36]"])
    ws.append(["导出类型：[全部账单]"])
    ws.append([])
    ws.append(["共4笔记录"])
    ws.append(["收入：1笔 100.00元"])
    ws.append(["支出：3笔 257.00元"])
    ws.append([])
    ws.append(["----------------------微信支付账单明细列表--------------------"])
    ws.append(["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)", "支付方式",
               "当前状态", "交易单号", "商户单号", "备注"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main():
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    try:
        print("\n[health & meta]")
        code, d = req("GET", "/api/health")
        check(code == 200 and d["ok"], "health")
        code, meta = req("GET", "/api/meta")
        check(code == 200 and len(meta["categories"]) > 10, "meta has categories")
        check(len(meta["accounts"]) >= 5, "meta has accounts")

        code, page = req("GET", "/")
        check(code == 200 and "糊涂账" in page, "index served")

        print("\n[entries]")
        code, e1 = req("POST", "/api/entries", {
            "occurred_at": "2026-10-05", "amount": 32.5, "note": "午饭",
            "category_id": meta["categories"][0]["id"], "account_id": meta["accounts"][0]["id"],
        })
        check(code == 201 and e1["amount"] == 32.5, "create personal entry")
        check(e1["status"] == "personal", "personal status")

        code, e2 = req("POST", "/api/entries", {
            "occurred_at": "2026-10-06 09:00", "amount": 120, "note": "客户打车",
            "usage": "work", "project": "客户A",
            "category_id": meta["categories"][0]["id"],
        })
        check(code == 201 and e2["status"] == "work", "work entry flagged")

        code, e3 = req("POST", "/api/entries", {
            "occurred_at": "2026-10-07 19:30", "amount": 80, "note": "加班餐",
            "usage": "work", "project": "客户A",
        })
        check(code == 201, "second work entry")

        code, bad = req("POST", "/api/entries", {"occurred_at": "2026-10-07", "amount": 0})
        check(code == 422, "rejects zero amount")

        print("\n[overview]")
        code, ov = req("GET", "/api/overview?month=2026-10")
        check(code == 200, "overview ok")
        check(ov["personal_cents"] == 3250, "personal excludes work", ov["personal_cents"])
        check(ov["work_cents"] == 20000, "work total", ov["work_cents"])
        check(ov["work_count"] == 2, "work count", ov["work_count"])
        check(ov["personal_count"] == 1, "personal count", ov["personal_count"])
        check(len(ov["trend"]) == 12, "trend has 12 months")
        check(ov["categories"][0]["amount_cents"] == 3250, "category rollup excludes work")

        print("\n[work flag]")
        code, lst = req("GET", "/api/entries?usage=work&month=2026-10")
        check(lst["total"] == 2, "filter work", lst["total"])
        check(all(x["status"] == "work" for x in lst["items"]), "work status flag")
        code, ov = req("GET", "/api/overview?month=2026-10")
        check(len(ov["work_recent"]) == 2 and ov["work_recent"][0]["usage"] == "work", "work_recent list")

        print("\n[filters & bulk]")
        code, lst = req("GET", "/api/entries?usage=work&month=2026-10")
        check(lst["total"] == 2, "filter work", lst["total"])
        code, lst = req("GET", "/api/entries?status=work")
        check(lst["total"] == 2, "filter status work", lst["total"])
        code, lst = req("GET", "/api/entries?date_from=2026-10-05&date_to=2026-10-05")
        check(lst["total"] == 1 and lst["items"][0]["id"] == e1["id"], "filter single day", lst["total"])
        code, r = req("POST", "/api/entries/bulk", {"ids": [e2["id"]], "usage": "personal"})
        check(r["updated"] == 1, "bulk update")
        code, row = req("GET", "/api/entries?month=2026-10&limit=200")
        one = [x for x in row["items"] if x["id"] == e2["id"]][0]
        check(one["usage"] == "personal", "bulk marks personal")

        print("\n[import]")
        rows = [
            ["2026-09-01 12:00", "支出", "-12.00", "食品餐饮", "午餐", "支付宝", "日常账本",
             None, None, None, "测试午饭", None, None, None, "f1lter", None, None, None, None, None],
            ["2026-09-01 12:00", "支出", "-12.00", "食品餐饮", "午餐", "支付宝", "日常账本",
             None, None, None, "测试午饭", None, None, None, "f1lter", None, None, None, None, None],
            ["2026-09-02 08:30", "支出", "-50.00", "出行交通", "打车", "微信钱包", "日常账本",
             None, None, None, "客户打车", None, None, None, "f1lter", None, None, None, None, None],
            ["2026-09-03 09:00", "收入", "1000.00", "收入", "工资", "招商银行", "日常账本",
             None, None, None, "工资", None, None, None, "f1lter", None, None, None, None, None],
            ["2026-09-04 20:00", "支出", "-30.00", "购物消费", "", "招商银行", "日常账本",
             None, None, None, "押金", None, None, None, "f1lter", None, "不计入收支", None, None, None],
        ]
        data = xlsx_bytes(rows)
        code, prev = req("POST", "/api/import/preview?filename=t.xlsx", raw=data)
        check(code == 200 and prev["total"] == 5, "preview rows", prev)
        check(prev["expense_count"] == 4 and prev["income_count"] == 1, "preview split")
        check(prev["exclude_stats_count"] == 1, "preview exclude stats")

        code, r = req("POST", "/api/import?filename=t.xlsx&mode=merge", raw=data)
        check(code == 200 and r["imported"] == 5, "import 5 rows", r)

        code, r = req("POST", "/api/import?filename=t.xlsx&mode=merge", raw=data)
        check(r["imported"] == 0 and r["skipped"] == 5, "re-import dedupes", r)

        code, ov = req("GET", "/api/overview?month=2026-09")
        check(ov["personal_cents"] == 2400, "imported personal total (taxi moved to work)", ov["personal_cents"])
        check(ov["work_cents"] == 5000, "imported taxi auto-marked work", ov["work_cents"])
        check(ov["income_cents"] == 100000, "imported income", ov["income_cents"])
        code, lst = req("GET", "/api/entries?status=work&month=2026-09")
        check(lst["total"] == 1 and lst["items"][0]["note"] == "客户打车", "work filter finds taxi", lst["total"])

        print("\n[export]")
        code, csv = req("GET", "/api/export.csv?month=2026-10")
        check(code == 200 and "日期" in csv and "客户打车" in csv, "csv export")

        code, row = req("PATCH", f"/api/entries/{e3['id']}", {"usage": "personal"})
        check(row["usage"] == "personal", "patch to personal")

        print("\n[settings & cleanup]")
        code, info = req("GET", "/api/settings")
        check(code == 200 and info["counts"]["entries"] == 8, "settings counts", info["counts"])
        code, _ = req("DELETE", f"/api/entries/{e1['id']}")
        check(code == 200, "delete entry")
        code, info = req("GET", "/api/settings")
        check(info["counts"]["entries"] == 7, "count after delete")

        print("\n[alipay import]")
        ali_rows = [
            ["2026/10/10 12:30", "餐饮美食", "某餐厅", "", "午饭", "支出", "32.00", "账户余额",
             "交易成功", "", "", ""],
            ["2026/10/11 09:10", "交通出行", "滴滴出行", "", "打车", "支出", "18.50",
             "招商银行储蓄卡(1234)", "交易成功", "", "", ""],
            ["2026/10/12 10:00", "转账红包", "朋友", "", "转账", "收入", "100.00", "账户余额",
             "交易成功", "", "", ""],
            ["2026/10/13 11:00", "日用百货", "淘宝", "", "已关闭", "支出", "50.00", "账户余额",
             "已关闭", "", "", ""],
            ["2026/10/14 19:00", "文化休闲", "美团", "", "外卖", "支出", "20.00", "账户余额",
             "交易成功", "", "", ""],
            ["2026/10/15 20:00", "文化休闲", "美团", "", "美团单车", "支出", "1.50", "账户余额",
             "交易成功", "", "", ""],
        ]
        ali_data = alipay_bytes(ali_rows)
        code, prev = req("POST", "/api/import/preview?filename=ali.csv", raw=ali_data)
        check(code == 200 and prev["source"] == "alipay", "detect alipay source", prev.get("source"))
        check(prev["total"] == 5 and prev["expense_count"] == 4 and prev["income_count"] == 1,
              "alipay preview rows", {k: prev.get(k) for k in ("total", "expense_count", "income_count")})
        check(prev["skipped_total"] == 1 and prev["skipped"].get("已关闭") == 1, "alipay skips closed", prev.get("skipped"))
        check(prev["expense_cents"] == 7200 and prev["income_cents"] == 10000, "alipay totals",
              (prev["expense_cents"], prev["income_cents"]))
        check(prev["start"] == "2026-10-10" and prev["end"] == "2026-10-15", "alipay date range")
        check(prev["auto_work_count"] == 1, "alipay preview auto-work count", prev.get("auto_work_count"))

        code, r = req("POST", "/api/import?filename=ali.csv&mode=merge", raw=ali_data)
        check(code == 200 and r["imported"] == 5 and r["source"] == "alipay", "alipay import", r)
        check(r["auto_work"] == 1, "alipay auto-marked one work row", r.get("auto_work"))
        code, r = req("POST", "/api/import?filename=ali.csv&mode=merge", raw=ali_data)
        check(r["imported"] == 0 and r["skipped"] == 5, "alipay re-import dedupes", r)

        code, lst = req("GET", "/api/entries?month=2026-10&q=午饭")
        check(lst["total"] == 1 and lst["items"][0]["category_label"] == "食品餐饮", "alipay food mapping",
              lst["items"][0]["category_label"] if lst["items"] else None)
        code, lst = req("GET", "/api/entries?month=2026-10&q=美团单车")
        check(lst["items"][0]["category_label"] == "出行交通", "alipay keyword refine",
              lst["items"][0]["category_label"] if lst["items"] else None)
        code, lst = req("GET", "/api/entries?month=2026-10&q=打车")
        check(lst["items"][0]["account_name"] == "招商银行", "alipay account mapping",
              lst["items"][0]["account_name"] if lst["items"] else None)
        check(lst["items"][0]["usage"] == "work" and lst["items"][0]["status"] == "work",
              "taxi defaults to work", lst["items"][0]["usage"] if lst["items"] else None)

        print("\n[work rules]")
        check(rules.is_work("出行交通", "", "铁路12306 · 火车票"), "rule: train")
        check(rules.is_work("出行交通", "", "汉庭酒店"), "rule: hotel")
        check(rules.is_work("出行交通", "", "滴滴快车打车"), "rule: taxi")
        check(not rules.is_work("出行交通", "", "某市地铁(某火车站)"), "rule: subway is not travel")
        check(not rules.is_work("食品餐饮", "", "午饭"), "rule: lunch untouched")
        code, hotel = req("POST", "/api/entries", {
            "occurred_at": "2026-10-16 21:00", "amount": 260, "note": "某市出差住宿",
            "category_id": meta["categories"][0]["id"],
        })
        check(code == 201 and hotel["usage"] == "personal", "manual entry stays personal until rules applied")
        code, dry = req("POST", "/api/entries/apply-work-rules", {"dry_run": True})
        check(code == 200 and dry["matched"] >= 1 and dry["updated"] == 0, "dry run counts matches", dry)
        code, applied = req("POST", "/api/entries/apply-work-rules", {"dry_run": False})
        check(applied["updated"] == dry["matched"], "apply rules updates", applied)
        code, row = req("GET", "/api/entries?month=2026-10&q=住宿")
        check(row["items"][0]["usage"] == "work" and row["items"][0]["status"] == "work",
              "hotel marked as work", row["items"][0]["usage"] if row["items"] else None)

        print("\n[assets]")
        code, acc = req("POST", "/api/accounts", {
            "name": "测试储蓄", "type": "asset", "current_balance": 1000,
        })
        check(code == 201 and acc["balance_cents"] == 100000, "create account with balance", acc)
        code, _ = req("POST", "/api/entries", {
            "occurred_at": "2026-10-16 12:00", "amount": 200, "account_id": acc["id"],
            "note": "资产测试",
        })
        code, accs = req("GET", "/api/accounts")
        bank = next(a for a in accs["items"] if a["id"] == acc["id"])
        check(bank["balance_cents"] == 80000, "account balance follows expenses", bank["balance_cents"])

        code, cc = req("POST", "/api/accounts", {
            "name": "测试信用卡", "type": "liability", "current_balance": -500,
        })
        check(cc["balance_cents"] == -50000, "liability balance negative", cc["balance_cents"])

        alipay = next(a for a in meta["accounts"] if a["name"] == "支付宝")
        code, tr = req("POST", "/api/transfers", {
            "date": "2026-10-17", "from_account_id": acc["id"],
            "to_account_id": alipay["id"], "amount": 300, "note": "测试转账",
        })
        check(code == 201 and tr["amount_cents"] == 30000, "create transfer", tr)
        code, accs = req("GET", "/api/accounts")
        bank = next(a for a in accs["items"] if a["id"] == acc["id"])
        check(bank["balance_cents"] == 50000, "transfer moves money out", bank["balance_cents"])

        code, nw = req("GET", "/api/networth")
        check(code == 200 and nw["net_cents"] == nw["assets_cents"] - nw["liabilities_cents"],
              "net = assets - liabilities")
        check(nw["liabilities_cents"] >= 50000, "liability counted", nw["liabilities_cents"])
        check(len(nw["trend"]) == 12, "net worth trend 12 months")
        check("change_cents" in nw["trend"][0] and "month_change_cents" in nw,
              "net worth monthly change fields")
        check(any(a["id"] == acc["id"] for a in nw["accounts"]), "net worth lists accounts")

        code, _ = req("DELETE", f"/api/transfers/{tr['id']}")
        code, accs = req("GET", "/api/accounts")
        bank = next(a for a in accs["items"] if a["id"] == acc["id"])
        check(bank["balance_cents"] == 80000, "delete transfer restores balance", bank["balance_cents"])

        code, det = req("GET", f"/api/accounts/{acc['id']}/detail")
        check(code == 200 and det["opening_cents"] + det["income_cents"] - det["expense_cents"]
              + det["transfer_in_cents"] - det["transfer_out_cents"] == det["balance_cents"],
              "account balance composition adds up", det)

        code, cal = req("PATCH", f"/api/accounts/{acc['id']}", {"current_balance": 1200})
        check(cal["balance_cents"] == 120000, "calibrate account to current balance", cal["balance_cents"])
        code, err = req("DELETE", f"/api/accounts/{acc['id']}")
        check(code == 422, "cannot delete account with balance", code)

        print("\n[budget]")
        food = next(c for c in meta["categories"] if c["name"] == "食品餐饮" and not c["parent_id"])
        for payload in [
            {"occurred_at": "2025-01-05 12:00", "amount": 100, "category_id": food["id"], "note": "预算内"},
            {"occurred_at": "2025-01-06 12:00", "amount": 50, "category_id": food["id"], "note": "不计预算", "exclude_budget": True},
            {"occurred_at": "2025-01-07 12:00", "amount": 200, "category_id": food["id"], "note": "报销", "usage": "work"},
        ]:
            req("POST", "/api/entries", payload)
        code, _ = req("PUT", "/api/budgets", {"category_id": None, "amount": 1000})
        check(code == 200, "set total budget")
        code, _ = req("PUT", "/api/budgets", {"category_id": food["id"], "amount": 300})
        check(code == 200, "set category budget")

        code, b = req("GET", "/api/budget?month=2025-01")
        check(code == 200 and b["total"]["budget_cents"] == 100000, "budget total", b["total"])
        check(b["total"]["spent_cents"] == 10000, "budget excludes work & flagged", b["total"]["spent_cents"])
        check(len(b["items"]) == 1 and b["items"][0]["spent_cents"] == 10000, "category spend", b["items"])
        check(abs(b["items"][0]["pct"] - 1 / 3) < 0.01, "budget pct", b["items"][0]["pct"])

        code, _ = req("PUT", "/api/budgets", {"category_id": food["id"], "amount": 0})
        code, b = req("GET", "/api/budget?month=2025-01")
        check(len(b["items"]) == 0, "delete category budget")
        check(b["total"]["budget_cents"] == 100000, "total budget kept")

        print("\n[wechat import]")
        wx_rows = [
            ["2026-10-01 12:00:00", "商户消费", "美团", "外卖", "支出", 35, "零钱",
             "支付成功", "WX001", "", "/"],
            ["2026-10-02 08:00:00", "商户消费", "滴滴出行", "打车", "支出", 22,
             "招商银行储蓄卡(1234)", "支付成功", "WX002", "", "/"],
            ["2026-10-03 10:00:00", "转账", "朋友", "转账", "收入", 100, "零钱",
             "已收钱", "WX003", "", "/"],
            ["2026-10-04 10:00:00", "商户消费", "某店", "商品", "支出", 50, "零钱",
             "支付失败", "WX004", "", "/"],
            ["2026-10-05 10:00:00", "充值", "微信", "零钱充值", "不计收支", 200,
             "招商银行储蓄卡(1234)", "支付成功", "WX005", "", "/"],
        ]
        wx_data = wechat_bytes(wx_rows)
        code, prev = req("POST", "/api/import/preview?filename=wx.xlsx", raw=wx_data)
        check(code == 200 and prev["source"] == "wechat", "detect wechat source", prev.get("source"))
        check(prev["total"] == 4 and prev["income_count"] == 1, "wechat preview rows",
              {k: prev.get(k) for k in ("total", "income_count")})
        check(prev["skipped_total"] == 1 and prev["skipped"].get("支付失败") == 1,
              "wechat skips failed", prev.get("skipped"))
        check(prev["auto_work_count"] == 1, "wechat taxi auto-work", prev.get("auto_work_count"))

        code, r = req("POST", "/api/import?filename=wx.xlsx&mode=merge", raw=wx_data)
        check(code == 200 and r["imported"] == 4 and r["source"] == "wechat", "wechat import", r)
        code, r = req("POST", "/api/import?filename=wx.xlsx&mode=merge", raw=wx_data)
        check(r["imported"] == 0 and r["skipped"] == 4, "wechat re-import dedupes", r)

        code, lst = req("GET", "/api/entries?q=美团&date_from=2026-10-01&date_to=2026-10-01")
        check(lst["items"][0]["category_label"] == "食品餐饮", "wechat food category",
              lst["items"][0]["category_label"] if lst["items"] else None)
        code, lst = req("GET", "/api/entries?q=滴滴&date_from=2026-10-02&date_to=2026-10-02")
        check(lst["items"][0]["usage"] == "work", "wechat taxi marked work")
        check(lst["items"][0]["occurred_at"] == "2026-10-02 08:00", "wechat time normalized",
              lst["items"][0]["occurred_at"])
        code, lst = req("GET", "/api/entries?month=2026-10&q=零钱充值")
        check(lst["items"][0]["exclude_stats"] is True, "wechat neutral marked excluded")
        check(lst["items"][0]["account_name"] == "招商银行", "wechat account mapping",
              lst["items"][0]["account_name"])

        print("\n[statistics]")
        code, st = req("GET", "/api/stats?period=month&key=2026-10&scope=personal")
        check(code == 200 and len(st["series"]) == 31, "month stats series", len(st.get("series", [])))
        code, ov = req("GET", "/api/overview?month=2026-10")
        check(st["summary"]["expense_cents"] == ov["personal_cents"], "stats personal aligns with overview",
              (st["summary"]["expense_cents"], ov["personal_cents"]))
        check(st["summary"]["income_cents"] == ov["income_cents"], "stats income aligns")
        check(sum(x["expense_cents"] for x in st["series"]) == st["summary"]["expense_cents"],
              "series sums to summary")
        check(sum(c["amount_cents"] for c in st["categories"]) == st["summary"]["expense_cents"],
              "categories sum to summary")
        check(st["income_total_cents"] == st["summary"]["income_cents"], "income total aligns")
        check(sum(c["amount_cents"] for c in st["income_categories"]) == st["income_total_cents"],
              "income categories sum")
        check(1 <= len(st["assets"]) <= 31 and "net_cents" in st["assets"][0],
              "asset trend daily points", len(st.get("assets", [])))
        check(st["assets"][0]["date"] == "2026-10-01"
              and st["assets"][-1]["date"] == min(_date.today(), _date(2026, 10, 31)).isoformat(),
              "asset points by date")

        code, stw = req("GET", "/api/stats?period=month&key=2026-10&scope=work")
        check(stw["summary"]["expense_cents"] == ov["work_cents"], "stats work scope aligns",
              (stw["summary"]["expense_cents"], ov["work_cents"]))
        check(stw["summary"]["income_cents"] == 0, "work scope excludes income", stw["summary"]["income_cents"])
        check(stw["summary"]["expense_count"] > 0, "work scope counts entries")
        check(sum(x["income_cents"] for x in stw["series"]) == 0, "work series has no income")
        check(st["summary"]["income_cents"] == ov["income_cents"], "personal scope keeps income")
        code, sta = req("GET", "/api/stats?period=month&key=2026-10&scope=all")
        check(sta["summary"]["expense_cents"] == ov["expense_cents"], "stats all scope aligns")

        code, sy = req("GET", "/api/stats?period=year&key=2026&scope=personal")
        check(code == 200 and len(sy["series"]) == 12, "year stats series", len(sy.get("series", [])))
        check(sum(x["expense_cents"] for x in sy["series"]) == sy["summary"]["expense_cents"],
              "year series sums to summary")
        check(sy["prev"]["expense_cents"] >= 0, "year prev present")
        check(sy["assets"][-1]["net_cents"] == st["assets"][-1]["net_cents"],
              "year last net matches month last net",
              (sy["assets"][-1]["net_cents"], st["assets"][-1]["net_cents"]))

        print("\n[fixed expense]")
        home = next(c for c in meta["categories"] if c["name"] == "居家生活" and not c["parent_id"])
        rent_cat = next(c for c in meta["categories"] if c["name"] == "房租" and c["parent_id"] == home["id"])
        code, rent = req("POST", "/api/entries", {
            "occurred_at": "2025-02-05 10:00", "amount": 3000, "category_id": rent_cat["id"],
            "note": "2月房租", "exclude_daily": True,
        })
        check(code == 201 and rent["exclude_daily"] is True, "create fixed expense")
        code, ov = req("GET", "/api/overview?month=2025-02")
        check(ov["personal_cents"] == 0 and ov["fixed_cents"] == 300000, "overview excludes fixed",
              (ov["personal_cents"], ov["fixed_cents"]))
        check(ov["expense_cents"] == 300000, "total expense still includes rent")
        code, stf = req("GET", "/api/stats?period=month&key=2025-02&scope=personal")
        check(stf["summary"]["expense_cents"] == 0, "stats personal excludes rent")
        code, sta = req("GET", "/api/stats?period=month&key=2025-02&scope=all")
        check(sta["summary"]["expense_cents"] == 300000, "stats all includes rent")

        req("PUT", "/api/budgets", {"category_id": None, "amount": 5000})
        code, b = req("GET", "/api/budget?month=2025-02")
        check(b["total"]["spent_cents"] == 0, "budget excludes fixed", b["total"]["spent_cents"])
        req("POST", "/api/entries", {
            "occurred_at": "2025-02-06 12:00", "amount": 100, "category_id": food["id"], "note": "预算午饭",
        })
        code, b = req("GET", "/api/budget?month=2025-02")
        check(b["total"]["spent_cents"] == 10000, "budget counts normal spend", b["total"]["spent_cents"])

        code, rent2 = req("POST", "/api/entries", {
            "occurred_at": "2025-02-07 10:00", "amount": 2000, "category_id": rent_cat["id"],
            "note": "2月房租补款",
        })
        code, dry = req("POST", "/api/entries/apply-daily-rules", {"dry_run": True})
        check(dry["matched"] >= 1 and dry["updated"] == 0, "daily rules dry run", dry)
        code, applied = req("POST", "/api/entries/apply-daily-rules", {"dry_run": False})
        check(applied["updated"] == dry["matched"], "daily rules applied")
        code, ov = req("GET", "/api/overview?month=2025-02")
        check(ov["fixed_cents"] == 500000 and ov["personal_cents"] == 10000,
              "fixed total updated", (ov["fixed_cents"], ov["personal_cents"]))
        check(ov["trend"][-1]["month"] == _date.today().strftime("%Y-%m"),
              "overview trend anchored to current month", ov["trend"][-1])
        req("POST", "/api/entries", {
            "occurred_at": _date.today().isoformat() + " 16:00", "amount": 100,
            "category_id": rent_cat["id"], "note": "本月固定测试", "exclude_daily": True,
        })
        code, ovc = req("GET", "/api/overview?month=2025-02")
        check(ovc["trend"][-1]["month"] == _date.today().strftime("%Y-%m")
              and ovc["trend"][-1]["fixed_cents"] >= 10000,
              "trend includes current fixed", ovc["trend"][-1])

        import_rows_rent = [
            ["2025-03-01 09:00", "支出", "-3000.00", "居家生活", "房租", "银行卡", "日常账本",
             None, None, None, "3月房租", None, None, None, "f1lter", None, None, None, None, None],
        ]
        data = xlsx_bytes(import_rows_rent)
        code, prev = req("POST", "/api/import/preview?filename=rent.xlsx", raw=data)
        check(prev["fixed_count"] == 1, "import preview fixed count", prev.get("fixed_count"))
        code, r = req("POST", "/api/import?filename=rent.xlsx&mode=merge", raw=data)
        check(r["fixed_rows"] == 1, "import auto fixed", r.get("fixed_rows"))
        code, lst = req("GET", "/api/entries?month=2025-03&q=3月房租")
        check(lst["items"][0]["exclude_daily"] is True, "imported rent flagged")

        print("\n[reconcile]")
        code, racc = req("POST", "/api/accounts", {
            "name": "校准账户", "type": "asset", "current_balance": 1000,
        })
        req("POST", "/api/entries", {
            "occurred_at": "2026-10-10 12:00", "amount": 200, "account_id": racc["id"], "note": "校准前支出",
        })
        code, before_ov = req("GET", "/api/overview?month=2026-10")
        code, rec = req("POST", "/api/reconcile", {
            "balances": {str(racc["id"]): 900}, "reason": "测试校准",
        })
        check(code == 200 and rec["adjusted"] == 1 and rec["total_diff_cents"] == 10000,
              "reconcile creates adjustment", rec)
        code, accs = req("GET", "/api/accounts")
        row = next(a for a in accs["items"] if a["id"] == racc["id"])
        check(row["balance_cents"] == 90000, "reconciled balance", row["balance_cents"])
        code, lst = req("GET", "/api/entries?q=糊涂账")
        adj = next(x for x in lst["items"] if x["account_id"] == racc["id"])
        check(adj["source"] == "reconcile" and adj["exclude_stats"] and adj["exclude_daily"]
              and adj["exclude_budget"], "adjustment flags", adj)
        code, after_ov = req("GET", "/api/overview?month=2026-10")
        check(after_ov["personal_cents"] == before_ov["personal_cents"]
              and after_ov["expense_cents"] == before_ov["expense_cents"],
              "adjustment excluded from stats")
        code, rec0 = req("POST", "/api/reconcile", {"balances": {str(racc["id"]): 900}})
        check(rec0["adjusted"] == 0, "no change no adjustment")

        code, cc2 = req("POST", "/api/accounts", {
            "name": "校准信用卡", "type": "liability", "current_balance": -500,
        })
        code, recl = req("POST", "/api/reconcile", {"balances": {str(cc2["id"]): -650}})
        check(recl["adjusted"] == 1 and recl["total_diff_cents"] == -15000, "liability adjustment", recl)
        code, accs = req("GET", "/api/accounts")
        row = next(a for a in accs["items"] if a["id"] == cc2["id"])
        check(row["balance_cents"] == -65000, "liability reconciled", row["balance_cents"])

        print("\n[export / backup]")
        code, ex = req("POST", "/api/export", {"scope": "all"})
        check(code == 200 and ex["count"] > 0, "export all", ex.get("count"))
        export_path = Path(ex["path"])
        raw = export_path.read_bytes()
        check(raw.startswith(b"\xef\xbb\xbf"), "export has BOM")
        text = raw.decode("utf-8-sig")
        header = text.splitlines()[0]
        check("日期" in header and "来源" in header and "不计入日常" in header,
              "export header", header)
        check(len(text.splitlines()) == ex["count"] + 1, "export row count")
        check(str(export_path).startswith(os.environ["HUTUZHANG_EXPORT_DIR"]),
              "export lands in export dir")

        code, exm = req("POST", "/api/export", {"scope": "month", "month": "2026-10"})
        check(code == 200 and exm["count"] > 0 and "2026-10" in exm["filename"],
              "export month", exm)
        code, exy = req("POST", "/api/export", {"scope": "year", "year": "2026"})
        check(code == 200 and exy["count"] >= exm["count"], "export year", exy.get("count"))

        code, bk = req("POST", "/api/backup", {})
        backup_path = Path(bk["path"])
        check(code == 200 and backup_path.read_bytes()[:16] == b"SQLite format 3\x00",
              "backup is sqlite", bk)

        code, csv_text = req("GET", "/api/export.csv?month=2026-10")
        check(code == 200 and csv_text.startswith("\ufeff") and "日期" in csv_text.splitlines()[0],
              "legacy csv download")

        print("\n[backup restore]")
        code, meta_before = req("GET", "/api/meta")
        before_entries = meta_before["entry_count"]
        code, bk2 = req("POST", "/api/backup", {})
        backup_bytes = Path(bk2["path"]).read_bytes()

        req("POST", "/api/entries", {
            "occurred_at": "2026-10-18 12:00", "amount": 12.34, "note": "恢复后应消失",
        })
        code, prev = req("POST", "/api/import/backup/preview?filename=test.db", raw=backup_bytes)
        check(code == 200 and prev["kind"] == "backup" and prev["entries"] == before_entries,
              "backup preview counts", prev)
        code, restored = req("POST", "/api/import/backup", raw=backup_bytes)
        check(code == 200 and restored["counts"]["entries"] == before_entries,
              "backup restored", restored)
        check(Path(restored["backup_path"]).exists(), "pre-restore backup exists")
        code, found = req("GET", "/api/entries?q=" + quote("恢复后应消失"))
        check(len(found["items"]) == 0, "restore removed new entry")
        code, meta_after = req("GET", "/api/meta")
        check(meta_after["entry_count"] == before_entries, "meta count restored")

        code, bad = req("POST", "/api/import/backup", raw=b"definitely not sqlite")
        check(code == 422, "invalid backup rejected", code)

        print("\n[daily budget]")
        code, meta_now = req("GET", "/api/meta")
        food_cat = next(c for c in meta_now["categories"] if c["name"] == "食品餐饮" and not c["parent_id"])
        req("PUT", "/api/budgets", {"category_id": None, "amount": 3100})
        code, b0 = req("GET", "/api/budget?month=2026-10")
        daily0 = b0["total"]["daily"]
        check(daily0["days_in_month"] == 31, "daily days in month", daily0)
        check(daily0["budget_cents"] == 10000, "daily budget = total / days", daily0)
        check(daily0["is_current"] is True, "daily current month")

        before_spent = b0["total"]["spent_cents"]
        before_today = daily0["today_spent_cents"]
        today = _date.today().isoformat()
        req("POST", "/api/entries", {
            "occurred_at": today + " 13:00", "amount": 50, "category_id": food_cat["id"],
            "note": "日预算测试",
        })
        req("POST", "/api/entries", {
            "occurred_at": today + " 13:01", "amount": 10, "note": "日预算无分类",
        })
        code, b1 = req("GET", "/api/budget?month=2026-10")
        daily1 = b1["total"]["daily"]
        check(b1["total"]["spent_cents"] == before_spent + 6000,
              "budget total includes uncategorized", (before_spent, b1["total"]["spent_cents"]))
        check(daily1["today_spent_cents"] == before_today + 6000,
              "daily today spend", (before_today, daily1["today_spent_cents"]))
        check(daily1["today_remaining_cents"] == daily1["budget_cents"] - daily1["today_spent_cents"],
              "daily today remaining")

        print("\n[editable rules]")
        code, cfg = req("GET", "/api/rules")
        check(code == 200 and "高铁" in cfg["work"]["keywords"], "rules defaults", cfg)
        cfg["work"]["keywords"].append("规则测试词")
        cfg["fixed"]["keywords"].append("固定测试词")
        code, saved = req("PUT", "/api/rules", cfg)
        check(code == 200 and "规则测试词" in saved["work"]["keywords"], "rules saved", saved)
        code, meta_rules = req("GET", "/api/meta")
        check("规则测试词" in meta_rules["rules"]["work"]["keywords"], "rules in meta")

        req("POST", "/api/entries", {
            "occurred_at": today + " 14:00", "amount": 10, "note": "规则测试词",
        })
        code, dryw = req("POST", "/api/entries/apply-work-rules", {"dry_run": True})
        check(dryw["matched"] >= 1, "custom work rule matches", dryw)
        req("POST", "/api/entries/apply-work-rules", {"dry_run": False})
        code, found = req("GET", "/api/entries?q=规则测试词")
        check(found["items"] and found["items"][0]["usage"] == "work", "custom work tag applied")

        req("POST", "/api/entries", {
            "occurred_at": today + " 14:01", "amount": 20, "note": "固定测试词",
        })
        code, dryf = req("POST", "/api/entries/apply-daily-rules", {"dry_run": True})
        check(dryf["matched"] >= 1, "custom fixed rule matches", dryf)
        req("POST", "/api/entries/apply-daily-rules", {"dry_run": False})
        code, found = req("GET", "/api/entries?q=固定测试词")
        check(found["items"] and found["items"][0]["exclude_daily"] is True, "custom fixed tag applied")

        code, reset = req("POST", "/api/rules/reset", {})
        check("规则测试词" not in reset["work"]["keywords"] and "高铁" in reset["work"]["keywords"],
              "rules reset", reset)

        print("\n[budget exclusion]")
        before = req("GET", "/api/budget?month=2026-10")[1]["total"]["spent_cents"]
        code, big = req("POST", "/api/entries", {
            "occurred_at": today + " 15:00", "amount": 1200,
            "category_id": food_cat["id"], "note": "大额测试",
        })
        after_add = req("GET", "/api/budget?month=2026-10")[1]["total"]["spent_cents"]
        check(after_add == before + 120000, "large expense counts in budget")
        nob0 = req("GET", "/api/overview?month=2026-10")[1]["trend"][-1]["nobudget_cents"]
        ov0 = req("GET", "/api/overview?month=2026-10")[1]
        st_per0 = req("GET", "/api/stats?period=month&key=2026-10&scope=personal")[1]["summary"]["expense_cents"]
        st_nb0 = req("GET", "/api/stats?period=month&key=2026-10&scope=nobudget")[1]["summary"]["expense_cents"]
        req("POST", "/api/entries/bulk", {"ids": [big["id"]], "exclude_budget": True})
        excluded = req("GET", "/api/budget?month=2026-10")[1]["total"]["spent_cents"]
        check(excluded == before, "exclude_budget removes from budget", (before, excluded))
        nob1 = req("GET", "/api/overview?month=2026-10")[1]["trend"][-1]["nobudget_cents"]
        check(nob1 == nob0 + 120000, "trend splits no-budget", (nob0, nob1))
        ov1 = req("GET", "/api/overview?month=2026-10")[1]
        check(ov1["personal_cents"] == ov0["personal_cents"] - 120000
              and ov1["nobudget_cents"] == ov0["nobudget_cents"] + 120000,
              "overview daily excludes no-budget")
        st_per1 = req("GET", "/api/stats?period=month&key=2026-10&scope=personal")[1]["summary"]["expense_cents"]
        st_nb1 = req("GET", "/api/stats?period=month&key=2026-10&scope=nobudget")[1]["summary"]["expense_cents"]
        check(st_per1 == st_per0 - 120000, "stats daily excludes no-budget")
        check(st_nb1 == st_nb0 + 120000, "stats no-budget scope")
        code, rows = req("GET", "/api/entries?q=大额测试")
        check(rows["items"][0]["exclude_budget"] is True, "bulk exclude_budget flag")
        req("POST", "/api/entries/bulk", {"ids": [big["id"]], "exclude_budget": False})
        restored = req("GET", "/api/budget?month=2026-10")[1]["total"]["spent_cents"]
        check(restored == after_add, "include budget restores spend")
        nob2 = req("GET", "/api/overview?month=2026-10")[1]["trend"][-1]["nobudget_cents"]
        check(nob2 == nob0, "trend no-budget restores")

        print(f"\n{passed} passed, {failed} failed")
        return 0 if failed == 0 else 1
    finally:
        server.should_exit = True
        time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(main())
