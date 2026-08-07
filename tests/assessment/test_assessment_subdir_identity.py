"""Regression: maintainer-declared identity (repo topics + description) must NOT
influence the CLASSIFICATION of a subdir assessment.

Declared identity is a claim about the whole repo. A subdir assessment is scoped to a
subtree, so letting the repo's topics corroborate a marker found inside the subtree
attributes the repo's declaration to the subdir — exactly what the path-scoping honesty
rule forbids. Concretely: every subdir of `modelcontextprotocol/servers` that merely
mentions "mcp" in prose was promoted weak→strong (0.6→0.95) by the repo's `mcp` topic,
so a `docs/` subtree read as a confidently-classified MCP server.

Sibling of test_assessment_probes_subdir.py — same leak class (whole-repo signal
reaching a subtree), different channel: probes vs the classifier.
"""
from assessor.assessment import assemble


from assessor.config import Settings

_S = Settings(assessor_api_token="x")

_URL = "https://github.com/getsentry/sentry-mcp"
_META = {"topics": ["mcp"], "description": "An MCP server for Sentry"}
# A weak, prose-only mcp mention that lives INSIDE the subtree. The marker is genuinely
# path-local (so the subtree does classify) — what's under test is its CONFIDENCE.
_CONTENT = {"docs/README.md": "This documents the mcp server usage."}


def _build(subdir, meta):
    return assemble.build(_URL, _CONTENT, "abc123", None,
                          paths=tuple(_CONTENT), subdir=subdir, bucket_b=meta, settings=_S)


def test_subdir_confidence_is_not_promoted_by_repo_declared_identity():
    with_meta = _build("docs", _META)
    without_meta = _build("docs", {})
    # the subtree's own marker is weak either way — the repo's topics must not upgrade it
    assert with_meta["classification_confidence"] == without_meta["classification_confidence"]
    assert with_meta["asset_types"] == without_meta["asset_types"] == ["mcp_server"]
    tiers = [m["marker_tier"] for m in with_meta["assessment"]["classification"]["matches"]]
    assert tiers == ["weak"]


def test_subdir_evidence_cites_no_repo_level_declaration():
    rec = _build("docs", _META)
    paths = [e.get("path") for m in rec["assessment"]["classification"]["matches"]
             for e in m["evidence"]]
    assert "topics" not in paths          # 'declared MCP identity' must not appear as evidence


def test_whole_repo_identity_promotion_still_works():
    """The fix is subdir-scoped: whole-repo assessments still corroborate via topics
    (proves this didn't disable declared identity outright — PR #96's feature)."""
    promoted = assemble.build(_URL, {"README.md": "This documents the mcp server usage."},
                              "abc123", None, paths=("README.md",), bucket_b=_META, settings=_S)
    assert promoted["classification_confidence"] == 0.95
    tiers = [m["marker_tier"] for m in promoted["assessment"]["classification"]["matches"]]
    assert "strong" in tiers


def test_subdir_still_serves_repo_topics_as_repo_level_fact():
    """Suppressing identity from CLASSIFICATION must not stop us SERVING topics — they
    remain a true repo-level fact, carrying repo provenance so a consumer can see the
    scope they belong to."""
    rec = _build("docs", _META)
    assert rec["assessment"]["topics"]["value"] == ["mcp"]
    assert rec["assessment"]["topics"]["source"] == "repo object (github topics)"
