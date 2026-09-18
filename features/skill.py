"""
Skill acquisition: the agent writing new tools for itself.

THE DISTINCTION THAT MATTERS
The agent may extend itself with NEW TOOLS. It may not modify its own core,
its governance layer or its policy. Those are different things that sound
alike. Writing a new tool is what a junior does; rewriting the executor is
the convergent drive this whole project inverts.

THE PIPELINE
    skill.acquire  research -> write -> validate -> sandbox-test -> quarantine
    (a human reads the diff)
    skill.install  quarantine -> features/  [DANGER, asks every time]

Four properties keep this safe:

1. TIER FLOOR. Generated tools are pinned to DANGER, so anything the agent
   taught itself asks a human before it runs. It cannot grant itself READ --
   the validator rejects that as tier laundering. Lowering a tier is an edit
   a person makes in config/policy.yaml.

2. QUARANTINE. Generated code lands in .servant/quarantine, which the feature
   loader does not read. Nothing the agent writes becomes callable until a
   human runs skill.install and approves it.

3. SANDBOX, AND PROOF OF IT. Draft code is executed only in a limited
   subprocess with a stub context -- no real memory, no network calls through
   the brain, no credentials, CPU and memory capped. A draft is tested from a
   .pending file and only reaches quarantine if it PASSES, with the verdict
   stamped into its header. skill.install refuses anything without that
   record, so a failed attempt -- or a file someone dropped in by hand --
   cannot be installed.

4. THE GENERATED-CODE GATE. Self-written tools ask before every run even
   under --full-access. Otherwise the agent could write a tool and run it in
   the same turn, unseen. Governance recognises them from the provenance
   header, never from what the tool claims about itself. See
   governance.generated_code_always_asks.

NOTE FOR FEATURE AUTHORS: this module imports servant.validator and
servant.sandbox, which ordinary features must not do. It is first-party
plumbing. Do not copy this import pattern into your own feature -- use
servant.sdk only.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from servant.sandbox import run_tool
from servant.sdk import Tier, ToolError, tool
from servant.validator import validate_source

SDK_SPEC = '''\
Write ONE Python module that registers ONE tool. Follow this contract exactly.

    from servant.sdk import tool, Tier, ToolError

    @tool(
        name="<area>.<verb>",          # unique, dotted, e.g. "pdf.count_pages"
        tier=Tier.DANGER,              # REQUIRED: must be exactly Tier.DANGER
        params={"arg": "description for the model"},
        undo="one line on how a human reverses this",
    )
    def area_verb(ctx, arg: str, optional: int = 5) -> str:
        """One line saying what it does AND when to use it."""
        ...
        return "a short human-readable result string"

HARD RULES
- First argument is always `ctx`. Every other argument needs a type hint from:
  str, int, float, bool, list, dict. No *args, no **kwargs.
- The names above (`arg`, `optional`, `area_verb`) are PLACEHOLDERS. Use real
  names for this capability. Do not copy them, and do not add an argument the
  function does not actually use.
- tier MUST be Tier.DANGER. Any lower value is rejected.
- Import ONLY from servant.sdk plus the Python standard library.
- Never import servant.governance, servant.config, servant.executor, importlib,
  ctypes, pickle or marshal.
- Never import subprocess, socket, smtplib, ftplib, urllib.request, requests,
  or asyncio. If the task needs the network, email or a shell command, call
  the existing tool for it instead: ctx.call("web.fetch", url=...),
  ctx.call("mail.send", to=..., subject=..., body=...),
  ctx.call("shell.run", command=...). This is not optional -- those imports
  are rejected before your code ever runs.
- Never call eval, exec, compile or __import__.
- Never use shell=True. Pass subprocess a list of arguments.
- Never read or write: config/policy.yaml, .env, .servant/STOP, the servant/
  directory, .git/, or the audit log.
- Raise ToolError("what failed and what would fix it") for expected failures.
- Return a string. Keep it short -- it is fed back into a language model.
- Use ctx.log("..."), ctx.memory.remember/recall, ctx.check_path(p) for any
  path argument, and ctx.secret("ENV_VAR") for credentials.

Output ONLY the module source code. No explanation, no markdown fences.
'''


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _quarantine(ctx) -> Path:
    directory = ctx.config.path("state_dir") / "quarantine"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _tier_floor(ctx) -> Tier:
    return Tier(ctx.config.get("skills.tier_floor", "danger"))


def _slug(capability: str) -> str:
    words = re.findall(r"[a-z0-9]+", capability.lower())
    return "_".join(words[:4]) or "skill"


def _strip_fences(text: str) -> str:
    """Models wrap code in ``` even when told not to."""
    fenced = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return (fenced.group(1) if fenced else text).strip()


def _first_tool_name(source: str) -> str | None:
    match = re.search(r'@tool\(\s*(?:\n\s*)?name\s*=\s*["\']([^"\']+)["\']', source)
    return match.group(1) if match else None


def _research(ctx, capability: str) -> str:
    """Look the thing up before writing code about it. Best effort."""
    try:
        result = ctx.call("web.search", query=f"python {capability} example", max_results=3)
    except Exception as exc:  # noqa: BLE001
        return f"(research skipped: {exc})"
    if not result.ok:
        return f"(research unavailable: {result.error})"
    ctx.log("researched the capability before drafting")
    return result.output[:2500]


# --------------------------------------------------------------------------
# acquire
# --------------------------------------------------------------------------

@tool(
    name="skill.acquire",
    tier=Tier.WRITE,
    params={
        "capability": "What the new tool should do, in one sentence",
        "test_args": 'JSON object of arguments to test it with, e.g. {"n": 5}',
        "research": "Search the web for documentation before writing the code",
        "hints": "Extra detail: which library to use, edge cases, expected output",
    },
    undo="skill.discard with the name printed in the result",
)
def skill_acquire(
    ctx, capability: str, test_args: str = "{}", research: bool = False, hints: str = ""
) -> str:
    """Learn a capability the agent does not have yet by writing a new tool for it.

    Writes the tool, checks it statically, and runs it in a sandbox. The result
    lands in quarantine and is NOT callable until a human runs skill.install.
    Use this when no existing tool can do what was asked.
    """
    if not ctx.config.get("skills.enabled", True):
        raise ToolError("skill acquisition is disabled (skills.enabled in config/policy.yaml)")
    if not capability.strip():
        raise ToolError("capability is empty -- say what the tool should do")

    try:
        args = json.loads(test_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ToolError(f"test_args must be a JSON object like {{\"n\": 5}}: {exc}") from exc

    floor = _tier_floor(ctx)
    attempts = int(ctx.config.get("skills.max_attempts", 3))
    timeout = int(ctx.config.get("skills.sandbox_timeout", 20))
    existing = ", ".join(sorted(t.name for t in ctx.registry.all())) if hasattr(ctx, "registry") else ""

    context_block = _research(ctx, capability) if research else ""
    feedback = ""
    transcript: list[str] = []

    for attempt in range(1, attempts + 1):
        ctx.log(f"drafting attempt {attempt}/{attempts}")
        prompt = _build_prompt(capability, hints, context_block, existing, feedback, floor)
        source = _strip_fences(ctx.think(prompt, smart=True))

        report = validate_source(source, tier_floor=floor)
        if not report.ok:
            feedback = f"Your previous attempt was rejected:\n{report.summary()}\nFix every problem."
            reasons = "; ".join(f"{f.code} {f.message[:90]}" for f in report.findings[:3])
            transcript.append(f"attempt {attempt}: rejected by validator -- {reasons}")
            continue

        tool_name = _first_tool_name(source) or (report.tools[0] if report.tools else None)
        if not tool_name:
            feedback = "Could not find a tool name. Use @tool(name=\"area.verb\", ...)."
            transcript.append(f"attempt {attempt}: no tool name found")
            continue

        # Test from a pending file. Nothing reaches the quarantine folder until
        # it has actually passed, so a failed draft cannot sit there looking
        # installable.
        pending = _quarantine(ctx) / ".pending"
        pending.mkdir(parents=True, exist_ok=True)
        draft = pending / f"{_slug(capability)}.py"
        draft.write_text(source, encoding="utf-8")

        # No project_root: the sandbox finds the servant package itself, which
        # is not necessarily the agent's working root.
        sandbox = run_tool(draft, tool_name, args, timeout=timeout)
        draft.unlink(missing_ok=True)

        if sandbox.ok:
            candidate = _quarantine(ctx) / f"{_slug(capability)}.py"
            candidate.write_text(
                _provenance(capability, ctx.run_id, sandbox.summary(), args) + source,
                encoding="utf-8",
            )
            ctx.memory.remember(f"skill.draft.{candidate.stem}", {
                "capability": capability, "tool": tool_name, "path": str(candidate),
            })
            transcript.append(f"attempt {attempt}: validated and passed the sandbox")
            ctx.log(f"learned {tool_name} -> {candidate.name}")
            return (
                f"LEARNED {tool_name}\n"
                f"  file      {candidate}\n"
                f"  sandbox   {sandbox.summary()}\n"
                f"  attempts  {attempt}/{attempts}\n"
                f"  tier      {floor.value} (pinned -- it will ask before every run)\n\n"
                f"It is QUARANTINED and not callable yet. Review it with "
                f"skill.inspect name={candidate.stem}, then skill.install name={candidate.stem}."
            )

        feedback = f"Your previous attempt failed when run:\n{sandbox.summary()}\nFix it."
        transcript.append(f"attempt {attempt}: sandbox failed -- {sandbox.error[:120]}")

    history = "\n  ".join(transcript)
    raise ToolError(
        f"could not learn {capability!r} in {attempts} attempts:\n  {history}\n"
        f"Try again with clearer hints, or simpler test_args."
    )


def _build_prompt(capability, hints, research, existing, feedback, floor) -> str:
    parts = [
        f"Write a tool for this capability: {capability}",
        SDK_SPEC.replace("Tier.DANGER", f"Tier.{floor.name}"),
    ]
    if hints:
        parts.append(f"HINTS FROM THE USER:\n{hints}")
    if research:
        parts.append(f"SEARCH RESULTS (may be irrelevant, use judgement):\n{research}")
    if existing:
        parts.append(f"TOOLS THAT ALREADY EXIST (do not duplicate these names):\n{existing}")
    if feedback:
        parts.append(f"IMPORTANT -- {feedback}")
    return "\n\n".join(parts)


VERIFIED_MARKER = "Sandbox: PASS"


def _provenance(capability: str, run_id: str, sandbox_summary: str, args: dict) -> str:
    """Header stamped into a draft that PASSED. skill.install requires it.

    Keeping the verdict in the file, rather than only in memory, means a draft
    cannot be installed merely because it is sitting in the quarantine folder.
    A failed attempt, or a file someone dropped there by hand, has no record
    of having been executed, and is refused.
    """
    return (
        f'"""Generated by servant.\n\n'
        f"Capability: {capability}\n"
        f"Run: {run_id}\n"
        f"{VERIFIED_MARKER} -- tested with {json.dumps(args)}\n"
        f"Result: {sandbox_summary[:200]}\n\n"
        f"Written by a language model, passed static validation and a sandboxed\n"
        f"test run. A human still has to read it before install.\n"
        f'"""\n'
    )


# --------------------------------------------------------------------------
# review and install
# --------------------------------------------------------------------------

@tool(name="skill.list", tier=Tier.READ)
def skill_list(ctx) -> str:
    """List skills waiting in quarantine and skills already installed."""
    quarantined = sorted(_quarantine(ctx).glob("*.py"))
    features_dir = ctx.root / ctx.config.get("paths.features_dir", "features")
    installed = sorted(p.stem for p in features_dir.glob("*.py") if "Generated by servant" in p.read_text(errors="replace"))

    lines = []
    lines.append(f"QUARANTINED ({len(quarantined)}) -- not callable until installed:")
    for path in quarantined or []:
        lines.append(f"  {path.stem:<28} {path.stat().st_size} bytes")
    if not quarantined:
        lines.append("  (none)")

    lines.append(f"\nINSTALLED ({len(installed)}):")
    for name in installed or []:
        lines.append(f"  {name}")
    if not installed:
        lines.append("  (none)")
    return "\n".join(lines)


@tool(
    name="skill.inspect",
    tier=Tier.READ,
    params={"name": "The quarantined skill's file name, without .py"},
)
def skill_inspect(ctx, name: str) -> str:
    """Show the source of a quarantined skill. Read this before installing anything."""
    candidate = _quarantine(ctx) / f"{Path(name).stem}.py"
    if not candidate.is_file():
        raise ToolError(f"no quarantined skill named {name!r} (see skill.list)")
    return candidate.read_text(encoding="utf-8")


@tool(
    name="skill.test",
    tier=Tier.WRITE,
    params={"name": "Quarantined skill file name", "args": "JSON object of arguments"},
    undo="nothing to undo -- it runs in a sandbox",
)
def skill_test(ctx, name: str, args: str = "{}") -> str:
    """Run a quarantined skill in the sandbox again, with different arguments."""
    candidate = _quarantine(ctx) / f"{Path(name).stem}.py"
    if not candidate.is_file():
        raise ToolError(f"no quarantined skill named {name!r} (see skill.list)")

    source = candidate.read_text(encoding="utf-8")
    tool_name = _first_tool_name(source)
    if not tool_name:
        raise ToolError(f"{name} does not declare a tool name")

    try:
        parsed = json.loads(args or "{}")
    except json.JSONDecodeError as exc:
        raise ToolError(f"args must be a JSON object: {exc}") from exc

    result = run_tool(
        candidate, tool_name, parsed,
        timeout=int(ctx.config.get("skills.sandbox_timeout", 20)),
    )
    return f"{tool_name}: {result.summary()}"


@tool(
    name="skill.install",
    tier=Tier.DANGER,
    params={"name": "Quarantined skill file name, without .py"},
    undo="delete the file from features/ and restart the agent",
)
def skill_install(ctx, name: str) -> str:
    """Promote a quarantined skill into features/, making it a real capability.

    THE AGENT WROTE THIS CODE. Read it with skill.inspect first. Once installed
    it loads on every run, though it stays tiered so it still asks before acting.
    """
    candidate = _quarantine(ctx) / f"{Path(name).stem}.py"
    if not candidate.is_file():
        raise ToolError(f"no quarantined skill named {name!r} (see skill.list)")

    source = candidate.read_text(encoding="utf-8")

    if VERIFIED_MARKER not in source:
        raise ToolError(
            f"refusing to install {name!r}: it carries no record of passing a sandbox "
            f"run. Only a draft that was actually executed and returned a result is "
            f"installable. Run skill.acquire again, and check skill.inspect."
        )

    report = validate_source(source, tier_floor=_tier_floor(ctx))
    if not report.ok:
        raise ToolError(f"refusing to install -- it no longer validates:\n{report.summary()}")

    tool_name = _first_tool_name(source)
    if tool_name and ctx.registry.get(tool_name) is not None:
        raise ToolError(
            f"a tool named {tool_name!r} is already registered. Rename it in the "
            f"quarantined file, or remove the existing one first."
        )

    features_dir = ctx.root / ctx.config.get("paths.features_dir", "features")
    features_dir.mkdir(parents=True, exist_ok=True)
    destination = features_dir / candidate.name
    if destination.exists():
        raise ToolError(f"{destination.name} already exists in features/ -- rename or remove it")

    # Print the whole thing. Whatever the approval settings are, nobody should
    # be able to say afterwards that they never got to see it.
    ctx.log(f"promoting {tool_name} -- full source follows")
    print("\n" + "-" * 62)
    print(f"  SOURCE OF {tool_name}  (written by the agent)")
    print("-" * 62)
    for number, line in enumerate(source.splitlines(), 1):
        print(f"  {number:>3} | {line}")
    print("-" * 62 + "\n")

    shutil.copy2(candidate, destination)
    candidate.unlink()

    ctx.log(f"installed {tool_name} -> {destination}")
    return (
        f"installed {tool_name} into {destination}\n"
        f"It loads on the next run. It is tiered '{_tier_floor(ctx).value}', and because "
        f"the agent wrote it, it asks before every run even under --full-access "
        f"(governance.generated_code_always_asks)."
    )


@tool(
    name="skill.discard",
    tier=Tier.WRITE,
    params={"name": "Quarantined skill file name, without .py"},
    undo="skill.acquire it again",
)
def skill_discard(ctx, name: str) -> str:
    """Delete a quarantined skill that did not work out."""
    candidate = _quarantine(ctx) / f"{Path(name).stem}.py"
    if not candidate.is_file():
        raise ToolError(f"no quarantined skill named {name!r} (see skill.list)")
    candidate.unlink()
    ctx.memory.forget(f"skill.draft.{candidate.stem}")
    return f"discarded {candidate.stem}"
