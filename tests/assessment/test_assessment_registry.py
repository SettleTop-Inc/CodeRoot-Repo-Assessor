from assessor.assessment.registry import (
    STRONG, WEAK, TIER_CONF, FULL_TAXONOMY, PRECEDENCE, REGISTRY_VERSION, _pick_primary, _merge_risk)
from assessor.assessment.shapes import fact, unknown


def _m(t, tier):
    return {"asset_type": t, "marker_tier": tier, "confidence": TIER_CONF[tier], "evidence": []}


def test_constants():
    assert STRONG == 0.95 and WEAK == 0.6 and REGISTRY_VERSION == 9
    assert len(FULL_TAXONOMY) == 8 and set(PRECEDENCE) == set(FULL_TAXONOMY)


def test_tier_beats_confidence_ordering():
    p, tiebroken, ties = _pick_primary([_m("mcp_server", "strong"), _m("agent", "weak")])
    assert p["asset_type"] == "mcp_server" and tiebroken is False and ties == []


def test_equal_tier_tiebreaks_by_precedence_and_flags_it():
    p, tiebroken, ties = _pick_primary([_m("mcp_server", "strong"), _m("agent", "strong")])
    assert p["asset_type"] == "agent" and tiebroken is True
    assert ties == ["agent", "mcp_server"]


def test_single_match_no_tiebreak():
    p, tiebroken, ties = _pick_primary([_m("agent", "weak")])
    assert p["asset_type"] == "agent" and not tiebroken


def test_merge_true_beats_false_both_orders():
    t = fact(True, "tools", [{"path": "c", "marker": "writes"}])
    f = fact(False, "tools (complete inventory)")
    for order in ([("agent", {"writes": t}), ("mcp_server", {"writes": f})],
                  [("mcp_server", {"writes": f}), ("agent", {"writes": t})]):
        out = _merge_risk(order)
        assert out["writes"]["value"] is True
        assert {"path": "c", "marker": "writes"} in out["writes"]["evidence"]


def test_merge_unknown_beats_false_both_orders_with_basis():
    u = unknown("not derivable: tool inventory incomplete")
    f = fact(False, "tools (complete inventory)")
    for order in ([("agent", {"writes": u}), ("mcp_server", {"writes": f})],
                  [("mcp_server", {"writes": f}), ("agent", {"writes": u})]):
        out = _merge_risk(order)
        assert out["writes"]["known_unknown"]            # NOT a bare fact(False)
        assert "mcp_server" in out["writes"]["basis"]    # who reported clean-over-complete


def test_merge_two_trues_concat_evidence_deterministically():
    a = fact(True, "s", [{"path": "a", "marker": "m"}])
    b = fact(True, "s", [{"path": "b", "marker": "m"}])
    o1 = _merge_risk([("agent", {"x": a}), ("mcp_server", {"x": b})])
    o2 = _merge_risk([("mcp_server", {"x": b}), ("agent", {"x": a})])
    assert o1["x"]["evidence"] == o2["x"]["evidence"] and len(o1["x"]["evidence"]) == 2


from assessor.assessment.registry import _apply_repo_shape_suppressor


def _src(paths):
    return {p: "x = 1" for p in paths}


def test_demo_name_drops_weak_nonexempt_and_discloses():
    matches = [{"asset_type": "agent", "marker_tier": "weak", "confidence": 0.6, "evidence": []}]
    kept, sup = _apply_repo_shape_suppressor(matches, "https://github.com/o/x-demo", _src(["src/a.py"]))
    assert kept == [] and sup == [{"asset_type": "agent",
                                   "reason": "demo/template shape — weak match suppressed"}]


def test_strong_survives_demo_name():
    matches = [{"asset_type": "agent", "marker_tier": "strong", "confidence": 0.95, "evidence": []}]
    kept, sup = _apply_repo_shape_suppressor(matches, "https://github.com/o/x-demo", _src(["src/a.py"]))
    assert kept == matches and sup == []


def test_examples_density_triggers_and_mcp_weak_exempt():
    content = _src(["examples/a.py", "examples/b.py", "src/c.py"])   # 2/3 >= 50%
    matches = [{"asset_type": "agent", "marker_tier": "weak", "confidence": 0.6, "evidence": []},
               {"asset_type": "mcp_server", "marker_tier": "weak", "confidence": 0.6, "evidence": []}]
    kept, sup = _apply_repo_shape_suppressor(matches, "https://github.com/o/real-name", content)
    assert [m["asset_type"] for m in kept] == ["mcp_server"]         # grandfathered (§4.3)
    assert sup[0]["asset_type"] == "agent"


