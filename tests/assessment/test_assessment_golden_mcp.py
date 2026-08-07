"""Golden behavior-identity corpus (spec §13). Pins classify_mcp/compose_mcp outputs
BEFORE the registry refactor; Task 6 keeps these green (minus added marker_tier/NAME keys).
"""
import json

from assessor.assessment.classify_mcp import classify
from assessor.assessment.compose_mcp import compose

CORPUS = {
    "strong-dep": {"package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1.0"}}),
                   "src/index.ts": 'srv.registerTool("search", {});'},
    "manifest": {"mcp.json": "{}", "src/server.py": 'Tool(name="fetch")'},
    "readme-prose-only": {"README.md": "# thing\nan MCP server for x"},          # R3-grandfathered weak
    "construct-only": {"src/server.ts": "const s = new McpServer({});"},
    "readme-plus-construct": {"README.md": "an MCP server", "src/s.py": "m = FastMCP()"},
    "nothing": {"README.md": "a plain library"},
}

# (is_asset, asset_type, confidence) pinned from CURRENT behavior:
EXPECTED = {
    "strong-dep": (True, "mcp_server", 0.95),
    "manifest": (True, "mcp_server", 0.95),
    "readme-prose-only": (True, "mcp_server", 0.6),
    "construct-only": (True, "mcp_server", 0.6),
    "readme-plus-construct": (True, "mcp_server", 0.6),
    "nothing": (False, "not_an_asset", 0.0),
}


def _norm_classify(r):
    """Task 6 changes classify to return Match|None; map both shapes to one view."""
    if r is None:
        return (False, "not_an_asset", 0.0)
    conf = r.get("confidence", {"strong": 0.95, "weak": 0.6}.get(r.get("marker_tier"), 0.0))
    return (bool(r.get("is_asset", True)), r["asset_type"], conf)


def test_golden_classification_corpus():
    for name, content in CORPUS.items():
        assert _norm_classify(classify(content)) == EXPECTED[name], name


def test_golden_compose_shape_stable():
    out = compose(CORPUS["strong-dep"])
    assert [t["name"] for t in out["tools"]] == ["search"]
    assert set(out) >= {"transport", "auth", "tools", "tools_complete", "tools_incomplete_reason"}
