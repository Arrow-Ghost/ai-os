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
    """Each test starts with an empty tool registry and no cached features.

    Purging `features*` from sys.modules matters because the loader test
    imports a DIFFERENT directory under the same package name; leaving that
    cached makes every later feature import resolve against a temp dir.
    """
    import sys

    def purge():
        for name in [m for m in sys.modules if m == "features" or m.startswith("features.")]:
            del sys.modules[name]

    # load_features() puts its directory on sys.path and leaves it there, which
    # is correct in production and poisonous in a test run: the loader test
    # points at a temp dir named "features" that would then shadow the real
    # package for every test after it.
    original_path = list(sys.path)

    REGISTRY.clear()
    purge()
    yield
    REGISTRY.clear()
    purge()
    sys.path[:] = original_path


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
        # clean_registry purged the cache, so a plain import registers exactly
        # once. Reload only if something else imported it during this test.
        if dotted in sys.modules:
            return importlib.reload(sys.modules[dotted])
        return importlib.import_module(dotted)

    return _load
