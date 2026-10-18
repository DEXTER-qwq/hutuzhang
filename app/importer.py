"""Import third-party bookkeeping exports and Alipay CSV."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from . import db, rules

ONE_WOOD_KEYS = ("日期", "金额", "收支类型")
ALIPAY_KEYS = ("交易时间", "金额", "收/支")

DEFAULT_ICON = "🧾"
DEFAULT_COLOR = "#94A3B8"

# 支付宝「交易分类」→ 糊涂账的一级分类
ALIPAY_CATEGORY_MAP = {
    "餐饮美食": "食品餐饮",
    "交通出行": "出行交通",
    "爱车养护": "出行交通",
    "日用百货": "购物消费",
    "服饰装扮": "购物消费",
    "数码电器": "购物消费",
    "美容美发": "购物消费",
    "母婴亲子": "购物消费",
    "宠物": "购物消费",
    "住房物业": "居家生活",
    "生活服务": "居家生活",
    "充值缴费": "居家生活",
    "水电煤": "居家生活",
    "医疗健康": "健康医疗",
    "教育培训": "文化教育",
    "文化休闲": "休闲娱乐",
    "运动户外": "休闲娱乐",
    "酒店旅游": "休闲娱乐",
    "转账红包": "送礼人情",
    "商业服务": "其他",
    "投资理财": "其他",
    "保险": "其他",
    "信用借还": "其他",
    "公益捐赠": "其他",
    "其他": "其他",
}

ALIPAY_OK_STATUS = {"交易成功", "等待确认收货", "交易完成", "退款成功", "部分退款成功"}

WECHAT_BAD_STATUS = ("失败", "撤销", "关闭", "取消", "过期")

WECHAT_CATEGORY_RULES = [
    ("出行交通", ("滴滴", "地铁", "公交", "铁路", "12306", "航空", "机票", "打车",
                  "出租车", "单车", "停车", "加油", "高速", "哈啰")),
    ("食品餐饮", ("美团", "饿了么", "肯德基", "麦当劳", "星巴克", "瑞幸", "餐饮", "饭店",
                  "餐厅", "烧烤", "火锅", "奶茶", "咖啡", "小吃", "面馆", "快餐", "食堂",
                  "烘焙", "蛋糕", "烤鸡", "小炒", "外卖", "食品")),
    ("购物消费", ("京东", "淘宝", "拼多多", "超市", "便利店", "盒马", "山姆", "优衣库",
                  "小米", "苹果", "华为", "商城", "旗舰店", "百货")),
    ("居家生活", ("电费", "水费", "燃气", "物业", "房租", "宽带", "话费", "家政", "维修")),
    ("健康医疗", ("药房", "药店", "医院", "诊所", "体检", "挂号", "口腔")),
    ("休闲娱乐", ("电影", "影城", "演出", "酒店", "民宿", "旅游", "门票", "景区",
                  "游戏", "KTV", "健身")),
    ("文化教育", ("书店", "图书", "课程", "培训", "考试", "教育")),
    ("送礼人情", ("转账", "红包", "礼金", "份子")),
]

# 支付宝自带的分类有时很粗（比如把美团外卖记成「文化休闲」），用商户/商品关键词纠偏
REFINE_RULES = [
    ("出行交通", ("滴滴", "高德", "花小猪", "地铁", "公交", "铁路", "12306", "航空", "机票",
                  "加油", "停车", "单车", "骑行", "哈啰", "打车", "出租车")),
    ("休闲娱乐", ("电影", "影城", "影院", "演出", "酒店", "民宿", "旅游", "门票", "景区",
                  "游戏", "KTV", "健身", "球馆", "剧本杀")),
    ("食品餐饮", ("美团", "饿了么", "肯德基", "麦当劳", "星巴克", "瑞幸", "餐饮", "饭店",
                  "餐厅", "烧烤", "火锅", "奶茶", "咖啡", "小吃", "面馆", "快餐", "食堂",
                  "烘焙", "蛋糕", "烤鸡", "小炒", "外卖")),
    ("购物消费", ("淘宝", "天猫", "京东", "拼多多", "超市", "便利店", "盒马", "山姆",
                  "优衣库", "小米", "苹果", "华为", "商城", "旗舰店")),
    ("居家生活", ("电费", "水费", "燃气", "物业", "房租", "宽带", "话费", "家政", "维修")),
    ("健康医疗", ("药房", "药店", "医院", "诊所", "体检", "挂号", "口腔")),
    ("文化教育", ("书店", "图书", "课程", "培训", "考试", "教育")),
]
# 这些分类足够具体，直接采信支付宝的归类
ALIPAY_SPECIFIC = {"餐饮美食", "交通出行", "医疗健康", "教育培训", "酒店旅游", "日用百货",
                   "服饰装扮", "数码电器", "充值缴费", "住房物业", "水电煤"}


def _norm(s) -> str:
    return "" if s is None else str(s).strip()


def _to_cents(value) -> int:
    s = _norm(value).replace("¥", "").replace("￥", "").replace(",", "")
    if not s:
        return 0
    try:
        return int(round(abs(float(s)) * 100))
    except ValueError:
        return 0


def _norm_time(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    s = _norm(value)
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", s)
    if not m:
        return ""
    y, mo, d, hh, mm = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d} {int(hh or 0):02d}:{mm or '00'}"


def _clean_item(s: str) -> str:
    s = _norm(s)
    s = re.sub(r"[，,\s]*订单编号[:：]\s*\S+", "", s)
    s = re.sub(r"[-—·\s]*\d{12,}\S*", "", s)          # 美团/淘宝嵌在商品名里的长订单号
    s = re.sub(r"[-—·\s]*(美团App|支付宝|微信支付)[-—·\s]*$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -—·，,")
    return s


def _refine_category(alipay_cat: str, party: str, item: str) -> str:
    if alipay_cat in ALIPAY_SPECIFIC:
        return ALIPAY_CATEGORY_MAP.get(alipay_cat, "其他")
    text = f"{party} {item}"
    for parent, words in REFINE_RULES:
        if any(w in text for w in words):
            return parent
    return ALIPAY_CATEGORY_MAP.get(alipay_cat, "其他")


def _map_account(method: str) -> str:
    s = _norm(method).split("&")[0].strip()
    if not s:
        return ""
    if "余额宝" in s or s.startswith("账户余额") or s in ("余额", "支付宝余额"):
        return "支付宝"
    if "花呗" in s:
        return "花呗"
    if "亲情卡" in s:
        return "亲情卡"
    s = re.sub(r"\(\d+\)\s*$", "", s)
    s = re.sub(r"(储蓄卡|信用卡|借记卡)$", "", s)
    return s.strip() or _norm(method)


# --------------------------------------------------------------------------- raw loading

def _load_raw(data: bytes, filename: str) -> list[tuple]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        raw = list(ws.iter_rows(values_only=True))
        wb.close()
        return raw
    if name.endswith(".csv"):
        text = None
        for enc in ("utf-8-sig", "gb18030", "utf-8"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("无法识别文件编码")
        return [tuple(r) for r in csv.reader(io.StringIO(text))]
    raise ValueError("只支持 .xlsx / .csv 文件（支付宝导出的 zip 请先解压）")


def parse_file(data: bytes, filename: str) -> dict:
    """Detect the export format and return normalized rows + metadata."""
    raw = _load_raw(data, filename)

    for i, row in enumerate(raw[:80]):
        cells = [_norm(c) for c in row]
        if all(k in cells for k in ("交易时间", "收/支")) and (
            "金额(元)" in cells or "支付方式" in cells or "当前状态" in cells
        ):
            rows, skipped, statuses = _parse_wechat(raw, i)
            return {
                "rows": rows,
                "source": "wechat",
                "skipped": skipped,
                "skipped_total": sum(skipped.values()),
                "statuses": statuses,
            }
        if all(k in cells for k in ALIPAY_KEYS):
            rows, skipped, statuses = _parse_alipay(raw, i)
            return {
                "rows": rows,
                "source": "alipay",
                "skipped": skipped,
                "skipped_total": sum(skipped.values()),
                "statuses": statuses,
            }
        if all(k in cells for k in ONE_WOOD_KEYS):
            return {
                "rows": _parse_onewood(raw, i),
                "source": "onewood",
                "skipped": {},
                "skipped_total": 0,
                "statuses": {},
            }
    raise ValueError(
        "无法识别文件格式。支持：第三方记账导出（日期/收支类型/金额）或支付宝交易明细（交易时间/收/支/金额）"
    )


def _parse_onewood(raw: list[tuple], header_idx: int) -> list[dict]:
    header = [_norm(c) for c in raw[header_idx]]
    out = []
    for row in raw[header_idx + 1:]:
        if not any(_norm(c) for c in row):
            continue
        out.append({header[i]: row[i] if i < len(row) else None for i in range(len(header))})
    return out


def _parse_alipay(raw: list[tuple], header_idx: int):
    header = [_norm(c) for c in raw[header_idx]]

    rows: list[dict] = []
    skipped: dict[str, int] = {}
    statuses: dict[str, int] = {}

    for r in raw[header_idx + 1:]:
        rec = {header[i]: (r[i] if i < len(r) else None) for i in range(len(header))}
        occurred = _norm_time(rec.get("交易时间"))
        if not occurred:
            continue

        status = _norm(rec.get("交易状态")) or "未知"
        statuses[status] = statuses.get(status, 0) + 1
        if status not in ALIPAY_OK_STATUS:
            skipped[status] = skipped.get(status, 0) + 1
            continue

        flow = _norm(rec.get("收/支"))
        if flow not in ("支出", "收入", "不计收支"):
            skipped[flow or "未知"] = skipped.get(flow or "未知", 0) + 1
            continue

        cents = _to_cents(rec.get("金额"))
        if cents <= 0:
            skipped["金额为 0"] = skipped.get("金额为 0", 0) + 1
            continue

        alipay_cat = _norm(rec.get("交易分类"))
        party = _norm(rec.get("交易对方"))
        item = _clean_item(rec.get("商品说明"))
        if flow == "收入":
            parent, kind = "收入", "income"
        else:
            parent = _refine_category(alipay_cat, party, item)
            kind = "expense"
        remark = _norm(rec.get("备注"))
        note = " · ".join([p for p in (party, item) if p])
        if remark:
            note = (note + f"（{remark}）").strip()
        if status.startswith("退款"):
            note = "退款 · " + note
        if len(note) > 80:
            note = note[:79] + "…"

        rows.append({
            "日期": occurred,
            "收支类型": "收入" if kind == "income" else "支出",
            "金额": f"{cents / 100:.2f}",
            "类别": parent,
            "子类": "",
            "账户": _map_account(rec.get("收/付款方式")),
            "备注": note,
            "其他": "不计入收支" if flow == "不计收支" else "",
            "退款金额": "",
            "_alipay_category": alipay_cat,
            "_key": "|".join([occurred, str(cents), party, item, flow]),
        })

    return rows, skipped, statuses


def _parse_wechat(raw: list[tuple], header_idx: int):
    header = [_norm(c) for c in raw[header_idx]]

    rows: list[dict] = []
    skipped: dict[str, int] = {}
    statuses: dict[str, int] = {}

    for r in raw[header_idx + 1:]:
        rec = {header[i]: (r[i] if i < len(r) else None) for i in range(len(header))}
        occurred = _norm_time(rec.get("交易时间"))
        if not occurred:
            continue

        status = _norm(rec.get("当前状态")) or "未知"
        statuses[status] = statuses.get(status, 0) + 1
        if any(bad in status for bad in WECHAT_BAD_STATUS):
            skipped[status] = skipped.get(status, 0) + 1
            continue

        flow = _norm(rec.get("收/支"))
        if flow not in ("支出", "收入", "不计收支"):
            skipped[flow or "未知"] = skipped.get(flow or "未知", 0) + 1
            continue

        cents = _to_cents(rec.get("金额(元)"))
        if cents <= 0:
            skipped["金额为 0"] = skipped.get("金额为 0", 0) + 1
            continue

        party = _norm(rec.get("交易对方"))
        item = _norm(rec.get("商品")).removeprefix("收款方备注:")
        item = re.sub(r"\s+", " ", item).strip()
        trade_type = _norm(rec.get("交易类型"))
        remark = _norm(rec.get("备注"))
        if remark in ("/", "—", "-"):
            remark = ""

        if flow == "收入":
            parent, kind = "收入", "income"
        else:
            parent, kind = _wechat_category(party, item, trade_type), "expense"

        note = " · ".join([p for p in (party, item) if p])
        if remark:
            note = (note + f"（{remark}）").strip()
        if "退款" in status:
            note = "退款 · " + note
        if len(note) > 80:
            note = note[:79] + "…"

        order_no = _norm(rec.get("交易单号"))
        key_seed = order_no or "|".join([occurred, str(cents), party, item, flow])

        rows.append({
            "日期": occurred,
            "收支类型": "收入" if kind == "income" else "支出",
            "金额": f"{cents / 100:.2f}",
            "类别": parent,
            "子类": "",
            "账户": _map_wechat_account(_norm(rec.get("支付方式"))),
            "备注": note,
            "其他": "不计入收支" if flow == "不计收支" else "",
            "退款金额": "",
            "_wechat_type": trade_type,
            "_key": key_seed,
        })

    return rows, skipped, statuses


def _wechat_category(party: str, item: str, trade_type: str) -> str:
    text = f"{party} {item} {trade_type}"
    for parent, words in WECHAT_CATEGORY_RULES:
        if any(w in text for w in words):
            return parent
    return "其他"


def _map_wechat_account(method: str) -> str:
    s = _norm(method)
    if not s:
        return ""
    if "零钱" in s:
        return "微信钱包"
    if "亲属卡" in s:
        return "亲属卡"
    if "分付" in s:
        return "分付"
    s = re.sub(r"\(\d+\)\s*$", "", s)
    s = re.sub(r"(储蓄卡|信用卡|借记卡)$", "", s)
    return s.strip() or _norm(method)


# --------------------------------------------------------------------------- categories

def _ensure_parent(con, name: str) -> int:
    row = con.execute(
        "SELECT id FROM categories WHERE parent_id IS NULL AND name = ?", (name,)
    ).fetchone()
    if row:
        return row["id"]
    return db._insert_category(con, name, None, DEFAULT_ICON, DEFAULT_COLOR, 99)


def _ensure_category(con, parent_name: str, child_name: str) -> int:
    parent_id = _ensure_parent(con, parent_name)
    if not child_name:
        return parent_id
    row = con.execute(
        "SELECT id FROM categories WHERE parent_id = ? AND name = ?", (parent_id, child_name)
    ).fetchone()
    if row:
        return row["id"]
    color = con.execute("SELECT color FROM categories WHERE id = ?", (parent_id,)).fetchone()
    return db._insert_category(con, child_name, parent_id, "", color["color"] if color else DEFAULT_COLOR, 99)


def _ensure_account(con, name: str) -> int | None:
    name = _norm(name)
    if not name:
        return None
    row = con.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return int(
        con.execute(
            "INSERT INTO accounts(name, kind, color, sort) VALUES (?, 'other', ?, 99)",
            (name, DEFAULT_COLOR),
        ).lastrowid
    )


def _rule_text(r: dict) -> str:
    return " ".join([
        _norm(r.get("备注")),
        _norm(r.get("类别")),
        _norm(r.get("子类")),
        _norm(r.get("_alipay_category")),
    ])


def _is_fixed_expense(r: dict, cfg: dict | None = None) -> bool:
    if _norm(r.get("收支类型")) == "收入":
        return False
    return rules.is_fixed(
        _norm(r.get("类别")), _norm(r.get("子类")), _rule_text(r), cfg=cfg
    )


def _auto_work(r: dict, cfg: dict | None = None) -> bool:
    if _norm(r.get("收支类型")) == "收入":
        return False
    if _to_cents(r.get("报销金额")) or _norm(r.get("报销账户")):
        return False
    return rules.is_work(
        _norm(r.get("类别")), _norm(r.get("子类")), _rule_text(r), cfg=cfg
    )


# --------------------------------------------------------------------------- preview

def preview(rows: list[dict], meta: dict | None = None, cfg: dict | None = None) -> dict:
    meta = meta or {}
    expenses = [r for r in rows if _norm(r.get("收支类型")) != "收入"]
    incomes = [r for r in rows if _norm(r.get("收支类型")) == "收入"]
    dates = [_norm_time(r.get("日期")) for r in rows]
    dates = [d for d in dates if d]
    parents = sorted({(_norm(r.get("类别")) or "其他") for r in rows})
    pairs = {(_norm(r.get("类别")) or "其他", _norm(r.get("子类"))) for r in rows if _norm(r.get("子类"))}
    accs = sorted({_norm(r.get("账户")) for r in rows if _norm(r.get("账户"))})
    others = [_norm(r.get("其他")) for r in rows]
    refunds = sum(1 for r in rows if _to_cents(r.get("退款金额")))
    excluded = sum(1 for o in others if "不计入收支" in o)
    auto_work = sum(1 for r in rows if _auto_work(r, cfg))
    fixed = sum(1 for r in rows if _is_fixed_expense(r, cfg))
    return {
        "total": len(rows),
        "expense_count": len(expenses),
        "income_count": len(incomes),
        "expense_cents": sum(_to_cents(r.get("金额")) for r in expenses),
        "income_cents": sum(_to_cents(r.get("金额")) for r in incomes),
        "start": min(dates)[:10] if dates else "",
        "end": max(dates)[:10] if dates else "",
        "categories": len(parents),
        "category_pairs": len(pairs),
        "accounts": accs,
        "refund_count": refunds,
        "exclude_stats_count": excluded,
        "auto_work_count": auto_work,
        "fixed_count": fixed,
        "category_list": parents,
        "source": meta.get("source", "onewood"),
        "skipped_total": meta.get("skipped_total", 0),
        "skipped": meta.get("skipped", {}),
        "statuses": meta.get("statuses", {}),
    }


# --------------------------------------------------------------------------- import

def import_rows(con, rows: list[dict], mode: str = "merge", source: str = "onewood") -> dict:
    if mode == "replace":
        con.execute("DELETE FROM entries")
        con.execute("DELETE FROM claims")
        con.execute("DELETE FROM sqlite_sequence WHERE name IN ('entries','claims')")

    cfg = rules.load(con)
    now = db.now_iso()
    imported = skipped = 0
    seen: dict[str, int] = {}
    work_rows = 0
    auto_work_rows = 0
    excluded_stats = 0
    fixed_rows = 0

    for r in rows:
        occurred = _norm_time(r.get("日期"))
        cents = _to_cents(r.get("金额"))
        if not occurred or cents <= 0:
            skipped += 1
            continue

        type_label = _norm(r.get("收支类型"))
        kind = "income" if type_label == "收入" else "expense"
        parent_name = _norm(r.get("类别")) or ("收入" if kind == "income" else "其他")
        child_name = _norm(r.get("子类"))
        if parent_name == "收入" and kind == "expense":
            kind = "income"
        if kind == "income":
            parent_name = "收入"

        note = _norm(r.get("备注"))
        other = _norm(r.get("其他"))
        refund = _to_cents(r.get("退款金额"))
        if refund:
            note = (note + f"（退款 {refund / 100:.2f}）").strip()

        category_id = _ensure_category(con, parent_name, child_name)
        account_id = _ensure_account(con, r.get("账户"))

        reimburse_amount = _to_cents(r.get("报销金额"))
        import_reimb = bool(reimburse_amount or _norm(r.get("报销账户")))
        usage = "work" if import_reimb else "personal"
        auto_work = 0
        if kind == "expense" and usage != "work" and _auto_work(r, cfg):
            usage = "work"
            auto_work = 1
        if import_reimb:
            work_rows += 1

        auto_work_rows += auto_work
        exclude_stats = 1 if "不计入收支" in other else 0
        exclude_daily = 0
        if kind == "expense" and usage == "personal" and rules.is_fixed(
            parent_name, child_name, _rule_text(r), cfg=cfg
        ):
            exclude_daily = 1
        exclude_budget = 1 if "不计入预算" in other else 0
        excluded_stats += exclude_stats
        fixed_rows += exclude_daily

        key_seed = r.get("_key") or "|".join(
            [occurred, str(cents), parent_name, child_name, note, _norm(r.get("账户")), kind]
        )
        key_base = hashlib.sha1(key_seed.encode("utf-8")).hexdigest()[:24]
        occurrence = seen.get(key_base, 0) + 1
        seen[key_base] = occurrence
        external_id = f"{source}:{key_base}:{occurrence}"

        cur = con.execute(
            """INSERT OR IGNORE INTO entries
               (kind, occurred_at, amount_cents, category_id, account_id, note, usage,
                claim_id, project, invoice, invoice_no, refund_cents, exclude_stats,
                exclude_daily, exclude_budget, source, external_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'none', '', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kind,
                occurred,
                cents,
                category_id,
                account_id,
                note,
                usage,
                None,
                refund,
                exclude_stats,
                exclude_daily,
                exclude_budget,
                source,
                external_id,
                now,
                now,
            ),
        )
        if cur.rowcount:
            imported += 1
        else:
            skipped += 1

    con.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "excluded_stats": excluded_stats,
        "fixed_rows": fixed_rows,
        "work_rows": work_rows,
        "auto_work": auto_work_rows,
        "mode": mode,
        "source": source,
    }


def guess_export_files() -> list[str]:
    """Recently modified exports, for the UI's quick-pick list."""
    candidates: list[Path] = []
    for folder in (Path.home() / "Downloads", Path.home() / "Desktop"):
        if folder.is_dir():
            candidates += list(folder.glob("账单_*.xlsx"))
            candidates += list(folder.glob("账单_*.csv"))
            candidates += list(folder.glob("支付宝交易明细*.csv"))
            candidates += list(folder.glob("微信支付账单*.csv"))
            candidates += list(folder.glob("微信支付账单*.xlsx"))
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in candidates[:6]]
