"""Smoke tests: verify every subpackage imports without errors."""
from __future__ import annotations


def test_import_root():
    import shellgeist
    assert hasattr(shellgeist, "__version__")


def test_import_agent():
    from shellgeist.agent.core import Agent
    assert callable(Agent)


def test_import_io():
    from shellgeist.io import UIEventEmitter, safe_drain, send_json
    assert callable(UIEventEmitter)
    assert callable(send_json)
    assert callable(safe_drain)


def test_import_llm():
    from shellgeist.llm import build_system_prompt, get_client
    assert callable(get_client)
    assert callable(build_system_prompt)


def test_import_protocol():
    from shellgeist.protocol import handle_request
    assert callable(handle_request)


def test_import_safety():
    from shellgeist.safety import (
        LoopGuard,
        RetryEngine,
        VerifyRuntime,
        is_blocked,
    )
    assert callable(LoopGuard)
    assert callable(RetryEngine)
    assert callable(VerifyRuntime)
    assert callable(is_blocked)


def test_import_session():
    from shellgeist.session import init_db, repair_conversation_history
    assert callable(init_db)
    assert callable(repair_conversation_history)


def test_import_tools():
    from shellgeist.tools import registry
    assert registry is not None
    # Tools should be registered via side-effect imports
    assert len(registry.get_tool_schemas()) > 0


def test_import_diff():
    from shellgeist.diff import apply_unified_diff, enforce_guards
    assert callable(apply_unified_diff)
    assert callable(enforce_guards)
