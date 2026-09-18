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
