"""
Append-only audit log.

Every observation, decision, gate verdict and action lands here as one JSON
line. Records are hash-chained: each entry carries the hash of the previous
one, so `verify()` detects a record that was edited or removed from the
middle of the log.

What it does NOT detect: truncation of the tail. Chop the last N lines off
and the remainder is still a valid chain. Catching that needs an anchor the
agent cannot reach -- a counter kept elsewhere, or shipping the log off the
machine. Say this out loud rather than overselling the guarantee.

Tail it live during a demo:  tail -f .servant/audit.jsonl
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 16


class AuditLog:
    def __init__(self, path: Path, redactor=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._redactor = redactor
        self._prev = self._last_hash()

    # -- writing -----------------------------------------------------------
    def write(self, kind: str, run_id: str = "", **payload: Any) -> dict:
        if self._redactor is not None:
            payload = self._redactor.scrub(payload)

        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": run_id,
            "kind": kind,
            "payload": payload,
            "prev": self._prev,
        }
        record["hash"] = _digest(record)

        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
            self._prev = record["hash"]
        return record

    # -- reading -----------------------------------------------------------
    def read(self, limit: int | None = None, run_id: str | None = None) -> list[dict]:
        rows = list(self._iter())
        if run_id:
            rows = [r for r in rows if r.get("run_id") == run_id]
        return rows[-limit:] if limit else rows

    def verify(self) -> tuple[bool, str]:
        """Walk the chain. Returns (intact, message)."""
        prev = GENESIS
        for i, rec in enumerate(self._iter(), start=1):
            if rec.get("prev") != prev:
                return False, f"chain broken at line {i}: prev mismatch"
            expected = _digest({k: rec[k] for k in ("ts", "run_id", "kind", "payload", "prev")})
            if rec.get("hash") != expected:
                return False, f"chain broken at line {i}: content was edited"
            prev = rec["hash"]
        return True, f"audit chain intact ({prev[:8]}...)"

    def _iter(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def _last_hash(self) -> str:
        last = GENESIS
        for rec in self._iter():
            last = rec.get("hash", last)
        return last


def _digest(record: dict) -> str:
    blob = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
