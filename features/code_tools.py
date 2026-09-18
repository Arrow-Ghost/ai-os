"""
code.* -- the bug-repro loop: read the code, run the tests, patch on a branch.

    code.read_range   READ    a slice of a source file, with line numbers
    code.run_tests    WRITE   run pytest / npm test, return only the failures
    code.apply_patch  DANGER  apply a unified diff -- refused on main/master

run_tests is WRITE, not READ: test suites write caches and temp files, and
some write a lot more than that.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path

from servant.sdk import Tier, ToolError, tool

from ._util import feature_setting, run, truncate

MAX_LINES = 400
PY_MARKERS = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "conftest.py")
DIFF_PATH = re.compile(r"^(?:---|\+\+\+) (?:\"?[ab]/)?(.+?)\"?(?:\t.*)?$")


# -- read_range ------------------------------------------------------------

@tool(
    name="code.read_range",
    tier=Tier.READ,
    params={
        "path": "Source file",
        "start": "First line to show (1-based)",
        "end": f"Last line to show; 0 means start+149 (at most {MAX_LINES} lines per call)",
    },
    examples=["code.read_range path=servant/executor.py start=60 end=120"],
)
def code_read_range(ctx, path: str, start: int = 1, end: int = 0) -> str:
    """Read specific lines of a source file, numbered. Use after a traceback points at a line."""
    target = ctx.check_path(path)
    if not target.is_file():
        raise ToolError(f"{target} is not a file")
    raw = target.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ToolError(f"{target} looks binary")

    lines = raw.decode("utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(1, int(start))
    end = int(end) or start + 149
    end = min(end, start + MAX_LINES - 1, total)
    if start > total:
        raise ToolError(f"{target.name} has only {total} lines")

    width = len(str(end))
    body = "\n".join(f"{n:>{width}} | {lines[n - 1]}" for n in range(start, end + 1))
    more = f"  (file continues to {total})" if end < total else ""
    return ctx.scrub(f"{target}  lines {start}-{end} of {total}{more}\n{body}")


# -- run_tests -------------------------------------------------------------

def _python_for(project: Path) -> str:
    """Prefer the project's own virtualenv; its deps are the ones the tests need."""
    for folder in (project, *list(project.parents)[:3]):
        for venv in (".venv", "venv", "env"):
            for exe in ("Scripts/python.exe", "bin/python"):
                candidate = folder / venv / exe
                if candidate.is_file():
                    return str(candidate)
    return sys.executable


def _detect_runner(project: Path) -> str:
    if any((project / m).exists() for m in PY_MARKERS) or (project / "tests").is_dir():
        return "pytest"
    pkg = project / "package.json"
    if pkg.is_file():
        try:
            if json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}).get("test"):
                return "npm"
        except ValueError:
            pass
    if list(project.glob("test_*.py")) or list(project.glob("*_test.py")):
        return "pytest"
    raise ToolError(f"cannot tell how to test {project}; pass runner='pytest' or runner='npm'")


