"""
The Context object -- everything a feature is allowed to reach.

Every tool receives `ctx` as its first argument. If a capability is not on
this object, a feature is not supposed to have it. That is the whole point:
features get a curated surface, not the framework's internals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import ActionResult, Status


class Context:
    def __init__(
        self,
        *,
        run_id: str,
        config,
        memory,
        audit,
        approver,
        brain=None,
        executor=None,
        redactor=None,
        quiet: bool = False,
    ):
        self.run_id = run_id
        self.config = config
        self.memory = memory
        self.root: Path = config.root
        self._audit = audit
        self._approver = approver
        self._brain = brain
        self._executor = executor
        self._redactor = redactor
        self._quiet = quiet

    # -- talking to the human ---------------------------------------------
    def log(self, message: str, **fields: Any) -> None:
        """Say something. Goes to the console and to the audit log."""
        if not self._quiet:
            print(f"    · {message}")
        self._audit.write("feature.log", self.run_id, message=message, **fields)

    def ask(self, question: str) -> bool:
        """Ask the human a yes/no question mid-tool. Blocks until answered."""
        self._audit.write("feature.ask", self.run_id, question=question)
        answer = self._approver.confirm(question)
        self._audit.write("feature.ask.answer", self.run_id, question=question, answer=answer)
        return answer

    # -- thinking ----------------------------------------------------------
    def think(self, prompt: str, *, smart: bool = False) -> str:
        """One-shot LLM call for a sub-question inside your tool.

        Input is redacted before it leaves the machine. Returns plain text.
        """
        if self._brain is None:
            raise RuntimeError("no brain attached to this context (offline run?)")
        return self._brain.complete(prompt, smart=smart)

    def see(self, image_path: "str | Path", prompt: str) -> str:
        """Ask the vision model what is in an image.

        NOTE: the image itself cannot be redacted. Whatever is on screen goes
        to Groq as-is. Say so in the docstring of any tool that calls this.
        """
        if self._brain is None:
            raise RuntimeError("no brain attached to this context (offline run?)")
        return self._brain.see(image_path, prompt)

    def transcribe(self, audio_path: "str | Path") -> str:
        """Speech to text, via Groq-hosted Whisper. Audio is uploaded as-is."""
        if self._brain is None:
            raise RuntimeError("no brain attached to this context (offline run?)")
        return self._brain.transcribe(audio_path)

    # -- calling other tools ----------------------------------------------
    def call(self, tool_name: str, **args: Any) -> ActionResult:
        """Call another registered tool. Still goes through governance.

        Use this to compose features instead of importing each other.
        """
        if self._executor is None:
            return ActionResult(Status.BLOCKED, error="no executor attached to this context")
        return self._executor.run(tool_name, args, ctx=self, rationale=f"called by {tool_name}")

    # -- safety helpers ----------------------------------------------------
    def check_path(self, path: "str | Path") -> Path:
        """Resolve a user-supplied path, refusing anything policy forbids.

        Call this in every tool that touches the filesystem. It is the one
        guard the framework cannot apply for you, because only your tool
        knows which argument is a path.
        """
        from .sdk import ToolError

        if self._redactor is not None and self._redactor.path_is_forbidden(path):
            raise ToolError(
                f"'{path}' is inside a forbidden location "
                f"(redaction.forbidden_paths in config/policy.yaml). Refusing."
            )
        return Path(path).expanduser().resolve()

    def scrub(self, value: Any) -> Any:
        """Mask secrets in a value before logging or displaying it."""
        return self._redactor.scrub(value) if self._redactor else value

    # -- small conveniences ------------------------------------------------
    def state_dir(self, feature: str) -> Path:
        """A private scratch directory for your feature. Created on demand."""
        d = self.config.path("state_dir") / "features" / feature
        d.mkdir(parents=True, exist_ok=True)
        return d

    def secret(self, env_var: str) -> str | None:
        """Read an API key from the environment. Never hardcode one."""
        return self.config.secret(env_var)
