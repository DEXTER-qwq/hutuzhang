"""HTTP API for 糊涂账."""

from __future__ import annotations

import csv
import io
import os
import subprocess
import re
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import db, importer, rules

router = APIRouter(prefix="/api")

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")


# --------------------------------------------------------------------------- db dep

def get_con():
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


# --------------------------------------------------------------------------- helpers

def yuan(cents: int) -> float:
    return round((cents or 0) / 100, 2)


def parse_month(month: str) -> tuple[str, str]:
    if not MONTH_RE.match(month or ""):
        raise HTTPException(422, "月份格式应为 YYYY-MM")
    y, m = int(month[:4]), int(month[5:7])
    start = f"{y:04d}-{m:02d}-01"
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, f"{ny:04d}-{nm:02d}-01"


def shift_month(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    n = y * 12 + (m - 1) + delta
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


ENTRY_SQL = """
SELECT e.*,
       c.name  AS category_name,
       c.parent_id AS category_parent,
       COALESCE(p.name, c.name)  AS parent_name,
       COALESCE(p.icon, c.icon, '')  AS icon,
       COALESCE(p.color, c.color, '#94A3B8') AS color,
       a.name  AS account_name,
       a.kind  AS account_kind
FROM entries e
LEFT JOIN categories c ON c.id = e.category_id
LEFT JOIN categories p ON p.id = c.parent_id
LEFT JOIN accounts a   ON a.id = e.account_id
"""


def entry_dict(r) -> dict:
    if r["kind"] == "income":
        status = "income"
    elif r["usage"] == "work":
        status = "work"
    else:
        status = "personal"

    label = r["parent_name"] or "未分类"
    if r["category_parent"] and r["category_name"] and r["category_parent"] != r["category_id"]:
        label = f"{r['parent_name']} · {r['category_name']}"
    return {
        "id": r["id"],
        "kind": r["kind"],
        "occurred_at": r["occurred_at"],
        "date": r["occurred_at"][:10],
        "amount_cents": r["amount_cents"],
        "amount": yuan(r["amount_cents"]),
        "category_id": r["category_id"],
        "category_label": label,
        "category_icon": r["icon"] or "🧾",
        "category_color": r["color"],
        "account_id": r["account_id"],
        "account_name": r["account_name"] or "",
        "note": r["note"],
        "usage": r["usage"],
        "status": status,
        "project": r["project"],
        "invoice": r["invoice"],
        "invoice_no": r["invoice_no"],
        "refund_cents": r["refund_cents"],
        "refund": yuan(r["refund_cents"]),
        "exclude_stats": bool(r["exclude_stats"]),
        "exclude_daily": bool(r["exclude_daily"]),
        "exclude_budget": bool(r["exclude_budget"]),
        "source": r["source"],
        "external_id": r["external_id"],
    }


# --------------------------------------------------------------------------- models

class EntryBody(BaseModel):
    kind: Literal["expense", "income"] = "expense"
    occurred_at: str
    amount: float = Field(gt=0)
    category_id: Optional[int] = None
    account_id: Optional[int] = None
    note: str = ""
    usage: Literal["personal", "work"] = "personal"
    project: str = ""
    invoice: Literal["none", "have", "submitted"] = "none"
    invoice_no: str = ""
    exclude_stats: bool = False
    exclude_daily: bool = False
    exclude_budget: bool = False


class EntryPatch(BaseModel):
    kind: Optional[Literal["expense", "income"]] = None
    occurred_at: Optional[str] = None
    amount: Optional[float] = Field(default=None, gt=0)
    category_id: Optional[int] = None
    account_id: Optional[int] = None
    note: Optional[str] = None
    usage: Optional[Literal["personal", "work"]] = None
    project: Optional[str] = None
    invoice: Optional[Literal["none", "have", "submitted"]] = None
    invoice_no: Optional[str] = None
    exclude_stats: Optional[bool] = None
    exclude_daily: Optional[bool] = None
    exclude_budget: Optional[bool] = None


class BulkBody(BaseModel):
    ids: list[int]
    usage: Optional[Literal["personal", "work"]] = None
    project: Optional[str] = None
    category_id: Optional[int] = None
    exclude_stats: Optional[bool] = None
    exclude_daily: Optional[bool] = None
    exclude_budget: Optional[bool] = None


class IdsBody(BaseModel):
    ids: list[int]


class ApplyRulesBody(BaseModel):
    dry_run: bool = False


class NamedBody(BaseModel):
    name: str
    parent_id: Optional[int] = None
    icon: str = ""
    color: str = ""


class NamedPatch(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    archived: Optional[bool] = None


class AccountBody(BaseModel):
    name: str
    kind: str = "other"
    type: Literal["asset", "investment", "liability"] = "asset"
    color: str = ""
    current_balance: Optional[float] = None


class AccountPatch(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    type: Optional[Literal["asset", "investment", "liability"]] = None
    color: Optional[str] = None
    archived: Optional[bool] = None
    current_balance: Optional[float] = None


class TransferBody(BaseModel):
    date: str
    from_account_id: int
    to_account_id: int
    amount: float = Field(gt=0)
    note: str = ""


class BudgetBody(BaseModel):
    category_id: Optional[int] = None
    amount: float = Field(ge=0)


class ReconcileBody(BaseModel):
    balances: dict[int, float]
    reason: str = ""


class ExportBody(BaseModel):
    scope: Literal["all", "year", "month"] = "all"
    month: str = ""
    year: str = ""


class WorkRuleBody(BaseModel):
    keywords: list[str] = []
    exclude: list[str] = []


class FixedRuleBody(BaseModel):
    keywords: list[str] = []


class RulesBody(BaseModel):
    work: WorkRuleBody
    fixed: FixedRuleBody


class PathImportBody(BaseModel):
    path: str
    mode: Literal["merge", "replace"] = "merge"


# --------------------------------------------------------------------------- meta

@router.get("/meta")
def meta(con=Depends(get_con)):
    cats = [dict(r) for r in con.execute(
        "SELECT id, parent_id, name, icon, color, sort, archived FROM categories ORDER BY sort, id"
    )]
    accs = [dict(r) for r in con.execute(
        "SELECT id, name, kind, color, sort, archived FROM accounts ORDER BY sort, id"
    )]
    projects = [r[0] for r in con.execute(
        "SELECT DISTINCT project FROM entries WHERE project != '' ORDER BY 1"
    )]
    months = [r[0] for r in con.execute(
        "SELECT DISTINCT substr(occurred_at, 1, 7) AS m FROM entries ORDER BY m DESC"
    )]
    counts = con.execute("SELECT COUNT(*) AS entries FROM entries").fetchone()
    return {
        "categories": cats,
        "accounts": accs,
        "projects": projects,
        "months": months,
        "entry_count": counts["entries"],
        "today": date.today().isoformat(),
        "account_kinds": db.ACCOUNT_KIND_LABEL,
        "rules": rules.load(con),
    }


# --------------------------------------------------------------------------- overview

@router.get("/overview")
def overview(month: str = Query(default=""), con=Depends(get_con)):
    today = date.today()
    month = month or today.strftime("%Y-%m")
    start, end = parse_month(month)
    prev = shift_month(month, -1)
    pstart, pend = parse_month(prev)

    def totals(a: str, b: str):
        return con.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN kind='expense' AND usage='personal' AND exclude_stats=0
                              AND exclude_daily=0 AND exclude_budget=0 THEN amount_cents END), 0) AS personal,
                 COALESCE(SUM(CASE WHEN kind='expense' AND usage='personal' AND exclude_stats=0
                              AND exclude_daily=0 AND exclude_budget=1 THEN amount_cents END), 0) AS nobudget,
                 COALESCE(SUM(CASE WHEN kind='expense' AND usage='personal' AND exclude_stats=0
                              AND exclude_daily=1 THEN amount_cents END), 0) AS fixed,
                 COALESCE(SUM(CASE WHEN kind='expense' AND usage='work' AND exclude_stats=0
                              THEN amount_cents END), 0) AS work,
                 COALESCE(SUM(CASE WHEN kind='income' AND exclude_stats=0
                              THEN amount_cents END), 0) AS income,
                 COALESCE(SUM(CASE WHEN kind='expense' AND exclude_stats=0
                              THEN amount_cents END), 0) AS expense,
                 COUNT(CASE WHEN kind='expense' AND usage='work' AND exclude_stats=0 THEN 1 END) AS work_n,
                 COUNT(CASE WHEN kind='expense' AND usage='personal' AND exclude_stats=0
                            AND exclude_daily=0 AND exclude_budget=0 THEN 1 END) AS personal_n,
                 COUNT(CASE WHEN kind='expense' AND usage='personal' AND exclude_stats=0
                            AND exclude_daily=0 AND exclude_budget=1 THEN 1 END) AS nobudget_n,
                 COUNT(CASE WHEN kind='expense' AND usage='personal' AND exclude_stats=0
                            AND exclude_daily=1 THEN 1 END) AS fixed_n
               FROM entries WHERE occurred_at >= ? AND occurred_at < ?""",
            (a, b),
        ).fetchone()

    cur = totals(start, end)
    old = totals(pstart, pend)

    cats = [dict(r) for r in con.execute(
        """SELECT p.id, p.name, p.icon, p.color,
                  COALESCE(SUM(e.amount_cents), 0) AS amount_cents
           FROM entries e
           JOIN categories c ON c.id = e.category_id
           JOIN categories p ON p.id = COALESCE(c.parent_id, c.id)
           WHERE e.kind='expense' AND e.usage='personal' AND e.exclude_stats=0
             AND e.exclude_daily=0 AND e.exclude_budget=0
             AND e.occurred_at >= ? AND e.occurred_at < ?
           GROUP BY p.id HAVING amount_cents > 0
           ORDER BY amount_cents DESC""",
        (start, end),
    )]

    trend_end = today.strftime("%Y-%m")
    trend_start = shift_month(trend_end, -11) + "-01"
    trend_next = shift_month(trend_end, 1) + "-01"
    rows = con.execute(
        """SELECT substr(occurred_at, 1, 7) AS m,
                  SUM(CASE WHEN usage='personal' AND exclude_daily=0 AND exclude_budget=0
                           THEN amount_cents ELSE 0 END) AS personal,
                  SUM(CASE WHEN usage='personal' AND exclude_daily=0 AND exclude_budget=1
                           THEN amount_cents ELSE 0 END) AS nobudget,
                  SUM(CASE WHEN usage='personal' AND exclude_daily=1 THEN amount_cents ELSE 0 END) AS fixed,
                  SUM(CASE WHEN usage='work' THEN amount_cents ELSE 0 END) AS work
           FROM entries
           WHERE kind='expense' AND exclude_stats=0
             AND occurred_at >= ? AND occurred_at < ?
           GROUP BY m""",
        (trend_start, trend_next),
    ).fetchall()
    by_month = {r["m"]: r for r in rows}
    trend = []
    m = trend_start[:7]
    for _ in range(12):
        r = by_month.get(m)
        trend.append({
            "month": m,
            "personal_cents": r["personal"] if r else 0,
            "nobudget_cents": r["nobudget"] if r else 0,
            "fixed_cents": r["fixed"] if r else 0,
            "work_cents": r["work"] if r else 0,
        })
        m = shift_month(m, 1)

    recent = [entry_dict(r) for r in con.execute(
        ENTRY_SQL + " ORDER BY e.occurred_at DESC, e.id DESC LIMIT 6"
    )]

    work_recent = [entry_dict(r) for r in con.execute(
        ENTRY_SQL + """ WHERE e.kind='expense' AND e.usage='work' AND e.exclude_stats=0
                        AND e.occurred_at >= ? AND e.occurred_at < ?
                        ORDER BY e.occurred_at DESC, e.id DESC LIMIT 6""",
        (start, end),
    )]

    daily = [dict(r) for r in con.execute(
        """SELECT substr(occurred_at, 1, 10) AS d,
                  SUM(CASE WHEN usage='personal' AND exclude_daily=0 THEN amount_cents ELSE 0 END) AS personal,
                  SUM(CASE WHEN usage='work' THEN amount_cents ELSE 0 END) AS work
           FROM entries
           WHERE kind='expense' AND exclude_stats=0 AND occurred_at >= ? AND occurred_at < ?
           GROUP BY d ORDER BY d""",
        (start, end),
    )]

    return {
        "month": month,
        "today": today.isoformat(),
        "personal_cents": cur["personal"],
        "personal_count": cur["personal_n"],
        "nobudget_cents": cur["nobudget"],
        "nobudget_count": cur["nobudget_n"],
        "fixed_cents": cur["fixed"],
        "fixed_count": cur["fixed_n"],
        "work_cents": cur["work"],
        "work_count": cur["work_n"],
        "income_cents": cur["income"],
        "expense_cents": cur["expense"],
        "prev_personal_cents": old["personal"],
        "prev_work_cents": old["work"],
        "prev_income_cents": old["income"],
        "categories": [
            {"id": c["id"], "name": c["name"], "icon": c["icon"], "color": c["color"],
             "amount_cents": c["amount_cents"],
             "share": round(c["amount_cents"] / cur["personal"], 4) if cur["personal"] else 0}
            for c in cats
        ],
        "trend": trend,
        "daily": daily,
        "recent": recent,
        "work_recent": work_recent,
    }


# --------------------------------------------------------------------------- entries

@router.get("/entries")
def list_entries(
    month: str = "",
    date_from: str = "",
    date_to: str = "",
    usage: str = "",
    status: str = "",
    kind: str = "",
    category_id: Optional[int] = None,
    account_id: Optional[int] = None,
    q: str = "",
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    con=Depends(get_con),
):
    where, params = ["1=1"], []
    if month and month != "all":
        if not MONTH_RE.match(month):
            raise HTTPException(422, "月份格式应为 YYYY-MM")
        where.append("substr(e.occurred_at,1,7) = ?")
        params.append(month)
    if date_from:
        where.append("e.occurred_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("e.occurred_at < ?")
        params.append(date_to + " 99")
    if kind:
        where.append("e.kind = ?")
        params.append(kind)
    if account_id:
        where.append("e.account_id = ?")
        params.append(account_id)
    if category_id:
        where.append("(e.category_id = ? OR c.parent_id = ?)")
        params += [category_id, category_id]
    if q:
        where.append("(e.note LIKE ? OR e.project LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if usage in ("personal", "work"):
        where.append("e.usage = ?")
        params.append(usage)
    if status in ("work", "reimbursed", "pending", "draft", "submitted"):
        where.append("e.usage = 'work' AND e.kind = 'expense'")
    elif status == "personal":
        where.append("e.usage='personal'")

    w = " AND ".join(where)
    total = con.execute(
        f"""SELECT COUNT(*) FROM entries e
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE {w}""",
        params,
    ).fetchone()[0]
    rows = con.execute(
        ENTRY_SQL + f" WHERE {w} ORDER BY e.occurred_at DESC, e.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    sums = con.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN e.kind='expense' THEN e.amount_cents END),0) AS expense,
                   COALESCE(SUM(CASE WHEN e.kind='income' THEN e.amount_cents END),0) AS income
            FROM entries e
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE {w}""",
        params,
    ).fetchone()

    return {
        "items": [entry_dict(r) for r in rows],
        "total": total,
        "expense_cents": sums["expense"],
        "income_cents": sums["income"],
    }


