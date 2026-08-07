import json

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
