"""mcp corroborate (declared identity) + marker-path context (spec §8.1/§8.2)."""
import json

from assessor.assessment.classify_mcp import classify


def test_weak_keyword_promoted_by_mcp_topic():
    r = classify({"README.md": "an mcp server"}, meta={"topics": ["mcp-server"], "description": None})
    assert r["marker_tier"] == "strong"


def test_weak_keyword_promoted_by_description():
    # NOTE: task-7-brief.md's Step-1 snippet used README.md="servers", which establishes
    # no weak marker at all (no "mcp server"/"an mcp"/"model-context-protocol" substring) —
    # inconsistent with the spec's own framing ("a *weak* match gets promoted"). Using a
    # real weak README keyword here so the test exercises promotion-of-an-existing-weak-match
    # via the description path specifically (topics left empty).
    r = classify({"README.md": "an mcp server"}, meta={"topics": [], "description": "Model Context Protocol Servers"})
    assert r["marker_tier"] == "strong"


def test_construction_in_examples_dir_ignored():
    r = classify({"examples/demo.py": "FastMCP('x')"}, meta=None)
    assert r is None   # teaching-path construction is not a ship signal


def test_construction_in_src_still_weak():
    r = classify({"src/server.py": "FastMCP('x')"}, meta=None)
    assert r and r["marker_tier"] == "weak"


def test_meta_none_weak_stays_weak():
    r = classify({"README.md": "an mcp server"}, meta=None)
    assert r and r["marker_tier"] == "weak"


def test_strong_dep_unaffected_by_meta_absence():
    content = {"package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1.0"}})}
    r = classify(content)
    assert r["marker_tier"] == "strong"


def test_meta_empty_dict_is_safe_no_promotion():
    r = classify({"src/server.py": "FastMCP('x')"}, meta={})
    assert r and r["marker_tier"] == "weak"


def test_teaching_dir_construction_does_not_block_other_weak_markers():
    # README keyword still fires weak even though the only construction hit is teaching-path.
    r = classify({"README.md": "an mcp server", "examples/demo.py": "FastMCP('x')"}, meta=None)
    assert r and r["marker_tier"] == "weak"


def test_corroborate_evidence_prefers_topics():
    r = classify(
        {"README.md": "an mcp server"},
        meta={"topics": ["mcp-server"], "description": "Model Context Protocol Servers"},
    )
    assert r["marker_tier"] == "strong"
    assert any(e.get("path") == "topics" for e in r["evidence"])


def test_declared_identity_alone_never_classifies():
    # Anti-gaming invariant (spec §5): declared identity only promotes/suppresses an
    # EXISTING marker, never classifies from declaration alone. A plain repo with an
    # mcp-server topic + "MCP server" description but ZERO file/prose/dep/construction
    # marker must stay not-an-asset.
    r = classify(
        {"README.md": "a plain library", "src/util.py": "def add(a, b): return a + b"},
        meta={"topics": ["mcp-server"], "description": "An MCP server for things"},
    )
    assert r is None


def test_teaching_only_construction_with_mcp_topic_still_none():
    # Even declared MCP identity cannot rescue a repo whose only construction hit is
    # confined to a teaching dir (no other marker) — nothing to promote.
    r = classify({"examples/demo.py": "FastMCP('x')"},
                 meta={"topics": ["mcp-server"], "description": None})
    assert r is None
