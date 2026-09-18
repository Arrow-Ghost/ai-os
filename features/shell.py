"""
shell.* -- the riskiest capability in the repo, fenced in three ways.

    shell.which  READ    is a program installed, and where
    shell.run    DANGER  run ONE allowlisted command, no shell

1. Allowlist. Only commands matching `features.shell.allowlist` in
   config/policy.yaml run. An entry is a token prefix: "git status" allows
   `git status -s` but not `git push`; "pytest" allows any pytest call. The
   list lives in the owner's file, so the agent cannot widen it.
2. No shell. The command is split into argv and executed directly: pipes,
   redirection, `&&`, `;`, subshells and variable expansion are rejected
   before anything runs, not interpreted.
3. DANGER tier. Even an allowlisted command waits for a human.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path

from servant.sdk import Tier, ToolError, tool

from ._util import feature_setting, run, truncate

METACHARS = re.compile(r"[|&;<>`\n\r]|\$\(|\$\{|%[A-Za-z_]+%")
WINDOWS = os.name == "nt"


def _split(command: str) -> list[str]:
    try:
        parts = shlex.split(command, posix=not WINDOWS)
    except ValueError as exc:
        raise ToolError(f"could not parse the command: {exc}") from None
    if WINDOWS:  # posix=False keeps the quotes; strip them, keep backslashes intact
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return parts


def _program(token: str) -> str:
    name = Path(token).name.lower()
    for ext in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _allowed(argv: list[str], allowlist: list[str]) -> str | None:
    """Return the allowlist entry that permits argv, or None."""
    words = [_program(argv[0])] + [a.lower() for a in argv[1:]]
    for entry in allowlist:
        prefix = [_program(t) if i == 0 else t.lower() for i, t in enumerate(_split(str(entry)))]
        if prefix and words[: len(prefix)] == prefix:
            return str(entry)
    return None


@tool(
    name="shell.which",
    tier=Tier.READ,
    params={"name": "Program name, e.g. 'git', 'node', 'pytest'"},
)
def shell_which(ctx, name: str) -> str:
    """Check whether a command-line program is installed, and where. Also says if shell.run may use it."""
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", name):
        raise ToolError(f"{name!r} is not a plain program name")
    found = shutil.which(name)
    allowlist = feature_setting(ctx, "shell.allowlist", [])
    usable = [e for e in allowlist if _program(_split(str(e))[0]) == _program(name)]
    note = f"shell.run allows: {', '.join(usable)}" if usable else "not on the shell.run allowlist"
    return f"{name}: {found}\n{note}" if found else f"{name}: not found on PATH"


@tool(
    name="shell.run",
    tier=Tier.DANGER,
    params={
        "command": "One command with its arguments. No pipes, redirects, && or ;",
        "cwd": "Folder to run it in",
        "timeout": "Seconds before it is killed (max 600)",
    },
    undo="depends on the command -- read it before approving",
)
def shell_run(ctx, command: str, cwd: str = ".", timeout: int = 60) -> str:
    """Run a single allowlisted command-line program. Prefer a dedicated tool (git.*, code.run_tests) when one exists."""
    if METACHARS.search(command):
        raise ToolError("pipes, redirection, chaining and variable expansion are not allowed; run one command")
    argv = _split(command)
    if not argv:
        raise ToolError("the command is empty")

    allowlist = feature_setting(ctx, "shell.allowlist", [])
    entry = _allowed(argv, allowlist)
    if entry is None:
        raise ToolError(
            f"'{_program(argv[0])}' with these arguments is not on the allowlist "
            f"(features.shell.allowlist in config/policy.yaml). Only the owner can add it."
        )

    exe = shutil.which(argv[0])
    if exe is None:
        raise ToolError(f"'{argv[0]}' is not installed or not on PATH")
    folder = ctx.check_path(cwd)
    if not folder.is_dir():
        raise ToolError(f"{folder} is not a directory")
    for arg in argv[1:]:
        # Any argument could be a path, including the value half of --flag=value.
        candidate = arg.split("=", 1)[-1]
        if candidate:
            try:
                ctx.check_path(folder / candidate)
            except ToolError:
                raise
            except Exception:  # noqa: BLE001 -- not a usable path, so not a forbidden one
                pass

    result = run(ctx, [exe, *argv[1:]], cwd=folder, timeout=timeout)
    ctx.log(f"ran `{command}` (allowed by '{entry}') -> exit {result.code}")
    body = truncate(result.text, int(feature_setting(ctx, "shell.max_output_chars", 4000)), keep="tail")
    return ctx.scrub(f"exit code {result.code}\n{body or '(no output)'}")