def _validate_time(s: str) -> str:
    if not TIME_RE.match(s or ""):
        raise HTTPException(422, "时间格式应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM")
    return s if len(s) == 16 else s + " 00:00"


@router.post("/entries", status_code=201)
def create_entry(body: EntryBody, con=Depends(get_con)):
    occurred = _validate_time(body.occurred_at)
    now = db.now_iso()
    cur = con.execute(
        """INSERT INTO entries(kind, occurred_at, amount_cents, category_id, account_id, note,
               usage, project, invoice, invoice_no, exclude_stats, exclude_daily,
               exclude_budget, source, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'manual', ?, ?)""",
        (
            body.kind, occurred, int(round(body.amount * 100)), body.category_id,
            body.account_id, body.note.strip(), body.usage,
            body.project.strip(), body.invoice, body.invoice_no.strip(),
            int(body.exclude_stats), int(body.exclude_daily), int(body.exclude_budget), now, now,
        ),
    )
    con.commit()
    row = con.execute(ENTRY_SQL + " WHERE e.id = ?", (cur.lastrowid,)).fetchone()
    return entry_dict(row)


@router.patch("/entries/{entry_id}")
def patch_entry(entry_id: int, body: EntryPatch, con=Depends(get_con)):
    row = con.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not row:
        raise HTTPException(404, "账目不存在")
    sets, params = [], []
    data = body.model_dump(exclude_unset=True)

    if "occurred_at" in data and data["occurred_at"]:
        data["occurred_at"] = _validate_time(data["occurred_at"])
    if "amount" in data and data["amount"] is not None:
        sets.append("amount_cents = ?")
        params.append(int(round(data["amount"] * 100)))
    for field in ("kind", "occurred_at", "category_id", "account_id", "note", "usage",
                  "project", "invoice", "invoice_no"):
        if field in data and data[field] is not None:
            sets.append(f"{field} = ?")
            params.append(data[field].strip() if isinstance(data[field], str) else data[field])
    for field in ("exclude_stats", "exclude_daily", "exclude_budget"):
        if field in data and data[field] is not None:
            sets.append(f"{field} = ?")
            params.append(int(data[field]))

    if not sets:
        return entry_dict(con.execute(ENTRY_SQL + " WHERE e.id = ?", (entry_id,)).fetchone())

    sets.append("updated_at = ?")
    params.append(db.now_iso())
    params.append(entry_id)
    con.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id = ?", params)
    con.commit()
    return entry_dict(con.execute(ENTRY_SQL + " WHERE e.id = ?", (entry_id,)).fetchone())


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, con=Depends(get_con)):
    cur = con.execute("DELETE FROM entries WHERE id=?", (entry_id,))
    con.commit()
    if not cur.rowcount:
        raise HTTPException(404, "账目不存在")
    return {"ok": True}


