"""SQLite storage layer for 糊涂账.

Local-first: one SQLite file under
~/Library/Application Support/hutuzhang/hutuzhang.db
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

APP_SUPPORT = Path(
    os.environ.get("HUTUZHANG_HOME")
    or os.environ.get("JINGZHI_HOME")
    or (Path.home() / "Library" / "Application Support" / "hutuzhang")
)
DB_PATH = APP_SUPPORT / "hutuzhang.db"
LEGACY_DB = Path.home() / "Library" / "Application Support" / "JingZhi" / "jingzhi.db"

_MIGRATED = False
_MIGRATE_LOCK = threading.Lock()


def _legacy_paths() -> list[Path]:
    paths = [APP_SUPPORT / "jingzhi.db"]
    if not (os.environ.get("HUTUZHANG_HOME") or os.environ.get("JINGZHI_HOME")):
        paths.append(LEGACY_DB)
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _ensure_db() -> None:
    """Move the old JingZhi database into the hutuzhang path once."""
    global _MIGRATED
    if _MIGRATED:
        return
    with _MIGRATE_LOCK:
        if _MIGRATED:
            return
        _MIGRATED = True
        if DB_PATH.exists():
            return
        old = next((p for p in _legacy_paths() if p.exists()), None)
        if not old:
            return
        APP_SUPPORT.mkdir(parents=True, exist_ok=True)
        tmp = APP_SUPPORT / ".hutuzhang-migrating.db"
        tmp.unlink(missing_ok=True)
        try:
            src = sqlite3.connect(str(old), timeout=5)
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            os.replace(tmp, DB_PATH)
            print(f"[db] migrated {old} -> {DB_PATH}")
        except (sqlite3.Error, OSError) as exc:
            tmp.unlink(missing_ok=True)
            print(f"[db] migration failed ({exc}); starting with a new database, old file kept at {old}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    name      TEXT    NOT NULL,
    icon      TEXT    NOT NULL DEFAULT '',
    color     TEXT    NOT NULL DEFAULT '',
    sort      INTEGER NOT NULL DEFAULT 0,
    archived  INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_name
    ON categories(COALESCE(parent_id, 0), name);

CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    kind          TEXT    NOT NULL DEFAULT 'other',
    type          TEXT    NOT NULL DEFAULT 'asset'
                  CHECK (type IN ('asset', 'investment', 'liability')),
    opening_cents INTEGER NOT NULL DEFAULT 0,
    color         TEXT    NOT NULL DEFAULT '',
    sort          INTEGER NOT NULL DEFAULT 0,
    archived      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS claims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL DEFAULT '',
    project       TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'submitted', 'reimbursed')),
    submitted_at  TEXT,
    reimbursed_at TEXT,
    note          TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT    NOT NULL DEFAULT 'expense'
                   CHECK (kind IN ('expense', 'income')),
    occurred_at    TEXT    NOT NULL,
    amount_cents   INTEGER NOT NULL CHECK (amount_cents >= 0),
    category_id    INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    account_id     INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    note           TEXT    NOT NULL DEFAULT '',
    usage          TEXT    NOT NULL DEFAULT 'personal'
                   CHECK (usage IN ('personal', 'work')),
    claim_id       INTEGER REFERENCES claims(id) ON DELETE SET NULL,
    project        TEXT    NOT NULL DEFAULT '',
    invoice        TEXT    NOT NULL DEFAULT 'none'
                   CHECK (invoice IN ('none', 'have', 'submitted')),
    invoice_no     TEXT    NOT NULL DEFAULT '',
    refund_cents   INTEGER NOT NULL DEFAULT 0,
    exclude_stats  INTEGER NOT NULL DEFAULT 0,
    exclude_daily  INTEGER NOT NULL DEFAULT 0,
    exclude_budget INTEGER NOT NULL DEFAULT 0,
    source         TEXT    NOT NULL DEFAULT 'manual',
    external_id    TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_external
    ON entries(external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_entries_date     ON entries(occurred_at);
CREATE INDEX IF NOT EXISTS idx_entries_usage    ON entries(usage);
CREATE INDEX IF NOT EXISTS idx_entries_claim    ON entries(claim_id);
CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category_id);

CREATE TABLE IF NOT EXISTS transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    from_account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    to_account_id   INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    amount_cents    INTEGER NOT NULL CHECK (amount_cents > 0),
    note            TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transfers_date ON transfers(date);

CREATE TABLE IF NOT EXISTS budgets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_budgets_cat
    ON budgets(COALESCE(category_id, 0));

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

# (parent, [(child, icon), ...], color)
SEED_CATEGORIES = [
    ("食品餐饮", ["早餐", "午餐", "晚餐", "饮料酒水", "休闲零食", "生鲜食品", "外卖"], "#F97316", "🍜"),
    ("出行交通", ["公共交通", "打车", "火车", "飞机", "加油停车"], "#3B82F6", "🚇"),
    ("购物消费", ["手机数码", "虚拟充值", "日用百货", "服饰鞋包", "美妆护肤"], "#EC4899", "🛍️"),
    ("居家生活", ["房租", "水电燃气", "物业", "家具家电", "家政维修"], "#10B981", "🏠"),
    ("休闲娱乐", ["电影演出", "游戏", "运动健身", "旅游", "聚会"], "#8B5CF6", "🎮"),
    ("文化教育", ["书籍", "课程", "考试"], "#06B6D4", "📚"),
    ("健康医疗", ["药品", "门诊", "体检", "保险"], "#EF4444", "💊"),
    ("送礼人情", ["礼物", "红包", "请客"], "#F43F5E", "🎁"),
    ("其他", [], "#94A3B8", "🧾"),
]

SEED_INCOME = ("收入", ["工资", "奖金", "兼职", "理财", "报销回款", "其他"], "#22C55E", "💰")

SEED_ACCOUNTS = [
    ("微信钱包", "wechat", "asset", "#07C160"),
    ("支付宝", "alipay", "asset", "#1677FF"),
    ("招商银行", "bank", "asset", "#B91C1C"),
    ("浦发银行", "bank", "asset", "#1D4ED8"),
    ("南京银行", "bank", "asset", "#0F766E"),
    ("信用卡", "credit", "liability", "#B45309"),
    ("现金", "cash", "asset", "#64748B"),
]

ACCOUNT_KIND_LABEL = {
    "wechat": "微信",
    "alipay": "支付宝",
    "bank": "银行卡",
    "credit": "信用卡",
    "cash": "现金",
    "other": "其他",
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def connect() -> sqlite3.Connection:
    _ensure_db()
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=5, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def _insert_category(con: sqlite3.Connection, name: str, parent_id, icon, color, sort) -> int:
    cur = con.execute(
        "INSERT INTO categories(parent_id, name, icon, color, sort) VALUES (?, ?, ?, ?, ?)",
        (parent_id, name, icon, color, sort),
    )
    return int(cur.lastrowid)


def _migrate(con: sqlite3.Connection) -> None:
    """Add columns introduced after the first release."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(accounts)")}
    if "type" not in cols:
        con.execute("ALTER TABLE accounts ADD COLUMN type TEXT NOT NULL DEFAULT 'asset'")
        con.execute("UPDATE accounts SET type = 'liability' WHERE kind = 'credit'")
    if "opening_cents" not in cols:
        con.execute("ALTER TABLE accounts ADD COLUMN opening_cents INTEGER NOT NULL DEFAULT 0")
    ecols = {r["name"] for r in con.execute("PRAGMA table_info(entries)")}
    if "exclude_daily" not in ecols:
        con.execute("ALTER TABLE entries ADD COLUMN exclude_daily INTEGER NOT NULL DEFAULT 0")


def init_db() -> None:
    _ensure_db()
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    con = connect()
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)
        _migrate(con)
        if con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            sort = 0
            for parent, children, color, icon in SEED_CATEGORIES + [SEED_INCOME]:
                pid = _insert_category(con, parent, None, icon, color, sort)
                sort += 1
                for idx, child in enumerate(children):
                    _insert_category(con, child, pid, "", color, idx)
        if con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            con.executemany(
                "INSERT INTO accounts(name, kind, type, color, sort) VALUES (?, ?, ?, ?, ?)",
                [(n, k, t, c, i) for i, (n, k, t, c) in enumerate(SEED_ACCOUNTS)],
            )
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('currency', 'CNY')")
        con.commit()
    finally:
        con.close()
