"""
Feature auto-discovery.

Everything in features/ is imported at startup, which triggers the @tool
decorators and fills the registry. A broken feature is reported and skipped --
it never takes down the agent or blocks another junior's work.

Both layouts work:
    features/hello.py                  a single-file feature
    features/inbox/__init__.py         a package, for anything bigger
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .registry import REGISTRY


@dataclass
class LoadReport:
    loaded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    tools_before: int = 0
    tools_after: int = 0

    @property
    def tools_added(self) -> int:
        return self.tools_after - self.tools_before

    def summary(self) -> str:
        parts = [f"{len(self.loaded)} feature module(s), {self.tools_added} tool(s)"]
        if self.failed:
            parts.append(f"{len(self.failed)} FAILED: {', '.join(self.failed)}")
        return " | ".join(parts)


def load_features(features_dir: Path, *, verbose: bool = False) -> LoadReport:
    report = LoadReport(tools_before=len(REGISTRY))
    features_dir = Path(features_dir)

    if not features_dir.exists():
        report.tools_after = len(REGISTRY)
        return report

    # Make the project root importable so features can `from servant.sdk import ...`
    root = str(features_dir.parent.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)

    package_name = features_dir.name
    try:
        package = importlib.import_module(package_name)
    except Exception as exc:  # noqa: BLE001
        report.failed[package_name] = f"{type(exc).__name__}: {exc}"
        report.tools_after = len(REGISTRY)
        return report

    for mod in pkgutil.iter_modules(package.__path__):
        if mod.name.startswith("_"):
            continue
        dotted = f"{package_name}.{mod.name}"
        try:
            importlib.import_module(dotted)
            report.loaded.append(dotted)
        except Exception as exc:  # noqa: BLE001 -- one bad feature must not break the rest
            report.failed[dotted] = f"{type(exc).__name__}: {exc}"
            if verbose:
                traceback.print_exc()

    report.tools_after = len(REGISTRY)
    return report