def test_normal_repo_untouched():
    matches = [{"asset_type": "agent", "marker_tier": "weak", "confidence": 0.6, "evidence": []}]
    kept, sup = _apply_repo_shape_suppressor(matches, "https://github.com/o/agentapp", _src(["src/a.py"]))
    assert kept == matches and sup == []


# -- repo_is_scaffold (extracted verdict, shared by the suppressor + DP3 probe guard) --

from assessor.assessment.registry import repo_is_scaffold


def test_repo_is_scaffold_name_suffixes():
    for suffix in ("-template", "-example", "-starter", "-demo"):
        assert repo_is_scaffold(f"https://github.com/o/x{suffix}", _src(["src/a.py"]))


def test_repo_is_scaffold_example_density():
    content = _src(["examples/a.py", "examples/b.py", "src/c.py"])   # 2/3 >= 50%
    assert repo_is_scaffold("https://github.com/o/real-name", content)


def test_repo_is_scaffold_false_for_normal_repo():
    assert not repo_is_scaffold("https://github.com/o/agentapp", _src(["src/a.py"]))


def test_repo_is_scaffold_matches_suppressor_verdict():
    """Same predicate the suppressor uses — extracting it must not change behavior."""
    for url, content in (
        ("https://github.com/o/x-demo", _src(["src/a.py"])),
        ("https://github.com/o/real-name", _src(["examples/a.py", "examples/b.py", "src/c.py"])),
        ("https://github.com/o/agentapp", _src(["src/a.py"])),
    ):
        matches = [{"asset_type": "agent", "marker_tier": "weak", "confidence": 0.6, "evidence": []}]
        _, suppressed = _apply_repo_shape_suppressor(matches, url, content)
        assert bool(suppressed) == repo_is_scaffold(url, content)


# -- declared-identity discriminate suppressor (§8.3) ---------------------------

from assessor.assessment.registry import _apply_declared_identity_suppressor


def _strong_skill():
    return {"asset_type": "skill", "marker_tier": "strong", "confidence": 0.95,
            "evidence": [{"path": "SKILL.md", "marker": "skill manifest"}]}


def _weak_mcp_prose():
    return {"asset_type": "mcp_server", "marker_tier": "weak", "confidence": 0.6,
            "evidence": [{"path": "README.md", "marker": "mcp keyword"}]}


def _weak_mcp_construction():
    return {"asset_type": "mcp_server", "marker_tier": "weak", "confidence": 0.6,
            "evidence": [{"path": "src/server.ts", "marker": "server construction"}]}


def test_weak_keyword_only_mcp_suppressed_when_strong_skill_present():
    matches = [_strong_skill(), _weak_mcp_prose()]
    kept, suppressed = _apply_declared_identity_suppressor(
        matches, meta={"topics": ["agent-skills"], "description": "Agent Skills"})
    assert [m["asset_type"] for m in kept] == ["skill"]
    assert suppressed and suppressed[0]["asset_type"] == "mcp_server"


def test_corroborated_mcp_not_suppressed():
    matches = [_strong_skill(), _weak_mcp_prose()]
    kept, suppressed = _apply_declared_identity_suppressor(
        matches, meta={"topics": ["mcp-server"], "description": None})
    assert {m["asset_type"] for m in kept} == {"skill", "mcp_server"}
    assert suppressed == []


def test_corroborated_via_description_not_suppressed():
    matches = [_strong_skill(), _weak_mcp_prose()]
    kept, _ = _apply_declared_identity_suppressor(
        matches, meta={"topics": [], "description": "An MCP server for x"})
    assert {m["asset_type"] for m in kept} == {"skill", "mcp_server"}


def test_no_strong_competitor_keeps_weak():
    matches = [_weak_mcp_prose()]
    kept, suppressed = _apply_declared_identity_suppressor(matches, meta=None)
    assert [m["asset_type"] for m in kept] == ["mcp_server"]
    assert suppressed == []


def test_construction_based_weak_mcp_never_suppressed():
    """Real code signal (server construction) is not prose-only -> survives even
    with a strong competitor present and no corroborating declared identity."""
    matches = [_strong_skill(), _weak_mcp_construction()]
    kept, suppressed = _apply_declared_identity_suppressor(
        matches, meta={"topics": ["agent-skills"], "description": "Agent Skills"})
    assert {m["asset_type"] for m in kept} == {"skill", "mcp_server"}
    assert suppressed == []


def test_strong_match_never_suppressed():
    strong_mcp = {"asset_type": "mcp_server", "marker_tier": "strong", "confidence": 0.95,
                  "evidence": [{"path": "README.md", "marker": "mcp keyword"}]}
    matches = [_strong_skill(), strong_mcp]
    kept, suppressed = _apply_declared_identity_suppressor(matches, meta=None)
    assert {m["asset_type"] for m in kept} == {"skill", "mcp_server"}
    assert suppressed == []
