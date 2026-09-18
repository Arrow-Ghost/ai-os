"""
Running code the agent wrote, without trusting it.

What this gives you:
  * a separate process, so a crash or a hang cannot take the agent down
  * a wall-clock timeout and a CPU limit, so an infinite loop dies
  * an address-space cap, so a runaway allocation dies
  * a file-size cap, so it cannot fill the disk
  * a scratch working directory, so relative writes land somewhere harmless
  * a stub Context, so a test run cannot touch real memory, the audit log,
    the network or the model

What this does NOT give you, and you should say so out loud:
  * it is not a container and not a kernel boundary. The code runs as your
    user and can read your files if it asks for absolute paths.
  * network egress is not blocked. Doing that properly needs namespaces
    (bwrap, nsjail) or a container, which is the right production answer.

So this is a guard against a confused tool, not a malicious one. The real
protection against malicious code is that a human reads the diff before
`skill.install` promotes anything, and that generated tools are pinned to a
tier that always asks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HARNESS = '''
import json, sys, tempfile
from pathlib import Path

PROJECT_ROOT, MODULE_PATH, TOOL_NAME, ARGS_JSON = sys.argv[1:5]
sys.path.insert(0, PROJECT_ROOT)

class SandboxMemory:
    """Throwaway memory. Nothing a test run remembers survives it."""
    def __init__(self): self._d = {}
    def remember(self, key, value): self._d[key] = value
    def recall(self, key, default=None): return self._d.get(key, default)
    def forget(self, key): self._d.pop(key, None)
    def all_facts(self): return dict(self._d)
    def record_outcome(self, *a, **k): pass
    def success_rate(self, tool): return None

class SandboxContext:
    """Looks like a Context, reaches nothing real."""
    def __init__(self, workdir):
        self.run_id = "sandbox"
        self.memory = SandboxMemory()
        self.root = Path(workdir)
        self.logs = []
    def log(self, message, **fields):
        self.logs.append(str(message))
        print(f"[log] {message}", file=sys.stderr)
    def ask(self, question):
        raise RuntimeError("ctx.ask() is not available in a sandbox test run")
    def think(self, prompt, smart=False):
        raise RuntimeError("ctx.think() is not available in a sandbox test run")
    def see(self, image_path, prompt):
        raise RuntimeError("ctx.see() is not available in a sandbox test run")
    def transcribe(self, audio_path):
        raise RuntimeError("ctx.transcribe() is not available in a sandbox test run")
    def call(self, tool_name, **args):
        raise RuntimeError("ctx.call() is not available in a sandbox test run")
    def check_path(self, path):
        from servant.sdk import ToolError
        resolved = Path(path).expanduser().resolve()
        for bad in (".ssh", ".aws", ".gnupg", ".env"):
            if bad in str(resolved):
                raise ToolError(f"{path} is a forbidden location")
        return resolved
    def scrub(self, value): return value
    def state_dir(self, feature):
        d = self.root / feature
        d.mkdir(parents=True, exist_ok=True)
        return d
    def secret(self, env_var):
        return None  # a test run never sees real credentials

def main():
    import importlib.util
    from servant.registry import REGISTRY

    spec = importlib.util.spec_from_file_location("sandboxed_feature", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sandboxed_feature"] = module
    spec.loader.exec_module(module)

    spec_obj = REGISTRY.get(TOOL_NAME)
    if spec_obj is None:
        available = ", ".join(REGISTRY.names()) or "none"
        raise SystemExit(f"tool {TOOL_NAME!r} not registered. Registered: {available}")

    ctx = SandboxContext(tempfile.mkdtemp())
    result = spec_obj.func(ctx, **json.loads(ARGS_JSON))
    print("__RESULT__" + json.dumps({"output": str(result), "logs": ctx.logs}))

main()
'''


@dataclass
class SandboxResult:
    ok: bool
    output: str = ""
    error: str = ""
    logs: list[str] | None = None
    timed_out: bool = False

    def summary(self) -> str:
        if self.ok:
            return f"PASS -> {self.output[:400]}"
        if self.timed_out:
            return f"TIMEOUT -- {self.error}"
        return f"FAIL -- {self.error[:600]}"


def _servant_root() -> Path:
    """Where the `servant` package actually lives.

    The sandbox child has to import servant.sdk, so it needs this on sys.path.
    It is NOT the same as config.root -- the agent can be pointed at another
    working directory, and using that would leave the child unable to import
    the SDK at all.
    """
    return Path(__file__).resolve().parent.parent


def run_tool(
    module_path: Path,
    tool_name: str,
    args: dict,
    *,
    project_root: Path | None = None,
    timeout: int = 20,
    memory_mb: int = 512,
    max_file_mb: int = 50,
) -> SandboxResult:
    """Execute one tool from a module file, in a limited subprocess."""
    module_path = Path(module_path)
    if not module_path.is_file():
        return SandboxResult(False, error=f"no such module: {module_path}")
    project_root = Path(project_root) if project_root else _servant_root()

    with tempfile.TemporaryDirectory(prefix="servant-sandbox-") as workdir:
        harness = Path(workdir) / "_harness.py"
        harness.write_text(HARNESS)

        try:
            completed = subprocess.run(
                [sys.executable, str(harness), str(project_root), str(module_path),
                 tool_name, json.dumps(args)],
                capture_output=True,
                timeout=timeout,
                cwd=workdir,                       # relative writes land here
                preexec_fn=_limits(timeout, memory_mb, max_file_mb) if os.name == "posix" else None,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": workdir,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                False, error=f"still running after {timeout}s -- killed", timed_out=True
            )

        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")

        marker = "__RESULT__"
        if marker in stdout:
            payload = json.loads(stdout.split(marker, 1)[1].strip())
            return SandboxResult(True, output=payload["output"], logs=payload.get("logs", []))

        # A negative return code is a signal. The resource limits kill with
        # SIGKILL/SIGXCPU/SIGSEGV, and "exited -9" helps nobody.
        killed = {
            -9: f"killed: exceeded the CPU limit ({timeout}s) or memory cap ({memory_mb}MB)",
            -24: f"killed: exceeded the CPU limit ({timeout}s)",
            -11: "killed: segmentation fault",
            -25: f"killed: tried to write a file larger than {max_file_mb}MB",
        }.get(completed.returncode)
        if killed:
            return SandboxResult(False, error=killed, timed_out=completed.returncode in (-9, -24))

        detail = (stderr or stdout).strip() or f"exited {completed.returncode} with no output"
        return SandboxResult(False, error=detail)


def _limits(cpu_seconds: int, memory_mb: int, max_file_mb: int):
    def apply() -> None:  # pragma: no cover -- runs in the child process
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))

    return apply
