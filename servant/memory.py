"""
Memory: a single SQLite file, three tables, no embeddings.

  episodes  what happened, in order (the agent's diary)
  facts     durable key/value the agent should not have to re-ask
  outcomes  did tool X work last time? -> feeds confidence later

Features reach this through `ctx.memory`. Keep feature writes small and
namespaced ("downloads.last_run"), because everything shares one store.

Semantic recall (ChromaDB) is a later upgrade; the interface below is designed
so `recall()` can grow a vector backend without any feature changing.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT NOT NULL,
    ts       REAL NOT NULL,
    role     TEXT NOT NULL,
    content  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    key      TEXT PRIMARY KEY,
    value    TEXT NOT NULL,
    ts       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tool     TEXT NOT NULL,
    ok       INTEGER NOT NULL,
    detail   TEXT,
    ts       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_run ON episodes(run_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_tool ON outcomes(tool);
"""


class Memory:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    # -- episodic ----------------------------------------------------------
    def add_episode(self, run_id: str, role: str, content: str) -> None:
        self._db.execute(
            "INSERT INTO episodes (run_id, ts, role, content) VALUES (?,?,?,?)",
            (run_id, time.time(), role, content),
        )
        self._db.commit()

    def recent_episodes(self, limit: int = 20, run_id: str | None = None) -> list[dict]:
        if run_id:
            cur = self._db.execute(
                "SELECT * FROM episodes WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit)
            )
        else:
            cur = self._db.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in reversed(cur.fetchall())]

    # -- facts -------------------------------------------------------------
    def remember(self, key: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO facts (key, value, ts) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
            (key, json.dumps(value, default=str), time.time()),
        )
        self._db.commit()

    def recall(self, key: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def forget(self, key: str) -> None:
        self._db.execute("DELETE FROM facts WHERE key=?", (key,))
        self._db.commit()

    def all_facts(self) -> dict[str, Any]:
        rows = self._db.execute("SELECT key, value FROM facts ORDER BY key").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def search_facts(self, query: str, *, prefix: str = "", limit: int = 20) -> list[dict]:
        """Keyword match on key and value. NOT semantic search -- there is no
        embedding model here, deliberately (see docs/ARCHITECTURE.md). This is
        a plain SQL LIKE over what memory.remember has stored, which is honest
        about what it is: exact and substring recall, not fuzzy-meaning recall.
        """
        needle = f"%{query.lower()}%"
        sql = "SELECT key, value, ts FROM facts WHERE (LOWER(key) LIKE ? OR LOWER(value) LIKE ?)"
        params: list[Any] = [needle, needle]
        if prefix:
            sql += " AND key LIKE ?"
            params.append(f"{prefix}%")
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(sql, params).fetchall()
        return [{"key": r["key"], "value": json.loads(r["value"]), "ts": r["ts"]} for r in rows]

    def search_episodes(self, query: str, *, limit: int = 10) -> list[dict]:
        """Substring match over the episodic log -- what the agent has said or done."""
        needle = f"%{query.lower()}%"
        rows = self._db.execute(
            "SELECT run_id, ts, role, content FROM episodes WHERE LOWER(content) LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (needle, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # -- outcomes ----------------------------------------------------------
    def record_outcome(self, tool: str, ok: bool, detail: str = "") -> None:
        self._db.execute(
            "INSERT INTO outcomes (tool, ok, detail, ts) VALUES (?,?,?,?)",
            (tool, 1 if ok else 0, detail[:500], time.time()),
        )
        self._db.commit()

    def success_rate(self, tool: str) -> float | None:
        row = self._db.execute(
            "SELECT COUNT(*) n, SUM(ok) s FROM outcomes WHERE tool=?", (tool,)
        ).fetchone()
        return None if not row["n"] else float(row["s"] or 0) / row["n"]

    def close(self) -> None:
        self._db.close()
