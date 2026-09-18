"""
The redaction gate.

Everything that leaves the machine (goes to the LLM) or gets written to the
audit log passes through here first. Feature authors never call this -- the
executor and the brain wrapper do it for you.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

MASK = "[REDACTED]"


class Redactor:
    def __init__(self, patterns: Iterable[str], forbidden_paths: Iterable[str], enabled: bool = True):
        self.enabled = enabled
        self._regexes = [re.compile(p) for p in patterns]
        self._forbidden = [Path(p).expanduser().resolve() for p in forbidden_paths]

    def scrub(self, value: Any) -> Any:
        """Recursively mask secrets in strings, dicts, lists."""
        if not self.enabled:
            return value
        if isinstance(value, str):
            out = value
            for rx in self._regexes:
                out = rx.sub(MASK, out)
            return out
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self.scrub(v) for v in value)
        return value

    def path_is_forbidden(self, candidate: str | Path) -> bool:
        """True if a path sits inside a directory the agent must never touch."""
        try:
            target = Path(candidate).expanduser().resolve()
        except (OSError, RuntimeError):
            return True
        for bad in self._forbidden:
            if target == bad or bad in target.parents:
                return True
            if target.name == bad.name and bad.name.startswith("."):
                return True
        return False

    @classmethod
    def from_config(cls, config) -> "Redactor":
        return cls(
            patterns=config.get("redaction.patterns", []),
            forbidden_paths=config.get("redaction.forbidden_paths", []),
            enabled=bool(config.get("redaction.enabled", True)),
        )
