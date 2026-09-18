"""
Command line interface.

    python -m servant tools              list every registered capability
    python -m servant tools -v           ... with arguments and undo notes
    python -m servant call files.list path=/tmp
    python -m servant run "tidy my downloads folder"
    python -m servant stop               engage the kill switch
    python -m servant go                 release it
    python -m servant audit -n 20        show the tail of the audit log
    python -m servant audit --verify     check the hash chain
    python -m servant doctor             is this machine set up correctly?
"""

from __future__ import annotations

import argparse
import json
import sys

from .agent import build_agent
from .audit import AuditLog
from .config import PROJECT_ROOT, load_config
from .contracts import Tier
from .governance import (
    AutoApprover,
    ConsoleApprover,
    DenyAllApprover,
    KillSwitch,
    Redactor,
    Stopped,
)

TIER_COLOR = {"read": "\033[32m", "write": "\033[33m", "danger": "\033[31m", "forbidden": "\033[35m"}
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="servant", description="A governed laptop agent.")
    parser.add_argument("--policy", help="path to an alternative policy.yaml")
    parser.add_argument(
        "--full-access", action="store_true",
        help="run every tier automatically -- nothing asks. Kill switch and audit stay on.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_tools = sub.add_parser("tools", help="list registered tools")
    p_tools.add_argument("-v", "--verbose", action="store_true")

    p_call = sub.add_parser("call", help="run one tool directly, through governance")
    p_call.add_argument("tool")
    p_call.add_argument("args", nargs="*", help="key=value pairs")
    p_call.add_argument("--yes", action="store_true", help="auto-approve (scratch data only)")

    p_run = sub.add_parser("run", help="give the agent a goal")
    p_run.add_argument("goal", nargs="+")
    p_run.add_argument("--yes", action="store_true", help="auto-approve (scratch data only)")
    p_run.add_argument("--non-interactive", action="store_true", help="deny anything needing a human")

    sub.add_parser("stop", help="engage the kill switch")
    sub.add_parser("go", help="release the kill switch")

    p_audit = sub.add_parser("audit", help="inspect the audit log")
    p_audit.add_argument("-n", "--lines", type=int, default=15)
    p_audit.add_argument("--verify", action="store_true")
    p_audit.add_argument("--json", action="store_true")

    p_watch = sub.add_parser("watch", help="run registered monitors in the background")
    p_watch.add_argument("--once", action="store_true", help="single pass, then exit (for cron)")
    p_watch.add_argument("--yes", action="store_true", help="auto-approve (scratch data only)")
    p_watch.add_argument("--quiet", action="store_true", help="only print when something fires")

    sub.add_parser("doctor", help="check the environment")
    sub.add_parser("memory", help="show what the agent remembers")

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except Stopped as stop:
        # A clean exit, not a crash. The kill switch working is success.
        print(f"\n[STOPPED] {stop}\n{DIM}release it with: python -m servant go{RESET}")
        return 130
    except KeyboardInterrupt:
        print("\n[interrupted]")
        return 130


def _dispatch(args) -> int:
    if args.command == "stop":
        return _cmd_stop(args)
    if args.command == "go":
        return _cmd_go(args)
    if args.command == "audit":
        return _cmd_audit(args)
    if args.command == "doctor":
        return _cmd_doctor(args)

    # No terminal means nobody can answer an approval prompt, so deny by
    # default rather than hanging or crashing halfway through an action.
    interactive = sys.stdin is not None and sys.stdin.isatty()
    approver = (
        AutoApprover() if getattr(args, "yes", False)
        else DenyAllApprover() if getattr(args, "non_interactive", False) or not interactive
        else ConsoleApprover()
    )
    policy_file = args.policy
    if args.full_access and not policy_file:
        candidate = PROJECT_ROOT / "config" / "policy.full-access.yaml"
        policy_file = candidate if candidate.exists() else None

    agent = build_agent(policy_file=policy_file, approver=approver)

    if args.full_access:
        # Belt and braces: the flag must work even without the profile file.
        agent.policy.tier_policy = {
            "read": "auto", "write": "auto", "danger": "auto", "forbidden": "block",
        }
        agent.policy.confidence_floor = 0.0
        _full_access_banner(agent)

    if args.command == "tools":
        return _cmd_tools(agent, args)
    if args.command == "call":
        return _cmd_call(agent, args)
    if args.command == "run":
        return _cmd_run(agent, args)
    if args.command == "memory":
        return _cmd_memory(agent)
    if args.command == "watch":
        return _cmd_watch(agent, args)
    return 1


# --------------------------------------------------------------------------

def _full_access_banner(agent) -> None:
    """Make the mode impossible to miss -- including on a demo projector."""
    pinned = [f"{k}->{v}" for k, v in agent.policy.tier_overrides.items()]
    print(f"\n{TIER_COLOR['danger']}{BOLD}{'=' * 62}")
    print("  FULL ACCESS -- every tier runs automatically, nothing asks")
    print(f"{'=' * 62}{RESET}")
    print(f"  {DIM}stop:  touch .servant/STOP   or   Ctrl-C{RESET}")
    print(f"  {DIM}watch: tail -f {agent.audit.path}{RESET}")
    if pinned:
        print(f"  {DIM}pinned back: {', '.join(pinned)}{RESET}")
    print()


def _cmd_tools(agent, args) -> int:
    report = agent.load_report
    print(f"\n{BOLD}Registered capabilities{RESET}  {DIM}({report.summary() if report else ''}){RESET}\n")

    if not len(agent.registry):
        print(f"  {DIM}Nothing registered yet. Add a file to features/ -- see docs/FEATURE_GUIDE.md{RESET}\n")
        return 0

    for tier in (Tier.READ, Tier.WRITE, Tier.DANGER, Tier.FORBIDDEN):
        specs = agent.registry.by_tier(tier)
        if not specs:
            continue
        action = agent.policy.tier_policy.get(tier.value, "approve")
        color = TIER_COLOR.get(tier.value, "")
        print(f"  {color}{BOLD}{tier.value.upper()}{RESET} {DIM}-> {action}{RESET}")
        for spec in specs:
            effective = agent.policy.resolve_tier(spec)
            flag = f" {DIM}(overridden to {effective.value}){RESET}" if effective is not spec.tier else ""
            print(f"    {spec.name:<26} {spec.description[:60]}{flag}")
            if args.verbose:
                for p in spec.params:
                    req = "" if p.required else f" {DIM}= {p.default!r}{RESET}"
                    print(f"        {DIM}{p.name}: {p.type}{req}  {p.description}{RESET}")
                if spec.undo:
                    print(f"        {DIM}undo: {spec.undo}{RESET}")
                print(f"        {DIM}from: {spec.module}{RESET}")
        print()
    return 0


def _cmd_call(agent, args) -> int:
    spec = agent.registry.get(args.tool)
    if spec is None:
        print(f"unknown tool: {args.tool}\nrun `python -m servant tools` to see what exists")
        return 1

    types = {p.name: p.type for p in spec.params}
    kwargs = {}
    for raw in args.args:
        if "=" not in raw:
            print(f"arguments must be key=value, got: {raw!r}")
            return 1
        key, _, value = raw.partition("=")
        kwargs[key] = _coerce(value, types.get(key, "string"))

    result = agent.call_tool(args.tool, kwargs)
    print(f"\n[{result.status.value}] {result.duration_ms}ms")
    print(result.output or result.error or "")
    return 0 if result.ok else 1


def _cmd_run(agent, args) -> int:
    goal = " ".join(args.goal)
    print(f"\n{BOLD}goal:{RESET} {goal}")
    print(f"{DIM}stop at any time: touch .servant/STOP  (or Ctrl-C){RESET}")
    result = agent.run(goal)
    print(f"{DIM}run {result.run_id} · {result.steps} action(s) · audit: {agent.audit.path}{RESET}")
    return 1 if result.stopped else 0


def _cmd_watch(agent, args) -> int:
    from .watcher import run_forever, run_once

    if args.once:
        stats = run_once(agent, quiet=args.quiet)
        print(
            f"{stats.checks} check(s), {stats.fires} fire(s), "
            f"{stats.escalations} escalation(s), {stats.errors} error(s)"
        )
        return 0
    run_forever(agent, quiet=args.quiet)
    return 0


def _cmd_memory(agent) -> int:
    facts = agent.memory.all_facts()
    print(f"\n{BOLD}facts{RESET} ({len(facts)})")
    for key, value in facts.items():
        print(f"  {key:<30} {json.dumps(value, default=str)[:80]}")
    print(f"\n{BOLD}recent episodes{RESET}")
    for ep in agent.memory.recent_episodes(10):
        print(f"  {DIM}{ep['role']:<9}{RESET} {ep['content'][:90]}")
    print()
    return 0


def _cmd_stop(args) -> int:
    config = load_config(args.policy)
    KillSwitch(config.root / config.get("governance.killswitch_file")).engage("stopped from CLI")
    print("kill switch ENGAGED. The agent will refuse to act until you run `python -m servant go`.")
    return 0


def _cmd_go(args) -> int:
    config = load_config(args.policy)
    KillSwitch(config.root / config.get("governance.killswitch_file")).release()
    print("kill switch released.")
    return 0


def _cmd_audit(args) -> int:
    config = load_config(args.policy)
    log = AuditLog(config.path("audit_log"), redactor=Redactor.from_config(config))

    if args.verify:
        intact, message = log.verify()
        print(("OK  " if intact else "FAIL ") + message)
        return 0 if intact else 1

    rows = log.read(limit=args.lines)
    if not rows:
        print("audit log is empty")
        return 0
    for row in rows:
        if args.json:
            print(json.dumps(row))
            continue
        payload = json.dumps(row["payload"], default=str)
        print(f"{DIM}{row['ts'][11:23]}{RESET} {row['kind']:<16} {payload[:110]}")
    return 0


def _cmd_doctor(args) -> int:
    config = load_config(args.policy)
    print(f"\n{BOLD}servant doctor{RESET}\n")
    print(f"  policy source   {config.source}")
    print(f"  brain provider  {config.get('brain.provider')}")
    print(f"  GROQ_API_KEY    {'set' if config.secret('GROQ_API_KEY') else 'NOT set'}")
    print(f"  state dir       {config.path('state_dir')}")

    killed = KillSwitch(config.root / config.get("governance.killswitch_file"))
    print(f"  kill switch     {'ENGAGED -- run `servant go`' if killed.engaged else 'clear'}")

    for module in ("yaml", "openai"):
        try:
            __import__(module)
            print(f"  {module:<15} installed")
        except ImportError:
            print(f"  {module:<15} missing ({DIM}pip install -r requirements.txt{RESET})")

    agent = build_agent(policy_file=args.policy, approver=DenyAllApprover(), quiet=True)
    report = agent.load_report
    print(f"  features        {report.summary() if report else 'not loaded'}")
    print()
    return 1 if (report and report.failed) else 0


def _coerce(value: str, json_type: str):
    if json_type == "integer":
        return int(value)
    if json_type == "number":
        return float(value)
    if json_type == "boolean":
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if json_type in {"array", "object"}:
        return json.loads(value)
    return value


if __name__ == "__main__":
    sys.exit(main())
