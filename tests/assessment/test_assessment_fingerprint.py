from assessor.assessment.fingerprint import build_payload, compute_fingerprint


def _p(mod_order="a", leaf_order="a"):
    per_type = {"mcp_server": {"marker_tier": "strong", "transport": "stdio", "auth": None,
                               "tool_names": ["a", "b"] if leaf_order == "a" else ["b", "a"]},
                "agent": {"marker_tier": "strong", "framework": "langgraph", "models": [],
                          "tool_names": ["x"]}}
    checked = ["mcp_server", "agent"] if mod_order == "a" else ["agent", "mcp_server"]
    return build_payload(asset_types=["agent", "mcp_server"], types_checked=checked,
                         registry_version=2, per_type=per_type,
                         coordinates=[{"kind": "git", "ref": "r"}], spdx="MIT")


def test_registry_order_and_leaf_order_invariant():
    assert compute_fingerprint(_p("a", "a")) == compute_fingerprint(_p("b", "b"))


def test_registry_version_changes_hash():
    p2 = _p()
    p2["registry_version"] = 3
    assert compute_fingerprint(_p()) != compute_fingerprint(p2)


def test_types_checked_changes_hash():
    p2 = _p()
    p2["types_checked"] = ["mcp_server"]
    assert compute_fingerprint(_p()) != compute_fingerprint(p2)
