"""Spec §7 trust rule, pinned end-to-end (Task 7).

Two structural properties, each closing a distinct attack surface Tasks 1-6 built
the machinery for but never pinned with a dedicated test:

(A) The declared-identity suppressor (registry.py, spec §8.3) may only KEEP or
    DROP a match classification already produced. `meta` (repo topics/description
    — and, by the same reasoning, a maintainer-authored record) can steer which
    weak matches survive, but it must never manufacture a brand-new match for a
    type nothing deterministic pointed at.

(B) An adversarial `asset-record.json` — one crafted to look like MCP/agent prose
    and to smuggle framework/dependency vocabulary that WOULD trip classification
    if it appeared anywhere else in the repo — changes nothing about the served
    assessment except `assessment.declared` itself. This is the composite
    end-to-end proof that channels 1 (classification reach) and 3 (prose/keyword
    scanning) both hold; channel 2 (marker-hit scanning) is pinned separately in
    test_record_marker_exclusion.py and channel 4 (budget-neutral selection) in
    test_record_selection.py.
"""
import json

from assessor.assessment import assemble
from assessor.assessment.record import RECORD_BASENAME

from conftest import _S


def test_suppressor_cannot_introduce_a_type():
    """_apply_declared_identity_suppressor may keep or drop, never add: meta
    declaring a type with NO match in `matches` must not create one."""
    from assessor.assessment.registry import _apply_declared_identity_suppressor

    # Real match-element shape, copied verbatim from test_assessment_registry.py's
    # `_strong_skill()` fixture (asset_type/marker_tier/confidence/evidence) —
    # no invented keys.
    matches = [{"asset_type": "skill", "marker_tier": "strong", "confidence": 0.95,
                "evidence": [{"path": "SKILL.md", "marker": "skill manifest"}]}]
    # meta declares mcp_server identity strongly (both topics AND description),
    # even though NO mcp_server match exists anywhere in `matches`.
    meta = {"description": "an mcp server for things", "topics": ["mcp", "mcp-server"]}
    kept, suppressed = _apply_declared_identity_suppressor(list(matches), meta)
    assert {m["asset_type"] for m in kept} == {"skill"}
    assert all(m["asset_type"] != "mcp_server" for m in kept)
    assert suppressed == []            # the lone match is strong -- nothing to suppress either


# A content fixture that actually classifies (mirrors Task 6's MCP_FIXTURE_CONTENT in
# test_record_declared_block.py, copied rather than imported so this file does not
# depend on that file's module layout), so the test below exercises a real
# `assemble.build` asset path, not `not_an_asset`.
MCP_FIXTURE_CONTENT = {
    "package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1.0"}}),
    "src/index.ts": 'srv.registerTool("search", {});',
}


def test_adversarial_record_changes_nothing_but_declared():
    """Spec §7 + §10: same repo with and without an adversarial record —
    det classification, fingerprint, marker-relevant outputs byte-identical."""
    base = dict(MCP_FIXTURE_CONTENT)               # a real fixture that classifies
    adversarial = json.dumps({
        "record_version": 1, "created_by": "x",
        "technologies": {"framework": "mcp server agent ANTHROPIC_BASE_URL",
                         "dependencies": ["langgraph", "crewai"]},
        "model_access": {"mode": "byo", "provider": None, "model": None}})
    # Trust channel 3 (prose): the adversarial body names exactly the words a
    # keyword/prose scanner would key on.
    assert "mcp server" in adversarial and "agent" in adversarial

    with_rec = dict(base)
    with_rec[RECORD_BASENAME] = adversarial

    a = assemble.build("https://github.com/o/n", base, "sha", None, settings=_S)
    b = assemble.build("https://github.com/o/n", with_rec, "sha", None, settings=_S)

    assert a["asset_types"] == b["asset_types"]
    assert a["asset_type"] == b["asset_type"]
    assert a["classification_confidence"] == b["classification_confidence"]
    assert a["content_fingerprint"] == b["content_fingerprint"]
    assert b["assessment"]["declared"] is not None    # non-vacuity: the record WAS read

    a_rest = {k: v for k, v in a["assessment"].items() if k != "declared"}
    b_rest = {k: v for k, v in b["assessment"].items() if k != "declared"}
    assert a_rest == b_rest
