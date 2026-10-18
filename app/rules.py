"""User-editable rules for work expenses and fixed expenses.

The defaults mirror the original hard-coded lists; the settings UI stores
overrides as JSON in the `settings` table under the `rules` key.
"""

from __future__ import annotations

import json

TRAIN = ("高铁", "动车", "火车", "铁路", "12306")
FLIGHT = ("机票", "飞机", "航班", "航空")
HOTEL = ("酒店", "宾馆", "住宿", "民宿", "客栈", "旅馆", "青旅")
TAXI = ("打车", "滴滴", "出租车", "网约车", "花小猪", "曹操出行", "T3出行",
        "首汽", "高德打车", "礼橙专车", "阳光出行")

KEYWORD_GROUPS = [
    ("高铁 / 火车", TRAIN),
    ("飞机", FLIGHT),
    ("酒店 / 住宿", HOTEL),
    ("打车", TAXI),
]

DEFAULT_RULES = {
    "work": {
        "keywords": list(dict.fromkeys(w for _, words in KEYWORD_GROUPS for w in words)),
        "exclude": ["地铁", "轨道交通", "公交", "巴士"],
    },
    "fixed": {
        "keywords": ["房租", "租金", "租房"],
    },
}


def _strings(value, limit: int = 80) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()[:80]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def normalize(cfg) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    work = cfg.get("work") if isinstance(cfg.get("work"), dict) else {}
    fixed = cfg.get("fixed") if isinstance(cfg.get("fixed"), dict) else {}
    return {
        "work": {
            "keywords": _strings(work.get("keywords")),
            "exclude": _strings(work.get("exclude")),
        },
        "fixed": {
            "keywords": _strings(fixed.get("keywords")),
        },
    }


def defaults() -> dict:
    return json.loads(json.dumps(DEFAULT_RULES))


def load(con) -> dict:
    cfg = defaults()
    try:
        row = con.execute("SELECT value FROM settings WHERE key='rules'").fetchone()
    except Exception:
        row = None
    if not row or not row["value"]:
        return cfg
    try:
        saved = json.loads(row["value"])
    except (TypeError, ValueError):
        return cfg
    return normalize(saved)


def save(con, cfg) -> dict:
    cfg = normalize(cfg)
    con.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('rules', ?)",
        (json.dumps(cfg, ensure_ascii=False),),
    )
    con.commit()
    return cfg


def match(parent: str, child: str, text: str, cfg: dict | None = None) -> str | None:
    """Return a short reason when an expense should default to work."""
    cfg = normalize(cfg) if cfg else defaults()
    work = cfg["work"]
    body = " ".join([text or "", parent or "", child or ""])
    if any(word in body for word in work["exclude"]):
        return None
    for word in work["keywords"]:
        if word in body:
            return word
    return None


def is_work(parent: str, child: str, text: str, cfg: dict | None = None) -> bool:
    return match(parent, child, text, cfg) is not None


def is_fixed(parent: str, child: str, text: str, cfg: dict | None = None) -> bool:
    cfg = normalize(cfg) if cfg else defaults()
    fixed = cfg["fixed"]
    body = " ".join([text or "", parent or "", child or ""])
    return any(word in body for word in fixed["keywords"])


def describe() -> dict:
    return defaults()
