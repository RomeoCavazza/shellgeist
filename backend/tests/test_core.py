"""Tests essentiels pour ShellGeist - version consolidée et minimale."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from shellgeist.diff.apply import PatchApplyError, apply_unified_diff
from shellgeist.diff.guards import enforce_guards
from shellgeist.protocol import handle_request
from shellgeist.util_json import loads_obj

# =============================================================================
# Diff Application
# =============================================================================

def test_apply_insert_into_empty():
    """Test insertion dans fichier vide."""
    old = ""
    diff = "@@ -0,0 +1,1 @@\n+hello\n"
    new = apply_unified_diff(old, diff)
    assert new == "hello\n"


def test_apply_context_mismatch_raises():
    """Test rejet si contexte ne correspond pas."""
    old = "a\nb\n"
    diff = "@@ -1,2 +1,2 @@\n a\n-BOOM\n+b\n"
    with pytest.raises(PatchApplyError):
        apply_unified_diff(old, diff)


def test_apply_rejects_empty_hunk():
    """Test rejet des hunks vides."""
    old = ""
    diff = "@@ -0,0 +1,1 @@\n"
    with pytest.raises(PatchApplyError):
        apply_unified_diff(old, diff)


# =============================================================================
# Guards
# =============================================================================

def test_guards_block_control_chars():
    """Test blocage des caractères de contrôle."""
    ok, why = enforce_guards(relpath="x.txt", instruction="edit", old="abc\n", new="a\x01bc\n")
    assert ok is False
    assert "control" in why


def test_guards_readme_rewrite_blocked():
    """Test protection spéciale pour README.md."""
    old = "hello\n" * 200
    new = "totally different\n" * 200
    ok, why = enforce_guards(relpath="README.md", instruction="Add heading", old=old, new=new)
    assert ok is False
    assert "README rewrite blocked" in why


# =============================================================================
# Protocol
# =============================================================================

def test_protocol_ping():
    """Test commande ping."""
    out = asyncio.run(handle_request({"cmd": "ping"}))
    assert out["type"] == "result"
    assert out["ok"] is True


def test_protocol_git_status_outside_repo(tmp_path: Path):
    """Test git_status en dehors d'un repo git."""
    res = asyncio.run(handle_request({"cmd": "git_status", "root": str(tmp_path)}))
    assert res["type"] == "result"
    assert res["ok"] is True
    assert res.get("inside_git") is False


# =============================================================================
# JSON Parsing
# =============================================================================

def test_loads_obj_plain():
    """Test parsing JSON simple."""
    assert loads_obj('{"diff": "x"}')["diff"] == "x"


def test_loads_obj_code_fence():
    """Test parsing avec code fences."""
    s = """```json
{"diff": "abc"}
```"""
    assert loads_obj(s)["diff"] == "abc"


def test_loads_obj_unquoted_key():
    """Test réparation des clés non-quotées."""
    s = '{diff: "hello"}'
    assert loads_obj(s)["diff"] == "hello"

