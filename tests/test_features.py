"""The built-in features, run for real against throwaway folders and repos."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys

import pytest

from servant.agent import Agent
from servant.brain import OfflineBrain
from servant.config import Config
from servant.contracts import Status
from servant.governance import AutoApprover

MODULES = ["files", "notes", "organize", "git_tools", "shell", "code_tools", "sys_tools"]
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _unload_features():
    for name in [m for m in sys.modules if m == "features" or m.startswith("features.")]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def features(clean_registry):
    """conftest empties the registry before each test; import the features fresh
    so their @tool decorators run again, and unload them afterwards so other
    tests that build their own `features` package are not handed this one."""
    _unload_features()
    for name in MODULES:
        importlib.import_module(f"features.{name}")
    yield
    _unload_features()


@pytest.fixture
def shell_agent(config):
    data = {**config.data, "features": {"shell": {"allowlist": ["git --version", "git status"]}}}
    cfg = Config(data=data, root=config.root, source="test")
    return Agent(cfg, approver=AutoApprover(announce=False), brain=OfflineBrain(), quiet=True)


def call(agent, tool, **args):
    return agent.call_tool(tool, args)


def ok(agent, tool, **args) -> str:
    result = call(agent, tool, **args)
    assert result.status is Status.OK, result.error
    return result.output


def err(agent, tool, **args) -> str:
    result = call(agent, tool, **args)
    assert result.status is Status.ERROR, f"expected an error, got {result.status}: {result.output}"
    return result.error


# -- files -----------------------------------------------------------------

def test_files_list_and_read(agent, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("api_key = sk-abcdefghijklmnopqrstuv\nhello")
    listing = ok(agent, "files.list", path=str(tmp_path))
    assert "sub/" in listing and "a.txt" in listing
    assert listing.index("sub/") < listing.index("a.txt"), "folders first"

    text = ok(agent, "files.read", path=str(tmp_path / "a.txt"))
    assert "hello" in text and "sk-abc" not in text


def test_files_read_refuses_binary_and_forbidden(agent, tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / ".env").write_text("SECRET=1")
    assert "binary" in err(agent, "files.read", path=str(tmp_path / "blob.bin"))
    assert "forbidden" in err(agent, "files.read", path=str(tmp_path / ".env"))
    assert ".env" not in ok(agent, "files.list", path=str(tmp_path), pattern="*")


def test_files_move_never_overwrites(agent, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    assert "already exists" in err(agent, "files.move", src=str(tmp_path / "a.txt"), dst=str(tmp_path / "b.txt"))
    assert (tmp_path / "b.txt").read_text() == "b"

    ok(agent, "files.mkdir", path=str(tmp_path / "into" / "deep"))
    out = ok(agent, "files.move", src=str(tmp_path / "a.txt"), dst=str(tmp_path / "into"))
    assert (tmp_path / "into" / "a.txt").exists() and "undo: files.move" in out


def test_files_copy_and_refuse_precious(agent, tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.txt").write_text("x")
    ok(agent, "files.copy", src=str(tmp_path / "d"), dst=str(tmp_path / "d2"))
    assert (tmp_path / "d2" / "x.txt").read_text() == "x"
    assert "inside itself" in err(agent, "files.copy", src=str(tmp_path / "d"), dst=str(tmp_path / "d" / "d"))
    assert "refusing" in err(agent, "files.move", src=str(tmp_path), dst=str(tmp_path.parent / "elsewhere"))


def test_files_trash_needs_a_human(agent, tmp_path):
    (tmp_path / "keep.txt").write_text("k")
    assert call(agent, "files.trash", path=str(tmp_path / "keep.txt")).status is Status.DENIED
    assert (tmp_path / "keep.txt").exists()


def test_files_trash_and_restore_round_trip(yes_agent, tmp_path):
    target = tmp_path / "report.txt"
    target.write_text("important")
    out = ok(yes_agent, "files.trash", path=str(target))
    assert not target.exists()
    item_id = out.split("id: ")[1].split()[0]

    assert item_id in ok(yes_agent, "files.restore")
    ok(yes_agent, "files.restore", item=item_id)
    assert target.read_text() == "important"
    assert ok(yes_agent, "files.restore") == "trash is empty"


# -- notes -----------------------------------------------------------------

def test_notes_save_refuses_to_overwrite(agent):
    ok(agent, "notes.save", title="todo", body="one")
    assert "already exists" in err(agent, "notes.save", title="todo", body="two")


# -- organize --------------------------------------------------------------

def _messy(folder):
    for name in ("a.jpg", "b.PNG", "c.pdf", "d.zip", "e.weird", ".hidden"):
        (folder / name).write_text(name)


def test_organize_plan_is_a_dry_run(agent, tmp_path):
    _messy(tmp_path)
    out = ok(agent, "organize.plan", path=str(tmp_path))
    assert "DRY RUN" in out and "Images: 2" in out and "Other: 1" in out
    assert (tmp_path / "a.jpg").exists() and not (tmp_path / "Images").exists()
    assert ".hidden" not in out


def test_organize_apply_needs_a_human(agent, tmp_path):
    _messy(tmp_path)
    plan_id = ok(agent, "organize.plan", path=str(tmp_path)).split("plan id: ")[1].split()[0]
    assert call(agent, "organize.apply", plan_id=plan_id).status is Status.DENIED
    assert (tmp_path / "a.jpg").exists()


def test_organize_apply_moves_exactly_the_plan(yes_agent, tmp_path):
    _messy(tmp_path)
    plan_id = ok(yes_agent, "organize.plan", path=str(tmp_path)).split("plan id: ")[1].split()[0]
    (tmp_path / "c.pdf").write_text("edited after the plan was made")

    out = ok(yes_agent, "organize.apply", plan_id=plan_id)
    assert (tmp_path / "Images" / "a.jpg").exists()
    assert (tmp_path / "Archives" / "d.zip").exists()
    assert (tmp_path / "c.pdf").exists(), "a file that changed since the plan must be skipped"
    assert "changed since the plan" in out

    manifest = json.loads(open(out.split("undo manifest: ")[1].strip()).read())
    assert len(manifest) == 4
    assert "already applied" in err(yes_agent, "organize.apply", plan_id=plan_id)


# -- git -------------------------------------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "app.py").write_text("def add(a, b):\n    return a - b\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "initial")
    return path


@needs_git
def test_git_read_tools(agent, repo):
    assert "working tree clean" in ok(agent, "git.status", repo=str(repo))
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    assert "app.py" in ok(agent, "git.status", repo=str(repo))
    assert "+    return a + b" in ok(agent, "git.diff", repo=str(repo))
    assert "initial" in ok(agent, "git.log", repo=str(repo))
    assert "not a directory" in err(agent, "git.status", repo=str(repo / "missing"))


@needs_git
def test_git_branch_stage_commit(agent, repo):
    ok(agent, "git.branch", name="fix/add", repo=str(repo))
    assert "not a valid branch name" in err(agent, "git.branch", name="-bad", repo=str(repo))
    assert "nothing is staged" in err(agent, "git.commit", message="x", repo=str(repo))

    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    assert "app.py" in ok(agent, "git.stage", paths=["app.py"], repo=str(repo))
    out = ok(agent, "git.commit", message="fix add", repo=str(repo))
    assert "fix add" in out and "undo: git reset --soft HEAD~1" in out
    assert ok(agent, "git.log", repo=str(repo), n=1).endswith("fix add")


@needs_git
def test_git_push_is_gated_then_works(agent, yes_agent, repo, tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))

    assert call(agent, "git.push", repo=str(repo)).status is Status.DENIED
    ok(yes_agent, "git.push", repo=str(repo))

    # Verify with ls-remote rather than `git branch` with cwd set to the bare
    # repo: a machine with safe.bareRepository=explicit refuses that, exits
    # 128, and hands back an empty stdout that looks exactly like "the push
    # did not happen". Check the return code too, so a real failure here can
    # never be silent again.
    probe = subprocess.run(
        ["git", "ls-remote", "--heads", str(remote)],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert "refs/heads/main" in probe.stdout


# -- shell -----------------------------------------------------------------

def test_shell_which(agent):
    assert "not found" in ok(agent, "shell.which", name="definitely-not-a-program-xyz")
    assert "not a plain program name" in err(agent, "shell.which", name="rm -rf")


@needs_git
def test_shell_run_allowlist(shell_agent, tmp_path):
    assert "git version" in ok(shell_agent, "shell.run", command="git --version", cwd=str(tmp_path))
    assert "not on the allowlist" in err(shell_agent, "shell.run", command="git push", cwd=str(tmp_path))
    assert "not allowed" in err(shell_agent, "shell.run", command="git status && git push")
    assert "not allowed" in err(shell_agent, "shell.run", command="git status | more")
    assert "forbidden" in err(shell_agent, "shell.run", command="git status .env", cwd=str(tmp_path))


def test_shell_run_empty_allowlist_refuses_everything(yes_agent):
    assert "not on the allowlist" in err(yes_agent, "shell.run", command="git --version")


def test_shell_run_needs_a_human(agent):
    assert call(agent, "shell.run", command="git --version").status is Status.DENIED


# -- code ------------------------------------------------------------------

def test_code_read_range(agent, tmp_path):
    src = tmp_path / "m.py"
    src.write_text("".join(f"line{i}\n" for i in range(1, 501)))
    out = ok(agent, "code.read_range", path=str(src), start=10, end=12)
    assert "lines 10-12 of 500" in out and "10 | line10" in out and "line13" not in out
    capped = ok(agent, "code.read_range", path=str(src), start=1, end=10_000)
    assert "lines 1-400" in capped
    assert "only 500 lines" in err(agent, "code.read_range", path=str(src), start=900)


def test_code_run_tests_returns_failures_only(agent, tmp_path):
    (tmp_path / "test_math.py").write_text(
        "def test_good():\n    assert 1 + 1 == 2\n\n"
        "def test_bad():\n    assert 1 + 1 == 3, 'arithmetic is broken'\n"
    )
    out = ok(agent, "code.run_tests", path=str(tmp_path))
    assert out.startswith("FAIL (pytest")
    assert "test_bad" in out and "arithmetic is broken" in out
    assert "1 failed, 1 passed" in out

    passing = ok(agent, "code.run_tests", path=str(tmp_path), target="test_math.py::test_good")
    assert passing.startswith("PASS") and "1 passed" in passing


PATCH = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


@needs_git
def test_code_apply_patch_refuses_main(yes_agent, repo):
    assert "create a branch" in err(yes_agent, "code.apply_patch", patch=PATCH, repo=str(repo))
    assert "a - b" in (repo / "app.py").read_text()


@needs_git
def test_code_apply_patch_on_a_branch(yes_agent, repo):
    ok(yes_agent, "git.branch", name="fix", repo=str(repo))
    assert "not a unified diff" in err(yes_agent, "code.apply_patch", patch="just words", repo=str(repo))
    assert "does not apply" in err(yes_agent, "code.apply_patch", patch=PATCH.replace("a - b", "a * b"), repo=str(repo))

    out = ok(yes_agent, "code.apply_patch", patch=PATCH, repo=str(repo))
    assert "a + b" in (repo / "app.py").read_text()
    saved = out.split("git apply -R '")[1].rstrip("'")
    _git(repo, "apply", "-R", saved)
    assert "a - b" in (repo / "app.py").read_text(), "the saved patch must reverse cleanly"


# -- sys -------------------------------------------------------------------

def test_sys_tools(agent, tmp_path):
    pytest.importorskip("psutil")
    assert "processes" in ok(agent, "sys.processes", limit=3)
    assert "free of" in ok(agent, "sys.disk", path=str(tmp_path))
    assert "battery" in ok(agent, "sys.battery")
    assert "sort must be" in err(agent, "sys.processes", sort="size")
