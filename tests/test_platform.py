"""Things that only break on someone else's machine: old Pythons, Windows codepages."""

from __future__ import annotations

import io
import sys

from servant import cli


def test_cli_output_survives_a_legacy_codepage(monkeypatch):
    """A pipe on Windows defaults to cp1252, which has no arrow character."""
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    cli._utf8_output()
    print("  \u2192 files.list(path=.)")
    sys.stdout.flush()
    assert "\u2192".encode("utf-8") in raw.getvalue()


def test_contracts_use_no_runtime_type_unions():
    """`A | B` between classes evaluates at import and needs 3.10. Keep 3.9 working."""
    from pathlib import Path

    source = (Path(__file__).parent.parent / "servant" / "contracts.py").read_text(encoding="utf-8")
    assert "= ToolCall | Finish" not in source