@router.post("/entries/bulk")
def bulk_entries(body: BulkBody, con=Depends(get_con)):
    if not body.ids:
        return {"updated": 0}
    placeholders = ",".join("?" * len(body.ids))
    sets, params = [], []
    if body.usage:
        sets.append("usage = ?")
        params.append(body.usage)
    if body.project is not None:
        sets.append("project = ?")
        params.append(body.project)
    if body.category_id is not None:
        sets.append("category_id = ?")
        params.append(body.category_id)
    if body.exclude_stats is not None:
        sets.append("exclude_stats = ?")
        params.append(int(body.exclude_stats))
    if body.exclude_daily is not None:
        sets.append("exclude_daily = ?")
        params.append(int(body.exclude_daily))
    if body.exclude_budget is not None:
        sets.append("exclude_budget = ?")
        params.append(int(body.exclude_budget))
    if not sets:
        return {"updated": 0}
    sets.append("updated_at = ?")
    params.append(db.now_iso())
    con.execute(
        f"UPDATE entries SET {', '.join(sets)} WHERE id IN ({placeholders})",
        params + body.ids,
    )
    con.commit()
    return {"updated": len(body.ids)}


@router.get("/rules")
def get_rules(con=Depends(get_con)):
    return rules.load(con)


@router.put("/rules")
def put_rules(body: RulesBody, con=Depends(get_con)):
    data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    return rules.save(con, data)


@router.post("/rules/reset")
def reset_rules(con=Depends(get_con)):
    return rules.save(con, rules.defaults())


@router.post("/entries/apply-work-rules")
def apply_work_rules(body: ApplyRulesBody, con=Depends(get_con)):
    cfg = rules.load(con)
    rows = con.execute(
        """SELECT e.id, e.note,
                  COALESCE(p.name, c.name) AS parent,
                  CASE WHEN c.parent_id IS NOT NULL THEN c.name ELSE '' END AS child
           FROM entries e
           LEFT JOIN categories c ON c.id = e.category_id
           LEFT JOIN categories p ON p.id = c.parent_id
           WHERE e.kind = 'expense' AND e.usage = 'personal'"""
    ).fetchall()
    ids = [
        r["id"] for r in rows
        if rules.is_work(r["parent"] or "", r["child"] or "",
                         " ".join([r["note"] or "", r["parent"] or "", r["child"] or ""]),
                         cfg=cfg)
    ]
    if not body.dry_run and ids:
        now = db.now_iso()
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            con.execute(
                f"UPDATE entries SET usage='work', updated_at=? "
                f"WHERE id IN ({','.join('?' * len(chunk))})",
                [now] + chunk,
            )
        con.commit()
    return {"matched": len(ids), "updated": 0 if body.dry_run else len(ids)}


@router.post("/entries/apply-daily-rules")
def apply_daily_rules(body: ApplyRulesBody, con=Depends(get_con)):
    cfg = rules.load(con)
    rows = con.execute(
        """SELECT e.id, e.note,
                  COALESCE(p.name, c.name) AS parent,
                  CASE WHEN c.parent_id IS NOT NULL THEN c.name ELSE '' END AS child
           FROM entries e
           LEFT JOIN categories c ON c.id = e.category_id
           LEFT JOIN categories p ON p.id = c.parent_id
           WHERE e.kind = 'expense' AND e.usage = 'personal'
             AND e.exclude_daily = 0 AND e.exclude_stats = 0"""
    ).fetchall()
    ids = []
    for r in rows:
        text = " ".join([r["note"] or "", r["parent"] or "", r["child"] or ""])
        if rules.is_fixed(r["parent"] or "", r["child"] or "", text, cfg=cfg):
            ids.append(r["id"])
    if not body.dry_run and ids:
        now = db.now_iso()
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            con.execute(
                f"UPDATE entries SET exclude_daily=1, updated_at=? "
                f"WHERE id IN ({','.join('?' * len(chunk))})",
                [now] + chunk,
            )
        con.commit()
    return {"matched": len(ids), "updated": 0 if body.dry_run else len(ids)}


@router.post("/entries/delete")
def bulk_delete(body: IdsBody, con=Depends(get_con)):
    if not body.ids:
        return {"deleted": 0}
    placeholders = ",".join("?" * len(body.ids))
    cur = con.execute(f"DELETE FROM entries WHERE id IN ({placeholders})", body.ids)
    con.commit()
    return {"deleted": cur.rowcount}


# --------------------------------------------------------------------------- categories & accounts

