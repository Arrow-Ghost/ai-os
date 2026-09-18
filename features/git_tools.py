"""
git.* -- read a repo freely, change it locally, and ask before anyone else sees it.

    git.status  git.diff  git.log     READ
    git.branch  git.stage  git.commit WRITE   local only; undone with git reset / switch
    git.push                          DANGER  other people see it

Every tool shells out to the real `git` with no shell, a timeout, and output
capped. Nothing here can force-push or rewrite published history.
"""

from __future__ import annotations

import re
from pathlib import Path

from servant.sdk import Tier, ToolError, tool

from ._util import run, truncate

BRANCH_RE = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,100}(?<![./])$")


def _repo(ctx, repo: str) -> Path:
    path = ctx.check_path(repo)
    if not path.is_dir():
        raise ToolError(f"{path} is not a directory")
    return path


def _git(ctx, repo: Path, *args: str, timeout: float = 30) -> str:
    result = run(ctx, ["git", *args], cwd=repo, timeout=timeout)
    if result.code != 0:
        detail = result.err.strip() or result.out.strip() or f"exit code {result.code}"
        if "not a git repository" in detail:
            raise ToolError(f"{repo} is not inside a git repository")
        raise ToolError(f"git {args[0]} failed: {truncate(detail, 1500)}")
    return result.out


def current_branch(ctx, repo: Path) -> str:
    """Empty string means detached HEAD."""
    return _git(ctx, repo, "branch", "--show-current").strip()


# -- READ ------------------------------------------------------------------

@tool(name="git.status", tier=Tier.READ, params={"repo": "Path inside the repository"})
def git_status(ctx, repo: str = ".") -> str:
    """Show the current branch and which files are changed, staged, or untracked."""
    out = _git(ctx, _repo(ctx, repo), "status", "--short", "--branch", "--untracked-files=normal")
    lines = out.rstrip().splitlines()
    if len(lines) <= 1:
        return (lines[0] if lines else "") + "\nworking tree clean"
    return truncate(out.rstrip(), 4000)


