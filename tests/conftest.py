"""Shared test fixtures. Everything runs offline, in a temp dir, with no API key."""

from __future__ import annotations

import pytest

from servant.agent import Agent
from servant.config import Config, DEFAULT_POLICY
from servant.governance import AutoApprover, DenyAllApprover
from servant.brain import OfflineBrain
from servant.registry import REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test starts with an empty tool registry."""
    REGISTRY.clear()
    yield
    REGISTRY.clear()


@pytest.fixture
def config(tmp_path):
    data = {
        **DEFAULT_POLICY,
        "paths": {
            "state_dir": ".servant",
            "audit_log": ".servant/audit.jsonl",
            "memory_db": ".servant/memory.db",
            "features_dir": "features",
        },
    }
    return Config(data=data, root=tmp_path, source="test")


@pytest.fixture
def agent(config):
    return Agent(config, approver=DenyAllApprover(), brain=OfflineBrain(), quiet=True)


@pytest.fixture
def yes_agent(config):
    return Agent(config, approver=AutoApprover(announce=False), brain=OfflineBrain(), quiet=True)


@pytest.fixture
def feature():
    """Load a module from features/ against the freshly-cleared registry.

    The autouse clean_registry fixture empties REGISTRY between tests, but
    Python caches imported modules, so a plain import would not re-run the
    @tool decorators. Reloading does.
    """
    import importlib
    import sys

    def _load(module_name: str):
        dotted = f"features.{module_name}"
        # Import registers once. If it is already cached from an earlier test,
        # reload instead -- doing both would register every tool twice.
        if dotted in sys.modules:
            return importlib.reload(sys.modules[dotted])
        return importlib.import_module(dotted)

    return _load