@router.get("/categories")
def list_categories(con=Depends(get_con)):
    rows = con.execute(
        """SELECT c.*, COUNT(e.id) AS used
           FROM categories c LEFT JOIN entries e
             ON e.category_id = c.id OR e.category_id IN (SELECT id FROM categories WHERE parent_id = c.id)
           GROUP BY c.id ORDER BY c.sort, c.id"""
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/categories", status_code=201)
def create_category(body: NamedBody, con=Depends(get_con)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "分类名不能为空")
    try:
        cid = db._insert_category(con, name, body.parent_id, body.icon, body.color, 99)
        con.commit()
    except Exception:
        raise HTTPException(409, "同名分类已存在")
    return dict(con.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone())


@router.patch("/categories/{cat_id}")
def patch_category(cat_id: int, body: NamedPatch, con=Depends(get_con)):
    if not con.execute("SELECT 1 FROM categories WHERE id=?", (cat_id,)).fetchone():
        raise HTTPException(404, "分类不存在")
    sets, params = [], []
    for field in ("name", "icon", "color"):
        val = getattr(body, field)
        if val is not None:
            sets.append(f"{field} = ?")
            params.append(val.strip())
    if body.archived is not None:
        sets.append("archived = ?")
        params.append(int(body.archived))
    if sets:
        params.append(cat_id)
        con.execute(f"UPDATE categories SET {', '.join(sets)} WHERE id = ?", params)
        con.commit()
    return dict(con.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone())


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, con=Depends(get_con)):
    used = con.execute("SELECT COUNT(*) FROM entries WHERE category_id=?", (cat_id,)).fetchone()[0]
    if used:
        con.execute("UPDATE categories SET archived = 1 WHERE id=?", (cat_id,))
        con.commit()
        return {"archived": True}
    con.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    con.commit()
    return {"archived": False}


def _flow_map(con, as_of: str = "9999-12-31"):
    flows: dict[int, int] = {}
    for r in con.execute(
        """SELECT account_id AS id,
                  SUM(CASE WHEN kind='income' THEN amount_cents ELSE -amount_cents END) AS v
           FROM entries
           WHERE account_id IS NOT NULL AND occurred_at < ?
           GROUP BY account_id""",
        (as_of,),
    ):
        flows[r["id"]] = r["v"]
    for r in con.execute(
        "SELECT from_account_id AS id, -SUM(amount_cents) AS v FROM transfers "
        "WHERE from_account_id IS NOT NULL AND date < ? GROUP BY from_account_id",
        (as_of,),
    ):
        flows[r["id"]] = flows.get(r["id"], 0) + r["v"]
    for r in con.execute(
        "SELECT to_account_id AS id, SUM(amount_cents) AS v FROM transfers "
        "WHERE to_account_id IS NOT NULL AND date < ? GROUP BY to_account_id",
        (as_of,),
    ):
        flows[r["id"]] = flows.get(r["id"], 0) + r["v"]
    return flows


def _balance_map(con, as_of: str = "9999-12-31") -> dict[int, int]:
    flows = _flow_map(con, as_of)
    out = {}
    for a in con.execute("SELECT id, opening_cents FROM accounts"):
        out[a["id"]] = a["opening_cents"] + flows.get(a["id"], 0)
    return out


def _account_item(r, balance_cents: int) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "kind": r["kind"],
        "type": r["type"],
        "color": r["color"],
        "archived": r["archived"],
        "sort": r["sort"],
        "used": r["used"] if "used" in r.keys() else 0,
        "opening_cents": r["opening_cents"],
        "balance_cents": balance_cents,
        "balance": yuan(balance_cents),
    }


@router.get("/accounts")
def list_accounts(con=Depends(get_con)):
    balances = _balance_map(con)
    rows = con.execute(
        """SELECT a.*, (SELECT COUNT(*) FROM entries e WHERE e.account_id = a.id) AS used
           FROM accounts a ORDER BY a.sort, a.id"""
    ).fetchall()
    return {"items": [_account_item(r, balances.get(r["id"], 0)) for r in rows]}