@tool(
    name="git.diff",
    tier=Tier.READ,
    params={
        "repo": "Path inside the repository",
        "path": "Limit the diff to this file or folder",
        "staged": "Show what is staged for the next commit instead of unstaged changes",
        "max_chars": "Truncate the diff after this many characters",
    },
)
def git_diff(ctx, repo: str = ".", path: str = "", staged: bool = False, max_chars: int = 6000) -> str:
    """Show line-by-line changes. Use before committing to check what will go in."""
    root = _repo(ctx, repo)
    args = ["diff", "--no-color", "--no-ext-diff"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", str(ctx.check_path(root / path))]
    out = _git(ctx, root, *args)
    if not out.strip():
        return "no staged changes" if staged else "no unstaged changes (try staged=true)"
    stat = _git(ctx, root, *args[:3], "--stat", *args[3:]).strip().splitlines()
    header = stat[-1].strip() if stat else ""
    return ctx.scrub(f"{header}\n\n{truncate(out, max_chars)}")


@tool(
    name="git.log",
    tier=Tier.READ,
    params={"repo": "Path inside the repository", "n": "How many commits (max 100)", "path": "Only commits touching this path"},
)
def git_log(ctx, repo: str = ".", n: int = 10, path: str = "") -> str:
    """Show recent commits: short hash, date, author, subject."""
    root = _repo(ctx, repo)
    n = max(1, min(int(n), 100))
    args = ["log", f"-n{n}", "--date=short", "--pretty=format:%h  %ad  %an  %s"]
    if path:
        args += ["--", str(ctx.check_path(root / path))]
    out = _git(ctx, root, *args).strip()
    return out or "no commits yet"


# -- WRITE -----------------------------------------------------------------

@tool(
    name="git.branch",
    tier=Tier.WRITE,
    params={"name": "New branch name", "repo": "Path inside the repository"},
    undo="git switch - && git branch -d <name>",
)
def git_branch(ctx, name: str, repo: str = ".") -> str:
    """Create a new branch from the current commit and switch to it. Do this before changing code."""
    if not BRANCH_RE.match(name) or name.endswith(".lock"):
        raise ToolError(f"{name!r} is not a valid branch name (letters, digits, . _ / -)")
    root = _repo(ctx, repo)
    previous = current_branch(ctx, root) or "(detached)"
    _git(ctx, root, "switch", "-c", name)
    ctx.log(f"created branch {name} from {previous}")
    return f"on new branch {name} (was on {previous})\nundo: git switch {previous} && git branch -d {name}"


@tool(
    name="git.stage",
    tier=Tier.WRITE,
    params={"paths": "Files or folders to stage; ['.'] for everything", "repo": "Path inside the repository"},
    undo="git restore --staged <paths>",
)
def git_stage(ctx, paths: list, repo: str = ".") -> str:
    """Stage files for the next commit. Check git.status first so you stage only what you mean to."""
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        raise ToolError("give at least one path, or ['.'] for everything")
    root = _repo(ctx, repo)
    checked = [str(ctx.check_path(root / str(p))) for p in paths]
    _git(ctx, root, "add", "--", *checked)
    staged = _git(ctx, root, "diff", "--cached", "--name-only").strip().splitlines()
    ctx.log(f"staged {len(staged)} file(s)")
    listing = "\n".join(f"  {s}" for s in staged[:50]) or "  (nothing -- the paths had no changes)"
    return f"staged for commit ({len(staged)}):\n{listing}\nundo: git restore --staged -- {' '.join(paths)}"


@tool(
    name="git.commit",
    tier=Tier.WRITE,
    params={"message": "Commit message: a short summary line, optionally a blank line and detail", "repo": "Path inside the repository"},
    undo="git reset --soft HEAD~1  (keeps the changes staged)",
)
def git_commit(ctx, message: str, repo: str = ".") -> str:
    """Commit what is staged. Stages nothing itself -- call git.stage first."""
    if not message.strip():
        raise ToolError("the commit message is empty")
    root = _repo(ctx, repo)
    if not _git(ctx, root, "diff", "--cached", "--name-only").strip():
        raise ToolError("nothing is staged -- call git.stage first")
    _git(ctx, root, "commit", "--quiet", "-m", message)
    summary = _git(ctx, root, "log", "-n1", "--stat", "--pretty=format:%h %s").strip()
    ctx.log(f"committed: {summary.splitlines()[0]}")
    return f"{truncate(summary, 2000)}\nundo: git reset --soft HEAD~1"


# -- DANGER ----------------------------------------------------------------

@tool(
    name="git.push",
    tier=Tier.DANGER,
    params={
        "repo": "Path inside the repository",
        "remote": "Remote name",
        "branch": "Branch to push; empty means the current branch",
    },
    undo="for a new branch: git push <remote> --delete <branch>; otherwise push a revert commit",
)
def git_push(ctx, repo: str = ".", remote: str = "origin", branch: str = "") -> str:
    """Push a branch to the remote. Other people will see it. Never forces."""
    root = _repo(ctx, repo)
    branch = branch or current_branch(ctx, root)
    if not branch:
        raise ToolError("HEAD is detached -- switch to a branch (git.branch) before pushing")
    if not BRANCH_RE.match(branch) or not re.fullmatch(r"[A-Za-z0-9._-]+", remote):
        raise ToolError("invalid branch or remote name")
    result = run(ctx, ["git", "push", "--set-upstream", remote, branch], cwd=root, timeout=120)
    text = result.text.strip()
    if result.code != 0:
        raise ToolError(f"push failed: {truncate(text, 1500)}")
    ctx.log(f"pushed {branch} to {remote}")
    return ctx.scrub(truncate(text, 2000)) or f"pushed {branch} to {remote}"
