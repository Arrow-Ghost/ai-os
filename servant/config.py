"""
Configuration and policy.

Two rules that matter:

1. The agent READS this. It never writes it. Goals and limits belong to the
   human owner, not to the agent -- that is what makes it correctable.
2. Everything has a safe default in DEFAULT_POLICY, so the project runs with
   zero dependencies and zero setup. `config/policy.yaml` only overrides.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_POLICY: dict[str, Any] = {
    # --- brain -------------------------------------------------------------
    "brain": {
        # "groq" for the real thing, "offline" for a scripted stub that needs
        # no API key (juniors can build features without ever calling Groq).
        "provider": "offline",
        "base_url": "https://api.groq.com/openai/v1",
        "model_fast": "openai/gpt-oss-20b",
        "model_smart": "openai/gpt-oss-120b",
        "model_vision": "qwen/qwen3.8-27b",
        "model_transcribe": "whisper-large-v3-turbo",
        "temperature": 0.2,
        "max_retries": 3,
        "max_image_bytes": 4000000,
    },

    # --- governance --------------------------------------------------------
    "governance": {
        # Tier -> what the executor does. Change these to tighten or loosen.
        #   auto    run it, log it
        #   approve ask the human first
        #   block   refuse
        "tier_policy": {
            "read": "auto",
            "write": "auto",
            "danger": "approve",
            "forbidden": "block",
        },
        # Tools named here are forced to a tier regardless of what they
        # declared. Owner override beats feature author. Always.
        "tier_overrides": {},
        # Tools that may never run, by name.
        "denylist": [],
        # Below this confidence the brain must ask even for "auto" tiers.
        "confidence_floor": 0.35,
        "killswitch_file": ".servant/STOP",
    },

    # --- budgets (resource bounds, not resource acquisition) ---------------
    "budgets": {
        "max_steps": 12,          # tool calls per run
        "max_llm_calls": 20,
        "max_seconds": 300,
        "max_spend_usd": 0.0,     # hard zero. The agent never spends money.
    },

    # --- redaction ---------------------------------------------------------
    "redaction": {
        "enabled": True,
        # Regexes scrubbed from anything sent to the LLM or written to the log.
        "patterns": [
            r"sk-[A-Za-z0-9_\-]{16,}",
            r"gsk_[A-Za-z0-9_\-]{16,}",
            r"AKIA[0-9A-Z]{16}",
            r"ghp_[A-Za-z0-9]{36}",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*\S+",
        ],
        # Paths the agent must never read, at all.
        "forbidden_paths": ["~/.ssh", "~/.aws", "~/.gnupg", ".env"],
    },

    # --- skill acquisition (the agent writing its own tools) ---------------
    "skills": {
        "enabled": True,
        # Generated tools are pinned to this tier. DANGER means anything the
        # agent taught itself asks a human before it runs. The validator
        # rejects any generated tool that declares something lower.
        "tier_floor": "danger",
        "max_attempts": 3,
        "sandbox_timeout": 20,
        "sandbox_memory_mb": 512,
    },

    # --- storage -----------------------------------------------------------
    "paths": {
        "state_dir": ".servant",
        "audit_log": ".servant/audit.jsonl",
        "memory_db": ".servant/memory.db",
        "features_dir": "features",
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    """Read-only view over merged policy. Access with dotted paths."""

    # deepcopy, not dict(): a shallow copy shares the nested dicts with the
    # module-level DEFAULT_POLICY, so mutating config.data["governance"] would
    # silently rewrite the defaults for the whole process.
    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_POLICY))
    root: Path = PROJECT_ROOT
    source: str = "defaults"

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, key: str) -> Path:
        """Resolve a configured path relative to the project root."""
        raw = self.get(f"paths.{key}")
        if raw is None:
            raise KeyError(f"no configured path: {key}")
        p = Path(str(raw)).expanduser()
        return p if p.is_absolute() else self.root / p

    def secret(self, name: str, default: str | None = None) -> str | None:
        """Secrets come from the environment, never from the policy file."""
        return os.environ.get(name, default)


def load_config(policy_file: str | Path | None = None) -> Config:
    """Load defaults, then overlay config/policy.yaml if it exists.

    PyYAML is optional. Without it you get defaults and a warning, which keeps
    `git clone && python -m servant tools` working on a fresh machine.
    """
    _load_dotenv(PROJECT_ROOT / ".env")

    data = copy.deepcopy(DEFAULT_POLICY)
    source = "defaults"
    candidate = Path(policy_file) if policy_file else PROJECT_ROOT / "config" / "policy.yaml"

    if candidate.exists():
        try:
            import yaml  # type: ignore
        except ImportError:
            print(f"[config] {candidate.name} found but PyYAML is not installed; using defaults.")
        else:
            loaded = yaml.safe_load(candidate.read_text()) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{candidate} must contain a YAML mapping")
            data = _deep_merge(data, loaded)
            source = str(candidate)

    return Config(data=data, root=PROJECT_ROOT, source=source)


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so we do not depend on python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))
