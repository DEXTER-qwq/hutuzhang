"""Create a synthetic demo dataset for screenshots / local testing.

Usage:
    HUTUZHANG_HOME=/tmp/hutuzhang-demo HUTUZHANG_PORT=8799 .venv/bin/python run.py
    BASE=http://127.0.0.1:8799 .venv/bin/python tools/seed_demo.py

Only talks to the local HTTP API. All names/amounts are fictional.
"""

from __future__ import annotations

import json
import os
import random
import urllib.request
from datetime import date

BASE = os.environ.get("BASE", "http://127.0.0.1:8799")
random.seed(20261018)


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


meta = call("GET", "/api/meta")
cats = {c["name"]: c for c in meta["categories"]}
accs = {a["name"]: a for a in meta["accounts"]}


def cat(parent: str, child: str = "") -> int:
    pid = cats[parent]["id"]
    if not child:
        return pid
    for c in meta["categories"]:
        if c["parent_id"] == pid and c["name"] == child:
            return c["id"]
    return pid


TODAY = date.today()
MONTHS = []
y, m = TODAY.year, TODAY.month
for _ in range(12):
    MONTHS.append((y, m))
    m -= 1
    if m == 0:
        y, m = y - 1, 12
MONTHS.reverse()

PERSONAL = [
    ("食品餐饮", "早餐", 8, 22, ["豆浆油条", "三明治", "包子"]),
    ("食品餐饮", "午餐", 22, 45, ["公司食堂", "楼下简餐", "外卖"]),
    ("食品餐饮", "晚餐", 28, 88, ["晚饭", "朋友聚餐", "加班餐"]),
    ("食品餐饮", "饮料酒水", 12, 32, ["咖啡", "奶茶", "果汁"]),
    ("出行交通", "公共交通", 3, 12, ["地铁", "公交"]),
    ("出行交通", "打车", 18, 56, ["打车回家", "去客户现场"]),
    ("购物消费", "日用百货", 25, 160, ["超市", "纸巾", "洗衣液"]),
    ("购物消费", "虚拟充值", 15, 68, ["云盘会员", "视频会员"]),
    ("休闲娱乐", "电影演出", 35, 90, ["电影票"]),
    ("文化教育", "书籍", 32, 120, ["技术书", "小说"]),
    ("健康医疗", "药品", 18, 60, ["感冒药", "创可贴"]),
    ("居家生活", "水电燃气", 80, 220, ["电费", "燃气费"]),
]


def add(kind, when, amount, category_id, account, note, **extra):
    body = {
        "kind": kind,
        "occurred_at": when,
        "amount": round(amount, 2),
        "category_id": category_id,
        "account_id": accs[account]["id"],
        "note": note,
    }
    body.update(extra)
    call("POST", "/api/entries", body)


count = 0
for idx, (y, m) in enumerate(MONTHS):
    last_day = TODAY.day if (y, m) == (TODAY.year, TODAY.month) else 28

    add("income", f"{y:04d}-{m:02d}-05 09:00", 15000, cat("收入", "工资"), "招商银行", "工资")
    count += 1

    if (11 - idx) % 3 == 0:  # current month, then every 3 months back
        add("expense", f"{y:04d}-{m:02d}-01 10:00", 5400, cat("居家生活", "房租"),
            "招商银行", "季度房租", exclude_daily=True)
        count += 1

    add("expense", f"{y:04d}-{m:02d}-08 19:00", random.uniform(120, 260),
        cat("居家生活", "水电燃气"), "支付宝", "水电燃气")
    count += 1

    for _ in range(random.randint(10, 16)):
        parent, child, lo, hi, notes = random.choice(PERSONAL)
        day = random.randint(1, last_day)
        add("expense", f"{y:04d}-{m:02d}-{day:02d} {random.randint(8, 21):02d}:{random.choice(['08','15','32','46'])}",
            random.uniform(lo, hi), cat(parent, child),
            random.choice(["微信钱包", "支付宝", "招商银行"]), random.choice(notes))
        count += 1

    if (y, m) == (TODAY.year, TODAY.month):
        add("expense", f"{y:04d}-{m:02d}-12 14:00", 4999,
            cat("购物消费", "手机数码"), "招商银行", "笔记本电脑", exclude_budget=True)
        count += 1

    if idx >= 10:
        for j, (child, note, amount) in enumerate([
            ("火车", "客户现场往返", 109.5),
            ("打车", "展会往返", 63.0),
            ("酒店", "出差住宿", 458.0),
        ]):
            day = min(3 + j * 4, last_day)
            add("expense", f"{y:04d}-{m:02d}-{day:02d} 12:30", amount,
                cat("出行交通", child), "招商银行", note,
                usage="work", project="客户项目", invoice="have")
            count += 1

# budgets: total + a few categories
call("PUT", "/api/budgets", {"category_id": None, "amount": 6000})
for parent, amount in [("食品餐饮", 1600), ("出行交通", 700), ("购物消费", 900), ("休闲娱乐", 500)]:
    call("PUT", "/api/budgets", {"category_id": cat(parent), "amount": amount})

# a couple of transfers
call("POST", "/api/transfers", {
    "date": f"{TODAY.year:04d}-{TODAY.month:02d}-15", "from_account_id": accs["招商银行"]["id"],
    "to_account_id": accs["支付宝"]["id"], "amount": 1200, "note": "日常备用金",
})

# plausible current balances for screenshots
balances = {
    "招商银行": 42000, "支付宝": 3800, "微信钱包": 1600,
    "南京银行": 6000, "浦发银行": 2000, "现金": 600, "信用卡": -2300,
}
for name, amount in balances.items():
    if name in accs:
        call("PATCH", f"/api/accounts/{accs[name]['id']}", {"current_balance": amount})

print(f"seeded {count} entries, 12 months, budget, transfers, balances")
