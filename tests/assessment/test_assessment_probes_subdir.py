"""Regression: coverage_probes (declared-identity discrepancy, #101) must NOT run for
a SUBDIR assessment (subdir hybrid, #102). The two features merged concurrently; a
whole-branch validation caught that probes read the WHOLE-REPO declared identity
(repo name + topics + description) while a subdir assessment is scoped to a subtree,
so a `docs/` subdir of an mcp-named repo would falsely emit "declared mcp_server but
no tool registrations found" and fire a wasted advisory LLM call — contradicting the
path-scoping honesty rule. A subdir assessment carries no probes; whole-repo still does.
"""
from assessor.assessment import assemble


from assessor.config import Settings

_S = Settings(assessor_api_token="x")

_CONTENT = {"README.md": "x", "docs/guide.md": "how to use it"}   # no mcp/tool markers anywhere
_META = {"topics": ["mcp"], "description": "An MCP server for Sentry"}
_URL = "https://github.com/getsentry/sentry-mcp"                   # name declares mcp


def test_subdir_assessment_carries_no_coverage_probes():
    rec = assemble.build(_URL, _CONTENT, "sha", None, paths=tuple(_CONTENT),
                         subdir="docs", bucket_b=_META, settings=_S)
    # the docs subtree has no mcp marker AND doesn't itself declare mcp — the repo does.
    # probes must not attribute the whole-repo declaration to the subtree.
    assert rec["assessment"]["coverage_probes"] == []


def test_whole_repo_assessment_still_probes():
    # same repo, whole-repo scope: name+topics declare mcp_server but no tool markers →
    # a coverage probe SHOULD fire (proves the fix didn't disable whole-repo probing).
    rec = assemble.build(_URL, _CONTENT, "sha", None, paths=tuple(_CONTENT), bucket_b=_META, settings=_S)
    probes = rec["assessment"]["coverage_probes"]
    assert isinstance(probes, list) and any(p.get("type") == "mcp_server" for p in probes)