def _pytest_failures(text: str, limit: int) -> str:
    """Keep the FAILURES/ERRORS detail and the short summary; drop the noise."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^=+ (FAILURES|ERRORS) =+$", l)), None)
    summary_at = next((i for i, l in enumerate(lines) if "short test summary info" in l), None)
    if start is None and summary_at is None:
        return truncate(text, limit, keep="tail")
    summary = "\n".join(lines[summary_at:]) if summary_at is not None else lines[-1]
    detail = "\n".join(lines[start:summary_at]) if start is not None else ""
    room = max(200, limit - len(summary) - 2)
    return (truncate(detail, room) + "\n\n" if detail else "") + summary


@tool(
    name="code.run_tests",
    tier=Tier.WRITE,
    params={
        "path": "Project folder",
        "target": "Optional: one test file, folder, or node id, e.g. 'tests/test_x.py::test_y'",
        "runner": "'auto', 'pytest', or 'npm'",
        "timeout": "Seconds before the run is killed (max 600)",
        "max_chars": "Cap on the returned failure report",
    },
    undo="nothing to undo beyond caches the test suite writes",
)
def code_run_tests(
    ctx, path: str = ".", target: str = "", runner: str = "auto", timeout: int = 300, max_chars: int = 4000
) -> str:
    """Run a project's tests and return only what failed. Use to reproduce a bug and to check a fix."""
    project = ctx.check_path(path)
    if not project.is_dir():
        raise ToolError(f"{project} is not a directory")
    runner = runner.lower().strip()
    if runner == "auto":
        runner = _detect_runner(project)
    if target:
        ctx.check_path(project / target.split("::", 1)[0])
        if target.startswith("-"):
            raise ToolError("target is a test path, not an option")

    if runner == "pytest":
        argv = [_python_for(project), "-m", "pytest", "-q", "--tb=short", "-rfE",
                "--color=no", "-p", "no:cacheprovider"]
        argv += [target] if target else []
    elif runner == "npm":
        argv = [shutil.which("npm") or "npm", "test", "--silent"]
        argv += ["--", target] if target else []
    else:
        raise ToolError("runner must be 'auto', 'pytest', or 'npm'")

    started = time.monotonic()
    result = run(ctx, argv, cwd=project, timeout=timeout)
    took = f"{time.monotonic() - started:.1f}s"
    ctx.log(f"{runner} in {project} -> exit {result.code} ({took})")

    last = next((l for l in reversed(result.text.splitlines()) if l.strip()), "")
    if result.code == 0:
        return f"PASS ({runner}, {took}): {last.strip('= ')}"
    if runner == "pytest" and result.code == 5:
        return f"no tests were collected in {project}{' for ' + target if target else ''}"
    if runner == "pytest" and "No module named pytest" in result.text:
        raise ToolError(f"pytest is not installed for {argv[0]}")

    report = _pytest_failures(result.out, max_chars) if runner == "pytest" else truncate(result.text, max_chars, keep="tail")
    return ctx.scrub(f"FAIL ({runner}, exit {result.code}, {took})\n{report}")


# -- apply_patch -----------------------------------------------------------

def _git(ctx, root: Path, *args: str) -> str:
    result = run(ctx, ["git", *args], cwd=root, timeout=30)
    if result.code != 0:
        raise ToolError(f"git {args[0]}: {truncate(result.text.strip(), 1500)}")
    return result.out


def _patched_paths(patch: str) -> list[str]:
    paths = []
    for line in patch.splitlines():
        m = DIFF_PATH.match(line)
        if m and m.group(1) != "/dev/null" and m.group(1) not in paths:
            paths.append(m.group(1))
    return paths


@tool(
    name="code.apply_patch",
    tier=Tier.DANGER,
    params={
        "patch": "A unified diff (as produced by `git diff` or `diff -u`), paths relative to the repo root",
        "repo": "Path inside the git repository",
    },
    undo="git apply -R <the saved .patch file named in the result>",
)
def code_apply_patch(ctx, patch: str, repo: str = ".") -> str:
    """Apply a unified diff to the working tree. Only works on a feature branch -- create one with git.branch first."""
    start = ctx.check_path(repo)
    if not start.is_dir():
        raise ToolError(f"{start} is not a directory")
    root = Path(_git(ctx, start, "rev-parse", "--show-toplevel").strip()).resolve()

    branch = _git(ctx, root, "branch", "--show-current").strip()
    protected = [b.lower() for b in feature_setting(ctx, "code.protected_branches", ["main", "master"])]
    if not branch:
        raise ToolError("HEAD is detached -- create a branch with git.branch first")
    if branch.lower() in protected:
        raise ToolError(f"refusing to patch '{branch}' -- create a branch with git.branch first")

    files = _patched_paths(patch)
    if not files:
        raise ToolError("that is not a unified diff: no '--- a/...' / '+++ b/...' file headers found")
    for f in files:
        resolved = ctx.check_path(root / f)
        if root not in resolved.parents:
            raise ToolError(f"patch touches {f}, which is outside the repository")

    saved = ctx.state_dir("code") / f"{time.strftime('%Y%m%d-%H%M%S')}.patch"
    saved.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8", newline="\n")

    check = run(ctx, ["git", "apply", "--check", "--recount", "--whitespace=nowarn", str(saved)], cwd=root)
    if check.code != 0:
        saved.unlink()
        raise ToolError(f"patch does not apply cleanly, nothing changed:\n{truncate(check.text, 1500)}")
    _git(ctx, root, "apply", "--recount", "--whitespace=nowarn", str(saved))

    ctx.log(f"patched {len(files)} file(s) on {branch}")
    stat = _git(ctx, root, "diff", "--stat", "--", *files).strip()
    return f"applied to {len(files)} file(s) on branch {branch}\n{stat}\nundo: git apply -R '{saved}'"