@router.get("/accounts/{acc_id}/detail")
def account_detail(acc_id: int, con=Depends(get_con)):
    row = con.execute("SELECT * FROM accounts WHERE id=?", (acc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "账户不存在")
    balance = _balance_map(con).get(acc_id, 0)
    income = con.execute(
        "SELECT COALESCE(SUM(amount_cents),0) FROM entries WHERE account_id=? AND kind='income'",
        (acc_id,),
    ).fetchone()[0]
    expense = con.execute(
        "SELECT COALESCE(SUM(amount_cents),0) FROM entries WHERE account_id=? AND kind='expense'",
        (acc_id,),
    ).fetchone()[0]
    transfer_in = con.execute(
        "SELECT COALESCE(SUM(amount_cents),0) FROM transfers WHERE to_account_id=?", (acc_id,)
    ).fetchone()[0]
    transfer_out = con.execute(
        "SELECT COALESCE(SUM(amount_cents),0) FROM transfers WHERE from_account_id=?", (acc_id,)
    ).fetchone()[0]
    used = con.execute("SELECT COUNT(*) FROM entries WHERE account_id=?", (acc_id,)).fetchone()[0]
    return {
        "account": _account_item(dict(row) | {"used": used}, balance),
        "opening_cents": row["opening_cents"],
        "income_cents": income,
        "expense_cents": expense,
        "transfer_in_cents": transfer_in,
        "transfer_out_cents": transfer_out,
        "balance_cents": balance,
        "entry_count": used,
    }


@router.post("/accounts", status_code=201)
def create_account(body: AccountBody, con=Depends(get_con)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "账户名不能为空")
    opening = int(round((body.current_balance or 0) * 100))
    try:
        cur = con.execute(
            "INSERT INTO accounts(name, kind, type, opening_cents, color, sort) "
            "VALUES (?, ?, ?, ?, ?, 99)",
            (name, body.kind, body.type, opening, body.color),
        )
        con.commit()
    except Exception:
        raise HTTPException(409, "同名账户已存在")
    row = con.execute(
        """SELECT a.*, 0 AS used FROM accounts a WHERE a.id = ?""", (cur.lastrowid,)
    ).fetchone()
    return _account_item(row, opening)


@router.patch("/accounts/{acc_id}")
def patch_account(acc_id: int, body: AccountPatch, con=Depends(get_con)):
    row = con.execute("SELECT * FROM accounts WHERE id=?", (acc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "账户不存在")
    sets, params = [], []
    for field in ("name", "kind", "type", "color"):
        val = getattr(body, field)
        if val is not None:
            sets.append(f"{field} = ?")
            params.append(val.strip() if isinstance(val, str) else val)
    if body.archived is not None:
        sets.append("archived = ?")
        params.append(int(body.archived))
    if body.current_balance is not None:
        target = int(round(body.current_balance * 100))
        flows = _flow_map(con).get(acc_id, 0)
        sets.append("opening_cents = ?")
        params.append(target - flows)
    if sets:
        params.append(acc_id)
        con.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", params)
        con.commit()
    balances = _balance_map(con)
    row = con.execute(
        """SELECT a.*, (SELECT COUNT(*) FROM entries e WHERE e.account_id = a.id) AS used
           FROM accounts a WHERE a.id = ?""", (acc_id,)
    ).fetchone()
    return _account_item(row, balances.get(acc_id, 0))


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: int, con=Depends(get_con)):
    bal = _balance_map(con).get(acc_id, 0)
    if bal != 0:
        raise HTTPException(422, "账户还有余额，先转账或校准到 0 再删除")
    used = con.execute("SELECT COUNT(*) FROM entries WHERE account_id=?", (acc_id,)).fetchone()[0]
    used += con.execute(
        "SELECT COUNT(*) FROM transfers WHERE from_account_id=? OR to_account_id=?", (acc_id, acc_id)
    ).fetchone()[0]
    if used:
        con.execute("UPDATE accounts SET archived = 1 WHERE id=?", (acc_id,))
        con.commit()
        return {"archived": True}
    con.execute("DELETE FROM accounts WHERE id=?", (acc_id,))
    con.commit()
    return {"archived": False}


# --------------------------------------------------------------------------- transfers

@router.get("/transfers")
def list_transfers(month: str = "", limit: int = Query(default=50, le=200), con=Depends(get_con)):
    where, params = ["1=1"], []
    if month and month != "all":
        if not MONTH_RE.match(month):
            raise HTTPException(422, "月份格式应为 YYYY-MM")
        where.append("substr(t.date,1,7) = ?")
        params.append(month)
    rows = con.execute(
        f"""SELECT t.*, fa.name AS from_name, ta.name AS to_name
            FROM transfers t
            LEFT JOIN accounts fa ON fa.id = t.from_account_id
            LEFT JOIN accounts ta ON ta.id = t.to_account_id
            WHERE {' AND '.join(where)}
            ORDER BY t.date DESC, t.id DESC LIMIT ?""",
        params + [limit],
    ).fetchall()
    return {"items": [dict(r) | {"amount": yuan(r["amount_cents"])} for r in rows]}


@router.post("/transfers", status_code=201)
def create_transfer(body: TransferBody, con=Depends(get_con)):
    if body.from_account_id == body.to_account_id:
        raise HTTPException(422, "转出和转入账户不能相同")
    ids = {r["id"] for r in con.execute("SELECT id FROM accounts WHERE archived = 0")}
    if body.from_account_id not in ids or body.to_account_id not in ids:
        raise HTTPException(404, "账户不存在")
    if not TIME_RE.match(body.date or ""):
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD")
    now = db.now_iso()
    cur = con.execute(
        """INSERT INTO transfers(date, from_account_id, to_account_id, amount_cents, note,
                                 created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (body.date[:10], body.from_account_id, body.to_account_id,
         int(round(body.amount * 100)), body.note.strip(), now, now),
    )
    con.commit()
    row = con.execute(
        """SELECT t.*, fa.name AS from_name, ta.name AS to_name
           FROM transfers t LEFT JOIN accounts fa ON fa.id=t.from_account_id
           LEFT JOIN accounts ta ON ta.id=t.to_account_id WHERE t.id=?""",
        (cur.lastrowid,),
    ).fetchone()
    return dict(row) | {"amount": yuan(row["amount_cents"])}


@router.delete("/transfers/{transfer_id}")
def delete_transfer(transfer_id: int, con=Depends(get_con)):
    cur = con.execute("DELETE FROM transfers WHERE id=?", (transfer_id,))
    con.commit()
    if not cur.rowcount:
        raise HTTPException(404, "转账记录不存在")
    return {"ok": True}


# --------------------------------------------------------------------------- net worth

@router.get("/networth")
def networth(con=Depends(get_con)):
    balances = _balance_map(con)
    accounts = []
    rows = con.execute(
        """SELECT a.*, (SELECT COUNT(*) FROM entries e WHERE e.account_id = a.id) AS used
           FROM accounts a WHERE a.archived = 0 ORDER BY a.sort, a.id"""
    )
    for r in rows:
        accounts.append(_account_item(r, balances.get(r["id"], 0)))

    assets = sum(a["balance_cents"] for a in accounts if a["type"] != "liability")
    debt = sum(-a["balance_cents"] for a in accounts if a["type"] == "liability")
    net = assets - debt

    today = date.today()
    months = []
    m = today.strftime("%Y-%m")
    for _ in range(12):
        months.append(m)
        m = shift_month(m, -1)[:7]
    months.reverse()

    start = months[0] + "-01"
    flows = {
        r["m"]: r
        for r in con.execute(
            """SELECT substr(occurred_at, 1, 7) AS m,
                      SUM(CASE WHEN kind='income' AND exclude_stats=0 THEN amount_cents ELSE 0 END) AS income,
                      SUM(CASE WHEN kind='expense' AND exclude_stats=0 THEN amount_cents ELSE 0 END) AS expense
               FROM entries WHERE occurred_at >= ? GROUP BY m""",
            (start,),
        )
    }
    trend = []
    for m in months:
        r = flows.get(m)
        income = r["income"] if r else 0
        expense = r["expense"] if r else 0
        trend.append({
            "month": m,
            "income_cents": income,
            "expense_cents": expense,
            "change_cents": income - expense,
        })

    return {
        "net_cents": net,
        "assets_cents": assets,
        "liabilities_cents": debt,
        "month_change_cents": trend[-1]["change_cents"] if trend else 0,
        "accounts": accounts,
        "trend": trend,
        "transfers": list_transfers(month="", limit=5, con=con)["items"],
    }


# --------------------------------------------------------------------------- budgets

def _month_bounds(month: str) -> tuple[str, str]:
    if month and month != "all":
        return parse_month(month)
    start = date.today().strftime("%Y-%m-01")
    return start, shift_month(start[:7], 1)


@router.get("/budget")
def get_budget(month: str = "", con=Depends(get_con)):
    start, end = _month_bounds(month)
    rows = con.execute(
        """SELECT p.id AS category_id, p.name, p.icon, p.color,
                  COALESCE(SUM(e.amount_cents), 0) AS spent_cents
           FROM entries e
           JOIN categories c ON c.id = e.category_id
           JOIN categories p ON p.id = COALESCE(c.parent_id, c.id)
           WHERE e.kind = 'expense' AND e.usage = 'personal' AND e.exclude_budget = 0
             AND e.exclude_daily = 0
             AND e.occurred_at >= ? AND e.occurred_at < ?
           GROUP BY p.id""",
        (start, end),
    ).fetchall()
    spent_by_cat = {r["category_id"]: r["spent_cents"] for r in rows}
    total_spent = con.execute(
        """SELECT COALESCE(SUM(amount_cents), 0) AS s FROM entries e
           WHERE e.kind = 'expense' AND e.usage = 'personal' AND e.exclude_budget = 0
             AND e.exclude_daily = 0
             AND e.occurred_at >= ? AND e.occurred_at < ?""",
        (start, end),
    ).fetchone()["s"]

    budgets = con.execute(
        """SELECT b.*, p.name, p.icon, p.color
           FROM budgets b LEFT JOIN categories p ON p.id = b.category_id"""
    ).fetchall()
    total_budget = 0
    items = []
    for b in budgets:
        if b["category_id"] is None:
            total_budget = b["amount_cents"]
            continue
        spent = spent_by_cat.get(b["category_id"], 0)
        items.append({
            "category_id": b["category_id"],
            "name": b["name"] or "未分类",
            "icon": b["icon"] or "🧾",
            "color": b["color"] or "#94A3B8",
            "budget_cents": b["amount_cents"],
            "spent_cents": spent,
            "remaining_cents": b["amount_cents"] - spent,
            "pct": round(spent / b["amount_cents"], 4) if b["amount_cents"] else 0,
        })
    items.sort(key=lambda x: x["pct"], reverse=True)

    today = date.today()
    month_key = start[:7]
    current_key = today.strftime("%Y-%m")
    y, m = int(month_key[:4]), int(month_key[5:7])
    days_in_month = monthrange(y, m)[1]
    is_current = month_key == current_key
    is_future = month_key > current_key
    elapsed = today.day if is_current else (0 if is_future else days_in_month)
    remaining_days = (days_in_month - today.day + 1) if is_current else (days_in_month if is_future else 0)
    daily_budget = round(total_budget / days_in_month) if total_budget else 0
    daily_spent = round(total_spent / elapsed) if elapsed else 0
    today_spent = 0
    if is_current:
        today_spent = con.execute(
            """SELECT COALESCE(SUM(amount_cents), 0) AS s FROM entries e
               WHERE e.kind = 'expense' AND e.usage = 'personal' AND e.exclude_budget = 0
                 AND e.exclude_daily = 0
                 AND e.occurred_at >= ? AND e.occurred_at < ?""",
            (today.isoformat(), (today + timedelta(days=1)).isoformat()),
        ).fetchone()["s"]
    remaining_cents = total_budget - total_spent
    remaining_daily = (
        round(remaining_cents / remaining_days)
        if total_budget and remaining_days and remaining_cents > 0 else 0
    )

    budgeted_ids = {i["category_id"] for i in items}
    suggestions = [
        {"category_id": r["category_id"], "name": r["name"], "icon": r["icon"],
         "color": r["color"], "spent_cents": r["spent_cents"]}
        for r in rows if r["category_id"] not in budgeted_ids
    ]
    suggestions.sort(key=lambda x: x["spent_cents"], reverse=True)

    return {
        "month": start[:7],
        "total": {
            "budget_cents": total_budget,
            "spent_cents": total_spent,
            "remaining_cents": remaining_cents,
            "pct": round(total_spent / total_budget, 4) if total_budget else 0,
            "daily": {
                "days_in_month": days_in_month,
                "elapsed_days": elapsed,
                "remaining_days": remaining_days,
                "budget_cents": daily_budget,
                "spent_cents": daily_spent,
                "today_spent_cents": today_spent,
                "today_remaining_cents": daily_budget - today_spent,
                "remaining_daily_cents": remaining_daily,
                "is_current": is_current,
                "is_future": is_future,
            },
        },
        "items": items,
        "suggestions": suggestions,
    }


@router.put("/budgets")
def put_budget(body: BudgetBody, con=Depends(get_con)):
    if body.category_id is not None:
        cat = con.execute("SELECT id, parent_id FROM categories WHERE id=?", (body.category_id,)).fetchone()
        if not cat:
            raise HTTPException(404, "分类不存在")
        if cat["parent_id"] is not None:
            raise HTTPException(422, "预算请设在一级分类上")
    amount = int(round(body.amount * 100))
    now = db.now_iso()
    row = con.execute(
        "SELECT id FROM budgets WHERE COALESCE(category_id, 0) = COALESCE(?, 0)",
        (body.category_id,),
    ).fetchone()
    if amount <= 0:
        if row:
            con.execute("DELETE FROM budgets WHERE id=?", (row["id"],))
            con.commit()
        return {"ok": True, "deleted": True}
    if row:
        con.execute(
            "UPDATE budgets SET amount_cents=?, updated_at=? WHERE id=?",
            (amount, now, row["id"]),
        )
    else:
        con.execute(
            "INSERT INTO budgets(category_id, amount_cents, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (body.category_id, amount, now, now),
        )
    con.commit()
    return {"ok": True, "amount_cents": amount}


# --------------------------------------------------------------------------- import / export

@router.get("/import/suggestions")
def import_suggestions():
    return {"files": importer.guess_export_files()}


@router.post("/import/preview")
async def import_preview(
    request: Request, filename: str = Query(...), con=Depends(get_con)
):
    data = await request.body()
    try:
        parsed = importer.parse_file(data, filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    result = importer.preview(parsed["rows"], parsed, cfg=rules.load(con))
    result["filename"] = filename
    result["sample"] = parsed["rows"][:6]
    return result


@router.post("/import")
async def import_file(
    request: Request,
    filename: str = Query(...),
    mode: Literal["merge", "replace"] = "merge",
    con=Depends(get_con),
):
    data = await request.body()
    try:
        parsed = importer.parse_file(data, filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return importer.import_rows(con, parsed["rows"], mode, parsed["source"])


class PathPreviewBody(BaseModel):
    path: str


BACKUP_TABLES = {"entries", "categories", "accounts", "transfers", "budgets", "settings"}


def _inspect_backup(path: Path) -> dict:
    try:
        con = sqlite3.connect(str(path))
        con.execute("PRAGMA query_only = ON")
    except sqlite3.DatabaseError as exc:
        raise HTTPException(422, f"不是有效的 SQLite 文件：{exc}")
    try:
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            missing = BACKUP_TABLES - tables
            if missing:
                raise HTTPException(422, "不像是糊涂账的备份（缺少数据表）")
            cols = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
            if "amount_cents" not in cols:
                raise HTTPException(422, "备份结构不兼容（账目表缺少 amount_cents）")
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise HTTPException(422, f"备份文件损坏：{integrity}")
            counts = {
                "entries": con.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
                "accounts": con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
                "transfers": con.execute("SELECT COUNT(*) FROM transfers").fetchone()[0],
                "budgets": con.execute("SELECT COUNT(*) FROM budgets").fetchone()[0],
            }
        except sqlite3.DatabaseError as exc:
            raise HTTPException(422, f"不是有效的 SQLite 备份：{exc}")
    finally:
        con.close()
    return counts


def _restore_temp(data: bytes) -> Path:
    folder = db.APP_SUPPORT
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="restore-", suffix=".db", dir=folder)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return Path(name)


@router.post("/import/preview-path")
def import_preview_path(body: PathPreviewBody, con=Depends(get_con)):
    try:
        data = open(body.path, "rb").read()
        parsed = importer.parse_file(data, body.path)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc))
    result = importer.preview(parsed["rows"], parsed, cfg=rules.load(con))
    result["filename"] = body.path.split("/")[-1]
    result["sample"] = parsed["rows"][:6]
    return result


@router.post("/import/path")
def import_path(body: PathImportBody, con=Depends(get_con)):
    p = body.path
    try:
        data = open(p, "rb").read()
        parsed = importer.parse_file(data, p)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc))
    return importer.import_rows(con, parsed["rows"], body.mode, parsed["source"])


@router.post("/import/backup/preview")
async def backup_preview(request: Request, filename: str = Query("backup.db")):
    data = await request.body()
    if not data:
        raise HTTPException(422, "文件为空")
    tmp = _restore_temp(data)
    try:
        counts = _inspect_backup(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return {"kind": "backup", "filename": filename, "size": len(data), **counts}


@router.post("/import/backup/preview-path")
def backup_preview_path(body: PathPreviewBody):
    try:
        data = open(body.path, "rb").read()
    except OSError as exc:
        raise HTTPException(422, str(exc))
    tmp = _restore_temp(data)
    try:
        counts = _inspect_backup(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return {"kind": "backup", "filename": body.path.split("/")[-1], "size": len(data), **counts}


@router.post("/import/backup")
async def import_backup(request: Request, con=Depends(get_con)):
    data = await request.body()
    if not data:
        raise HTTPException(422, "文件为空")
    tmp = _restore_temp(data)
    try:
        counts = _inspect_backup(tmp)
        saved = backup_db()
        src = sqlite3.connect(str(tmp))
        try:
            src.backup(con)
        finally:
            src.close()
        con.commit()
        db.init_db()
        after = _inspect_backup(db.DB_PATH)
    finally:
        tmp.unlink(missing_ok=True)
    return {"restored": True, "counts": after, "backup_path": saved["path"]}


STATUS_LABEL = {"work": "报销", "personal": "日常", "income": "收入"}
SOURCE_LABEL = {"manual": "手动", "import": "导入", "reconcile": "糊涂账"}


def _export_dir() -> Path:
    return Path(
        os.environ.get("HUTUZHANG_EXPORT_DIR")
        or os.environ.get("JINGZHI_EXPORT_DIR")
        or (Path.home() / "Downloads")
    )


def _export_rows(scope: str, month: str, year: str, con):
    where, params = ["1=1"], []
    if scope == "month":
        where.append("substr(e.occurred_at,1,7) = ?")
        params.append(month)
    elif scope == "year":
        where.append("substr(e.occurred_at,1,4) = ?")
        params.append(year)
    return con.execute(
        ENTRY_SQL + f" WHERE {' AND '.join(where)} ORDER BY e.occurred_at", params
    ).fetchall()


def _csv_text(rows) -> str:
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["日期", "时间", "收支", "金额", "分类", "账户", "用途", "标记",
                "项目", "备注", "不计入收支", "不计入日常", "不计入预算", "来源"])
    for r in rows:
        d = entry_dict(r)
        when = d["occurred_at"] or ""
        day, _, clock = when.partition(" ")
        w.writerow([
            day, clock, "收入" if d["kind"] == "income" else "支出",
            f"{d['amount']:.2f}", d["category_label"], d["account_name"],
            "报销" if d["usage"] == "work" else "日常",
            STATUS_LABEL.get(d["status"], d["status"]),
            d["project"], d["note"], "是" if d["exclude_stats"] else "",
            "是" if d["exclude_daily"] else "", "是" if d["exclude_budget"] else "",
            SOURCE_LABEL.get(d["source"], d["source"]),
        ])
    return buf.getvalue()


def _save_text(text: str, prefix: str, suffix: str) -> Path:
    folder = _export_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"{prefix}-{stamp}{suffix}"
    n = 2
    while path.exists():
        path = folder / f"{prefix}-{stamp}-{n}{suffix}"
        n += 1
    path.write_text(text, encoding="utf-8")
    return path


@router.post("/export")
def export_csv(body: "ExportBody", con=Depends(get_con)):
    scope = body.scope
    month = body.month or date.today().strftime("%Y-%m")
    year = body.year or date.today().strftime("%Y")
    if scope == "month" and not MONTH_RE.match(month):
        raise HTTPException(422, "月份格式应为 YYYY-MM")
    if scope == "year" and not re.match(r"^\d{4}$", year):
        raise HTTPException(422, "年份格式应为 YYYY")
    rows = _export_rows(scope, month, year, con)
    label = "全部" if scope == "all" else (month if scope == "month" else year)
    path = _save_text(_csv_text(rows), f"糊涂账-{label}", ".csv")
    return {
        "path": str(path),
        "filename": path.name,
        "count": len(rows),
        "scope": scope,
        "size": path.stat().st_size,
    }


@router.get("/export.csv")
def export_csv_download(month: str = "", year: str = "", con=Depends(get_con)):
    if month and month != "all":
        scope, label = "month", month
        rows = _export_rows("month", month, "", con)
    elif year:
        scope, label = "year", year
        rows = _export_rows("year", "", year, con)
    else:
        scope, label = "all", "全部"
        rows = _export_rows("all", "", "", con)
    text = _csv_text(rows)
    filename = f"糊涂账-{label}.csv"
    headers = {
        "Content-Disposition": (
            'attachment; filename="hutuzhang.csv"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
    }
    return Response(text, media_type="text/csv; charset=utf-8", headers=headers)


class RevealBody(BaseModel):
    path: str


@router.post("/export/reveal")
def export_reveal(body: RevealBody):
    p = Path(body.path).resolve()
    folder = _export_dir().resolve()
    try:
        p.relative_to(folder)
    except ValueError:
        raise HTTPException(422, "只能打开导出目录里的文件")
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    subprocess.run(["open", "-R", str(p)], check=False)
    return {"ok": True}


@router.post("/backup")
def backup_db():
    db.init_db()
    folder = _export_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"糊涂账-备份-{stamp}.db"
    n = 2
    while path.exists():
        path = folder / f"糊涂账-备份-{stamp}-{n}.db"
        n += 1
    src = db.connect()
    dst = sqlite3.connect(str(path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return {"path": str(path), "filename": path.name, "size": path.stat().st_size}


# --------------------------------------------------------------------------- reconcile

@router.post("/reconcile")
def reconcile(body: ReconcileBody, con=Depends(get_con)):
    """Set each account to its real balance and record the differences."""
    balances = _balance_map(con)
    accounts = {
        r["id"]: r for r in con.execute("SELECT * FROM accounts WHERE archived = 0")
    }
    other_id = con.execute(
        "SELECT id FROM categories WHERE parent_id IS NULL AND name='其他'"
    ).fetchone()
    income_id = con.execute(
        "SELECT id FROM categories WHERE parent_id IS NULL AND name='收入'"
    ).fetchone()

    now = db.now_iso()
    occurred = date.today().isoformat() + " " + datetime.now().strftime("%H:%M")
    adjustments = []

    for acc_id, actual in body.balances.items():
        acc = accounts.get(int(acc_id))
        if not acc:
            continue
        before = balances.get(acc["id"], 0)
        target = int(round(float(actual) * 100))
        diff = target - before
        if diff == 0:
            continue
        kind = "income" if diff > 0 else "expense"
        note = (
            f"糊涂账调整 · {acc['name']}：原余额 ¥{before / 100:.2f} → "
            f"实际 ¥{target / 100:.2f}（{'补记' if diff > 0 else '冲减'} ¥{abs(diff) / 100:.2f}）"
        )
        if body.reason.strip():
            note += f"；说明：{body.reason.strip()}"
        cat = income_id if kind == "income" else other_id
        con.execute(
            """INSERT INTO entries(kind, occurred_at, amount_cents, category_id, account_id,
                   note, usage, project, invoice, invoice_no, exclude_stats, exclude_daily,
                   exclude_budget, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'personal', '', 'none', '', 1, 1, 1, 'reconcile', ?, ?)""",
            (
                kind, occurred, abs(diff), cat["id"] if cat else None, acc["id"],
                note, now, now,
            ),
        )
        adjustments.append({
            "account_id": acc["id"],
            "name": acc["name"],
            "before_cents": before,
            "after_cents": target,
            "diff_cents": diff,
        })

    con.commit()
    return {
        "adjusted": len(adjustments),
        "total_diff_cents": sum(a["diff_cents"] for a in adjustments),
        "adjustments": adjustments,
    }


# --------------------------------------------------------------------------- statistics

def _scope_clause(scope: str) -> str:
    if scope == "work":
        return " AND usage = 'work'"
    if scope == "personal":
        return " AND usage = 'personal' AND exclude_daily = 0 AND exclude_budget = 0"
    if scope == "nobudget":
        return " AND usage = 'personal' AND exclude_daily = 0 AND exclude_budget = 1"
    return ""


def _period_totals(con, start: str, end: str, scope: str):
    extra = _scope_clause(scope)
    return con.execute(
        f"""SELECT
              COALESCE(SUM(CASE WHEN kind='expense' AND exclude_stats=0{extra}
                           THEN amount_cents END), 0) AS expense,
              COALESCE(SUM(CASE WHEN kind='income' AND exclude_stats=0{extra}
                           THEN amount_cents END), 0) AS income,
              COUNT(CASE WHEN kind='expense' AND exclude_stats=0{extra} THEN 1 END) AS expense_n,
              COUNT(CASE WHEN kind='income' AND exclude_stats=0{extra} THEN 1 END) AS income_n
            FROM entries WHERE occurred_at >= ? AND occurred_at < ?""",
        (start, end),
    ).fetchone()


@router.get("/stats")
def stats(period: str = "month", key: str = "", scope: str = "personal", con=Depends(get_con)):
    period = period if period in ("month", "year") else "month"
    scope = scope if scope in ("personal", "nobudget", "work", "all") else "personal"
    today = date.today()
    extra = _scope_clause(scope)

    if period == "year":
        if not re.match(r"^\d{4}$", key or ""):
            key = today.strftime("%Y")
        year = int(key)
        start, end = f"{year}-01-01", f"{year + 1}-01-01"
        series_rows = con.execute(
            f"""SELECT substr(occurred_at, 6, 2) AS k,
                       COALESCE(SUM(CASE WHEN kind='expense' AND exclude_stats=0{extra}
                                    THEN amount_cents END), 0) AS expense,
                       COALESCE(SUM(CASE WHEN kind='income' AND exclude_stats=0{extra}
                                    THEN amount_cents END), 0) AS income
                FROM entries WHERE occurred_at >= ? AND occurred_at < ?
                GROUP BY k""",
            (start, end),
        ).fetchall()
        by_k = {r["k"]: r for r in series_rows}
        series = []
        for m in range(1, 13):
            r = by_k.get(f"{m:02d}")
            series.append({
                "label": f"{m}月",
                "expense_cents": r["expense"] if r else 0,
                "income_cents": r["income"] if r else 0,
            })
        prev_start, prev_end = f"{year - 1}-01-01", start
        asset_dates = [f"{year}-{m:02d}-01" for m in range(1, 13)]
        if year == today.year:
            asset_dates = [d for d in asset_dates if d <= today.strftime("%Y-%m-%d")]
        else:
            asset_dates = asset_dates
    else:
        if not MONTH_RE.match(key or ""):
            key = today.strftime("%Y-%m")
        start, end = parse_month(key)
        y, m = int(key[:4]), int(key[5:7])
        days = monthrange(y, m)[1]
        series_rows = con.execute(
            f"""SELECT substr(occurred_at, 9, 2) AS k,
                       COALESCE(SUM(CASE WHEN kind='expense' AND exclude_stats=0{extra}
                                    THEN amount_cents END), 0) AS expense,
                       COALESCE(SUM(CASE WHEN kind='income' AND exclude_stats=0{extra}
                                    THEN amount_cents END), 0) AS income
                FROM entries WHERE occurred_at >= ? AND occurred_at < ?
                GROUP BY k""",
            (start, end),
        ).fetchall()
        by_k = {r["k"]: r for r in series_rows}
        series = []
        for d in range(1, days + 1):
            r = by_k.get(f"{d:02d}")
            series.append({
                "label": str(d),
                "expense_cents": r["expense"] if r else 0,
                "income_cents": r["income"] if r else 0,
            })
        prev_start, prev_end = parse_month(shift_month(key, -1))
        asset_dates = []
        cur = date(y, m, 1)
        last_day = date(int(end[:4]), int(end[5:7]), 1)
        if key == today.strftime("%Y-%m"):
            last_day = min(last_day, today + timedelta(days=1))
        while cur < last_day:
            asset_dates.append(cur.isoformat())
            cur += timedelta(days=1)

    totals = _period_totals(con, start, end, scope)
    prev = _period_totals(con, prev_start, prev_end, scope)

    cats = con.execute(
        f"""SELECT COALESCE(p.id, 0) AS id,
                   COALESCE(p.name, '未分类') AS name,
                   COALESCE(p.icon, '🧾') AS icon,
                   COALESCE(p.color, '#94A3B8') AS color,
                   COALESCE(SUM(e.amount_cents), 0) AS amount_cents,
                   COUNT(*) AS n
            FROM entries e
            LEFT JOIN categories c ON c.id = e.category_id
            LEFT JOIN categories p ON p.id = COALESCE(c.parent_id, c.id)
            WHERE e.kind='expense' AND e.exclude_stats=0{_scope_clause(scope).replace('usage', 'e.usage')}
              AND e.occurred_at >= ? AND e.occurred_at < ?
            GROUP BY COALESCE(p.id, 0) HAVING amount_cents > 0
            ORDER BY amount_cents DESC""",
        (start, end),
    ).fetchall()
    expense_total = totals["expense"]
    categories = [
        {
            "id": r["id"], "name": r["name"], "icon": r["icon"], "color": r["color"],
            "amount_cents": r["amount_cents"], "count": r["n"],
            "share": round(r["amount_cents"] / expense_total, 4) if expense_total else 0,
        }
        for r in cats
    ]

    income_rows = con.execute(
        f"""SELECT COALESCE(c.id, p.id, 0) AS id,
                  COALESCE(c.name, p.name, '收入') AS name,
                  COALESCE(c.icon, p.icon, '💰') AS icon,
                  COALESCE(c.color, p.color, '#22C55E') AS color,
                  COALESCE(SUM(e.amount_cents), 0) AS amount_cents,
                  COUNT(*) AS n
           FROM entries e
           LEFT JOIN categories c ON c.id = e.category_id
           LEFT JOIN categories p ON p.id = c.parent_id
           WHERE e.kind='income' AND e.exclude_stats=0{_scope_clause(scope).replace('usage', 'e.usage')}
             AND e.occurred_at >= ? AND e.occurred_at < ?
           GROUP BY COALESCE(c.id, p.id, 0)
           HAVING amount_cents > 0
           ORDER BY amount_cents DESC""",
        (start, end),
    ).fetchall()
    income_total = sum(r["amount_cents"] for r in income_rows)
    income_categories = [
        {
            "id": r["id"], "name": r["name"], "icon": r["icon"], "color": r["color"],
            "amount_cents": r["amount_cents"], "count": r["n"],
            "share": round(r["amount_cents"] / income_total, 4) if income_total else 0,
        }
        for r in income_rows
    ]

    accounts = con.execute("SELECT id, type FROM accounts WHERE archived = 0").fetchall()
    asset_series = []
    for d in asset_dates:
        if period == "year":
            if d[:7] == today.strftime("%Y-%m"):
                as_of = (today + timedelta(days=1)).isoformat()
            else:
                as_of = (date.fromisoformat(d) + timedelta(days=32)).replace(day=1).isoformat()
        else:
            as_of = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
        bmap = _balance_map(con, as_of)
        assets = sum(bmap.get(a["id"], 0) for a in accounts if a["type"] != "liability")
        debt = sum(-bmap.get(a["id"], 0) for a in accounts if a["type"] == "liability")
        asset_series.append({
            "date": d,
            "assets_cents": assets,
            "liabilities_cents": debt,
            "net_cents": assets - debt,
        })

    return {
        "period": period,
        "key": key,
        "scope": scope,
        "start": start,
        "end": end,
        "summary": {
            "expense_cents": totals["expense"],
            "income_cents": totals["income"],
            "balance_cents": totals["income"] - totals["expense"],
            "expense_count": totals["expense_n"],
            "income_count": totals["income_n"],
        },
        "prev": {
            "expense_cents": prev["expense"],
            "income_cents": prev["income"],
            "balance_cents": prev["income"] - prev["expense"],
        },
        "series": series,
        "categories": categories,
        "income_categories": income_categories,
        "income_total_cents": income_total,
        "assets": asset_series,
    }


# --------------------------------------------------------------------------- settings & danger zone

@router.get("/settings")
def settings(con=Depends(get_con)):
    counts = con.execute(
        """SELECT (SELECT COUNT(*) FROM entries) AS entries,
                  (SELECT COUNT(*) FROM categories) AS categories,
                  (SELECT COUNT(*) FROM accounts) AS accounts"""
    ).fetchone()
    size = db.DB_PATH.stat().st_size if db.DB_PATH.exists() else 0
    return {
        "data_path": str(db.DB_PATH),
        "db_size": size,
        "counts": dict(counts),
        "version": "1.0.0",
    }


@router.post("/settings/reset")
def reset_data(con=Depends(get_con)):
    con.execute("DELETE FROM entries")
    con.commit()
    return {"ok": True}
