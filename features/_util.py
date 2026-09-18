"""
Shared helpers for the built-in features. Not a feature itself -- the loader
skips modules whose name starts with an underscore.

Everything that shells out goes through `run()`, so every subprocess gets the
same treatment: no shell, a timeout, output capped, and the kill switch
honoured while the child is still running.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from servant.sdk import ToolError

POLL_SECONDS = 0.2
MAX_TIMEOUT = 600


@dataclass
class Completed:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        """stdout and stderr together, the way a human would have seen them."""
        return "\n".join(part for part in (self.out.rstrip(), self.err.rstrip()) if part)


def stop_requested(ctx) -> bool:
    """The executor checks the kill switch between tool calls. A tool that
    loops or waits for a long time should also check it in the middle."""
    return (ctx.config.root / str(ctx.config.get("governance.killswitch_file", ".servant/STOP"))).exists()


def run(ctx, argv: list[str], *, cwd: Path, timeout: float = 60) -> Completed:
    """Run a command without a shell. Kills the child if STOP appears.

    Raises ToolError on timeout, on a stop, or if the program is missing --
    the three cases where there is no meaningful output to hand back.
    """
    timeout = max(1.0, min(float(timeout), MAX_TIMEOUT))

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "PYTHONIOENCODING": "utf-8"},
        )
    except FileNotFoundError:
        raise ToolError(f"'{argv[0]}' is not installed or not on PATH") from None

    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=POLL_SECONDS)
                return Completed(proc.returncode, out or "", err or "")
            except subprocess.TimeoutExpired:
                pass
            if stop_requested(ctx):
                raise ToolError("stopped by the kill switch; the command was killed")
            if time.monotonic() > deadline:
                raise ToolError(f"timed out after {timeout:.0f}s; the command was killed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def truncate(text: str, limit: int, *, keep: str = "head") -> str:
    """Cap text for the LLM. `keep="tail"` for logs, where the end matters."""
    limit = max(200, int(limit))
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    if keep == "tail":
        return f"... [{cut:,} earlier chars cut]\n" + text[-limit:]
    return text[:limit] + f"\n... [{cut:,} more chars cut]"


def feature_setting(ctx, key: str, default):
    """Read `features.<key>` from config/policy.yaml. The owner's file, read-only."""
    value = ctx.config.get(f"features.{key}")
    return default if value is None else value
