"""DP3: wire probes.detect into assemble.build — additive coverage_probes field
(spec §6). Load-bearing invariant: coverage_probes changes NOTHING about
classification — asset_types, primary type, compositions, risk, and
content_fingerprint are byte-identical with and without this feature.

Task 7 narrowed that invariant in exactly one place: a probe at
`evidence_state="candidate"` whose advisory LLM read returned a non-empty
`code_citation` may now be promoted into `asset_types` at weak tier
(`registry.promote_from_probes`). Every probe state exercised in THIS file
(`present_incomplete`, `absent`, `undetermined`) is still barred from
classification, and `content_fingerprint` is unmoved even by a promotion — see the
Task 7 block in test_assessment_assemble_multitype.py."""
import json

from assessor.assessment import assemble
import assessor.assessment.probes as probes_mod
from assessor.assessment.fingerprint import build_payload, compute_fingerprint
from assessor.assessment.registry import (
    TYPE_MODULES, TIER_CONF, REGISTRY_VERSION,
    _apply_repo_shape_suppressor, _apply_declared_identity_suppressor,
)
from assessor.assessment import coords, license as license_mod

from conftest import _S

FOO_MCP = {  # strong mcp match (sdk dep), zero tools registered -> present_incomplete probe
    "package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1"}}),
}

PLAIN = {"README.md": "just a plain library, nothing declared here"}


# -- 1. presence ---------------------------------------------------------------------

def test_coverage_probes_present_for_declared_incomplete_repo():
    rec = assemble.build("https://github.com/o/foo-mcp", FOO_MCP, "sha", None, settings=_S)
    probes = rec["assessment"]["coverage_probes"]
    assert len(probes) == 1
    p = probes[0]
    assert p["type"] == "mcp_server"
    assert p["evidence_state"] == "present_incomplete"


def test_coverage_probes_empty_when_nothing_declared():
    rec = assemble.build("https://github.com/o/widgetlib", PLAIN, "sha", None, settings=_S)
    assert rec["assessment"]["coverage_probes"] == []


# -- 2. additivity: the load-bearing invariant ----------------------------------------

def test_additivity_fingerprint_asset_types_confidence_risk_unchanged_by_probe(monkeypatch):
    # Same repo_url + same content (so coordinates/classification inputs are pinned —
    # repo_url legitimately feeds the fingerprint via coords.extract's `git` coordinate,
    # so varying repo_url would NOT isolate the probe's effect). Only probes.detect's
    # RESULT is toggled, to isolate whether coverage_probes leaks into anything else.
    repo_url = "https://github.com/o/foo-mcp"

    monkeypatch.setattr(assemble.probes, "detect",
                        lambda *a, **kw: [{"type": "mcp_server", "declared_by": ["name"],
                                           "evidence_state": "present_incomplete",
                                           "probe": "x", "llm_reconciliation": None}])
    fires = assemble.build(repo_url, FOO_MCP, "sha", None, settings=_S)

    monkeypatch.setattr(assemble.probes, "detect", lambda *a, **kw: [])
    silent = assemble.build(repo_url, FOO_MCP, "sha", None, settings=_S)

    assert fires["assessment"]["coverage_probes"] != []
    assert silent["assessment"]["coverage_probes"] == []

    assert fires["content_fingerprint"] == silent["content_fingerprint"]
    assert fires["asset_types"] == silent["asset_types"]
    assert fires["classification_confidence"] == silent["classification_confidence"]
    assert fires["assessment"]["risk"] == silent["assessment"]["risk"]


def test_additivity_real_probe_firing_vs_not_same_content_and_url():
    """Complement of the monkeypatched isolation above: with the REAL detector, toggle
    whether the probe fires via bucket_b's description alone (repo name "widgetlib"
    does not itself declare mcp, so description is the sole trigger) while holding
    repo_url and content fixed. The mcp match is already strong-tier from the
    package.json dependency, so the description never moves classification (the
    declared-identity promotion path only applies to weak matches) — asset_types,
    confidence, risk, and the fingerprint must stay byte-identical; only
    coverage_probes may differ."""
    repo_url = "https://github.com/o/widgetlib"
    fires = assemble.build(repo_url, FOO_MCP, "sha", None,
                           bucket_b={"description": "an mcp server", "topics": ["mcp"]}, settings=_S)
    silent = assemble.build(repo_url, FOO_MCP, "sha", None,
                            bucket_b={"description": "just some code", "topics": []}, settings=_S)

    assert fires["assessment"]["coverage_probes"] != []
    assert silent["assessment"]["coverage_probes"] == []

    assert fires["asset_types"] == silent["asset_types"]
    assert fires["classification_confidence"] == silent["classification_confidence"]
    assert fires["content_fingerprint"] == silent["content_fingerprint"]
    assert fires["assessment"]["risk"] == silent["assessment"]["risk"]


def test_fingerprint_matches_manual_payload_excluding_coverage_probes():
    """Reconstruct the classification payload independently (with zero knowledge of
    coverage_probes) and confirm it hashes to the same fingerprint assemble.build
    produced -- proving coverage_probes/name never reach build_payload."""
    repo_url = "https://github.com/o/foo-mcp"
    rec = assemble.build(repo_url, FOO_MCP, "sha", None, settings=_S)

    meta = {"description": None, "topics": [], "homepage": None}
    mods = {m.name: m for m in TYPE_MODULES}
    matches = [dict(r) for M in TYPE_MODULES
               if (r := M.classify(FOO_MCP, paths=(), meta=meta)) is not None]
    for m in matches:
        m["confidence"] = TIER_CONF[m["marker_tier"]]
    matches, _ = _apply_repo_shape_suppressor(matches, repo_url, FOO_MCP)
    matches, _ = _apply_declared_identity_suppressor(matches, meta)
    asset_types = sorted(m["asset_type"] for m in matches)
    compositions = {m["asset_type"]: mods[m["asset_type"]].compose(
        FOO_MCP, capped=False, paths=(), meta=meta) for m in matches}
    lic = license_mod.detect(FOO_MCP, None, repo_spdx=None)
    coordinates = coords.extract(FOO_MCP, repo_url)
    checked = sorted(m.name for m in TYPE_MODULES)
    per_type = {m["asset_type"]: mods[m["asset_type"]].fingerprint_facts(m, compositions[m["asset_type"]])
                for m in matches}
    payload = build_payload(asset_types=asset_types, types_checked=checked,
                            registry_version=REGISTRY_VERSION, per_type=per_type,
                            coordinates=coordinates, spdx=lic["spdx"].get("value"))
    assert compute_fingerprint(payload) == rec["content_fingerprint"]


# -- 3. meta passed through classify/compose/probes is unchanged (no name key) -------

def test_probes_receives_meta_without_name_key(monkeypatch):
    captured = {}
    orig = probes_mod.detect

    def spy(matches, compositions, meta, name, content, **kw):
        captured["meta"] = dict(meta)
        captured["name"] = name
        return orig(matches, compositions, meta, name, content, **kw)

    monkeypatch.setattr(assemble.probes, "detect", spy)
    assemble.build("https://github.com/o/foo-mcp", FOO_MCP, "sha", None, settings=_S)

    assert "name" not in captured["meta"]
    assert set(captured["meta"]) == {"description", "topics", "homepage"}
    assert captured["name"] == "foo-mcp"


def test_measured_sdk_names_are_in_the_vocabulary():
    from assessor.assessment.probes import _LLM_SDK_DEPS
    for name in ("openai-go", "anthropic-sdk-go", "genai", "@google/genai"):
        assert name in _LLM_SDK_DEPS, name
