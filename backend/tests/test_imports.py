"""Smoke test: all public modules import without error."""
from __future__ import annotations

import importlib

import pytest

_MODULES = [
    "shellgeist.agent",
    "shellgeist.agent.core",
    "shellgeist.agent.messages",
    "shellgeist.agent.orchestrator",
    "shellgeist.agent.state",
    "shellgeist.cli",
    "shellgeist.config",
    "shellgeist.diff",
    "shellgeist.diff.apply",
    "shellgeist.diff.guards",
    "shellgeist.io",
    "shellgeist.io.events",
    "shellgeist.io.results",
    "shellgeist.io.telemetry",
    "shellgeist.io.transport",
    "shellgeist.llm",
    "shellgeist.llm.client",
    "shellgeist.llm.prompt",
    "shellgeist.llm.stream",
    "shellgeist.protocol",
    "shellgeist.protocol.handler",
    "shellgeist.protocol.helpers",
    "shellgeist.protocol.models",
    "shellgeist.safety",
    "shellgeist.safety.blocked",
    "shellgeist.safety.loop_guard",
    "shellgeist.safety.retry",
    "shellgeist.safety.verify",
    "shellgeist.session",
    "shellgeist.session.ops",
    "shellgeist.session.repair",
    "shellgeist.session.store",
    "shellgeist.tools",
    "shellgeist.tools.base",
    "shellgeist.tools.coder",
    "shellgeist.tools.executor",
    "shellgeist.tools.fs",
    "shellgeist.tools.normalize",
    "shellgeist.tools.parser",
    "shellgeist.tools.policy",
    "shellgeist.tools.preview",
    "shellgeist.tools.runtime",
    "shellgeist.tools.shell",
    "shellgeist.util_git",
    "shellgeist.util_json",
    "shellgeist.util_path",
]


@pytest.mark.parametrize("module", _MODULES)
def test_import(module: str):
    importlib.import_module(module)
