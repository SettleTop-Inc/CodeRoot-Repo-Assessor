import json

from assessor.assessment.classify_agent import _all_deps
from assessor.assessment.classify_mcp import classify


def test_strong_marker_sdk_dep():
    content = {"package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1.0"}})}
    r = classify(content)
    assert r["asset_type"] == "mcp_server" and r["marker_tier"] == "strong"
    assert any("@modelcontextprotocol/sdk" in e["marker"] for e in r["evidence"])


def test_weak_name_marker_is_weak_tier():
    r = classify({"README.md": "# my-mcp-thing\nan MCP server"})
    assert r["asset_type"] == "mcp_server" and r["marker_tier"] == "weak"   # R3-grandfathered


def test_no_markers_returns_none():
    assert classify({"README.md": "a plain python library"}) is None


def test_unpinned_python_mcp_dep_matches():
    content = {"pyproject.toml": 'dependencies = [\n    "mcp",\n]\n'}
    r = classify(content)
    assert r["asset_type"] == "mcp_server" and r["marker_tier"] == "strong"


def test_pinned_python_mcp_dep_matches_and_ties_unpinned_tier():
    """A version-pinned `mcp` dep must classify — and land on the SAME tier as the
    unpinned form. Before the fix, `'"mcp"' in pyproject` only ever caught the
    unpinned literal; `mcp>=2.0` (the real-world norm) fell through to None."""
    unpinned = classify({"pyproject.toml": 'dependencies = [\n    "mcp",\n]\n'})
    for spec in ('"mcp>=2.0"', '"mcp==1.2.0"', '"mcp~=1.0"', '"mcp[cli]>=1.0"'):
        content = {"pyproject.toml": f"dependencies = [\n    {spec},\n]\n"}
        r = classify(content)
        assert r is not None, f"{spec} should still classify as mcp_server"
        assert r["asset_type"] == "mcp_server"
        assert r["marker_tier"] == "strong" == unpinned["marker_tier"], \
            f"{spec} tier should match the unpinned tier"


def test_pinned_python_mcp_dep_matches_poetry_style():
    content = {"pyproject.toml": "[tool.poetry.dependencies]\nmcp = \"^2.0\"\n"}
    r = classify(content)
    assert r["asset_type"] == "mcp_server" and r["marker_tier"] == "strong"


def test_unrelated_pinned_deps_do_not_match():
    """A repo with NO mcp dependency at all must not be swept up by the fix — the reader
    must key on the exact dep name `mcp`, not merely see version-pin syntax."""
    content = {"pyproject.toml": 'dependencies = [\n    "httpx>=0.27",\n    "pydantic==2.0",\n]\n'}
    assert classify(content) is None


def test_unrelated_dep_name_containing_mcp_substring_does_not_match():
    """Guards against a naive substring check creeping back in: a dep whose name merely
    CONTAINS "mcp" (but isn't exactly "mcp") must not match."""
    content = {"pyproject.toml": 'dependencies = [\n    "fastmcp>=1.0",\n]\n'}
    assert classify(content) is None


def test_modelcontextprotocol_fallback_still_works():
    content = {"pyproject.toml": '# see https://modelcontextprotocol.io\n'}
    r = classify(content)
    assert r["asset_type"] == "mcp_server" and r["marker_tier"] == "strong"


def test_newline_mcp_fallback_still_works():
    """A bare `mcp = ...` key sitting outside any table `_all_deps` recognizes (no
    leading whitespace, right after a newline, and NOT inside `[tool.poetry.dependencies]`
    — so `_all_deps` cannot see it) — the raw-text fallback this fix must not remove."""
    content = {"pyproject.toml": '[project]\nmcp = "^2.0"\n'}
    assert "mcp" not in _all_deps(content), "test setup: _all_deps must NOT see this line"
    r = classify(content)
    assert r["asset_type"] == "mcp_server" and r["marker_tier"] == "strong"
