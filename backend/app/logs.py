from __future__ import annotations

import json
import time
from typing import Any

from .db import connect
from .security import redact

KEEP = 8000


def add_log(
    message: str,
    *,
    level: str = "info",
    category: str = "system",
    action: str = "",
    detail: Any = None,
    actor: str = "",
) -> None:
    try:
        payload = ""
        if detail is not None:
            payload = detail if isinstance(detail, str) else json.dumps(detail, default=str)
        # Upstream errors can quote a URL that carries an API key, so nothing
        # reaches the log table (or the Logs page) without a scrub first.
        message = redact(message)
        payload = redact(payload) if payload else ""
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO logs (created_at, level, category, action, message, detail, actor)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (int(time.time()), level, category, action, message, payload, actor),
            )
            conn.execute(
                "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT ?)",
                (KEEP,),
            )
    except Exception:
        pass


def list_logs(
    q: str = "",
    category: str = "",
    level: str = "",
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(max(page_size, 10), 200)
    where = ["1=1"]
    params: list[Any] = []
    if category:
        where.append("category = ?")
        params.append(category)
    if q:
        where.append("(message LIKE ? OR action LIKE ? OR actor LIKE ? OR detail LIKE ?)")
        needle = f"%{q}%"
        params.extend([needle, needle, needle, needle])
    level_where = list(where)
    level_params = list(params)
    if level:
        where.append("level = ?")
        params.append(level)
    clause = " AND ".join(where)
    # Level counts ignore the level filter so the UI can show what switching to it
    # would find, rather than the count of the level you are already looking at.
    level_clause = " AND ".join(level_where)
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM logs WHERE {clause}", params).fetchone()[0]
        counts = {
            row["level"]: row["n"]
            for row in conn.execute(
                f"SELECT level, COUNT(*) AS n FROM logs WHERE {level_clause} GROUP BY level",
                level_params,
            ).fetchall()
        }
        rows = conn.execute(
            f"""
            SELECT * FROM logs
            WHERE {clause}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        detail = item.get("detail") or ""
        if detail.startswith("{") or detail.startswith("["):
            try:
                item["detail"] = json.loads(detail)
            except Exception:
                pass
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "levels": {
            "info": counts.get("info", 0),
            "warn": counts.get("warn", 0),
            "error": counts.get("error", 0),
        },
    }
