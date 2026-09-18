"""
Static checks on code the agent wrote for itself.

This runs BEFORE generated code is ever executed, and again before it is
installed. It is not a sandbox and does not pretend to be one -- a determined
adversary gets around AST checks. It is a guard against the realistic failure
here, which is not malice but a confused model writing something reckless:
shelling out with shell=True, editing its own policy file, silently declaring
a destructive tool as READ.

The important rule is TIER LAUNDERING (see _check_tier). A self-written tool
may not grant itself a permissive tier. It is pinned to the configured floor
-- DANGER by default -- so a generated tool always asks a human before it
runs. Lowering it is a deliberate edit a person makes in config/policy.yaml.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import Tier

# Modules a generated tool has no business importing.
FORBIDDEN_IMPORTS = {
    "servant.governance", "servant.executor", "servant.config", "servant.agent",
    "servant.audit", "servant.validator", "servant.sandbox", "servant.cli",
    "ctypes", "gc", "importlib", "marshal", "pickle", "pty", "socketserver",
}

# Names that are almost always a mistake in a tool.
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint", "globals", "vars"}

# Paths a generated tool may never write to, however it spells them.
PROTECTED_PATH_HINTS = {
    "config/policy.yaml", "policy.yaml", ".servant/stop", "servant/", ".env",
    ".git/", "audit.jsonl", "requirements.txt",
}


@dataclass
class Finding:
    line: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: [{self.code}] {self.message}"


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            return f"clean ({len(self.tools)} tool(s): {', '.join(self.tools) or 'none'})"
        return f"{len(self.findings)} problem(s):\n  " + "\n  ".join(str(f) for f in self.findings)


def validate_source(source: str, *, tier_floor: Tier = Tier.DANGER) -> ValidationReport:
    """Check generated feature source. Returns findings; empty means it passed."""
    report = ValidationReport()

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        report.findings.append(Finding(exc.lineno or 0, "SYNTAX", f"will not parse: {exc.msg}"))
        return report

    for node in ast.walk(tree):
        _check_imports(node, report)
        _check_calls(node, report)
        _check_strings(node, report)

    _check_tools(tree, report, tier_floor)
    return report


# --------------------------------------------------------------------------

def _check_imports(node: ast.AST, report: ValidationReport) -> None:
    names: list[str] = []
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        names = [node.module or ""]
    else:
        return

    for name in names:
        root = name.split(".")[0]
        if name in FORBIDDEN_IMPORTS or (root == "servant" and name != "servant.sdk"):
            report.findings.append(Finding(
                getattr(node, "lineno", 0), "IMPORT",
                f"may not import {name!r}. Features import from servant.sdk only.",
            ))
        elif root in FORBIDDEN_IMPORTS:
            report.findings.append(Finding(
                getattr(node, "lineno", 0), "IMPORT", f"may not import {root!r}",
            ))


def _check_calls(node: ast.AST, report: ValidationReport) -> None:
    if not isinstance(node, ast.Call):
        return

    name = _call_name(node.func)
    if name in FORBIDDEN_CALLS:
        report.findings.append(Finding(
            node.lineno, "CALL", f"{name}() is not allowed in a generated tool",
        ))

    # shell=True turns any string into a command line.
    if name and name.startswith(("subprocess.", "os.")):
        for keyword in node.keywords:
            if keyword.arg == "shell" and getattr(keyword.value, "value", False) is True:
                report.findings.append(Finding(
                    node.lineno, "SHELL",
                    "shell=True is banned -- pass a list of arguments instead",
                ))
    if name in {"os.system", "os.popen", "os.execv", "os.fork", "os.kill"}:
        report.findings.append(Finding(node.lineno, "SHELL", f"{name}() is banned"))


def _check_strings(node: ast.AST, report: ValidationReport) -> None:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return
    lowered = node.value.lower()
    for hint in PROTECTED_PATH_HINTS:
        if hint in lowered:
            report.findings.append(Finding(
                node.lineno, "PATH",
                f"references a protected path ({hint!r}). A tool may not touch "
                f"the agent's own policy, audit log, source or git data.",
            ))
            return


def _check_tools(tree: ast.Module, report: ValidationReport, tier_floor: Tier) -> None:
    """Every function must be a @tool, and none may under-declare its tier."""
    found_any = False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorator = _tool_decorator(node)
        if decorator is None:
            continue

        found_any = True
        report.tools.append(_tool_name(decorator, node))
        _check_tier(decorator, node, report, tier_floor)

        args = node.args.args
        if not args or args[0].arg not in {"ctx", "context"}:
            report.findings.append(Finding(
                node.lineno, "SIGNATURE",
                f"{node.name}: first argument must be 'ctx'",
            ))

    if not found_any:
        report.findings.append(Finding(
            0, "NOTOOL", "no @tool function found -- generated code must register a tool",
        ))


def _check_tier(decorator: ast.Call, node: ast.AST, report: ValidationReport, floor: Tier) -> None:
    """A self-written tool may not grant itself a permissive tier."""
    declared: str | None = None
    for keyword in decorator.keywords:
        if keyword.arg == "tier":
            declared = _tier_name(keyword.value)

    if declared is None:
        report.findings.append(Finding(
            getattr(node, "lineno", 0), "TIER",
            f"must declare tier=Tier.{floor.name} explicitly",
        ))
        return

    try:
        tier = Tier[declared.upper()] if declared.isalpha() else Tier(declared)
    except (KeyError, ValueError):
        report.findings.append(Finding(
            getattr(node, "lineno", 0), "TIER", f"unrecognised tier {declared!r}",
        ))
        return

    if tier.rank < floor.rank:
        report.findings.append(Finding(
            getattr(node, "lineno", 0), "TIER",
            f"declared tier '{tier.value}' is below the floor for generated code "
            f"('{floor.value}'). A tool the agent wrote for itself must ask a human "
            f"before it runs; lower it by hand in config/policy.yaml if you trust it.",
        ))


# --------------------------------------------------------------------------

def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return f"{_call_name(func.value)}.{func.attr}" if isinstance(
            func.value, (ast.Name, ast.Attribute)
        ) else func.attr
    return ""


def _tool_decorator(node: ast.AST) -> ast.Call | None:
    for decorator in getattr(node, "decorator_list", []):
        if isinstance(decorator, ast.Call) and _call_name(decorator.func).endswith("tool"):
            return decorator
    return None


def _tool_name(decorator: ast.Call, node: ast.AST) -> str:
    for keyword in decorator.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return getattr(node, "name", "?").replace("_", ".")


def _tier_name(value: ast.AST) -> str | None:
    if isinstance(value, ast.Attribute):     # Tier.DANGER
        return value.attr
    if isinstance(value, ast.Constant):      # "danger"
        return str(value.value)
    return None


def validate_file(path: Path, *, tier_floor: Tier = Tier.DANGER) -> ValidationReport:
    return validate_source(Path(path).read_text(encoding="utf-8"), tier_floor=tier_floor)
