import pytest

from assessor.config import Settings
from assessor.errors import NotDerivable
from assessor.handlers import assess_handler
from assessor.ports.cache import NullCache

_S = Settings(assessor_api_token="x")
_SUBJECT = {"repo_url": "https://github.com/o/n", "subject_key": "rid-1",
            "commit_sha": "abc123", "subdir": ""}


class _Source:
    def __init__(self, files, metrics=None):
        self.files = files

    def acquire(self, repo_url, *, prior): raise AssertionError("not called")

    def snapshot(self, subject):
        return {"commit_sha": "abc123",
                "metadata": {"description": None, "homepage": None,
                             "topics": [], "license_spdx": None},
                "tree_paths": tuple(self.files), "tree_capped": False,
                "marker_hits": (), "files": self.files,
                "source_coverage_capped": False, "allowlist_version": 7}

    def metrics(self, subject): return None

    def prior_assessment(self, subject): return None


_MCP = {"server.py": (
    "from mcp.server.fastmcp import FastMCP\n"
    "mcp = FastMCP('demo')\n"
    "@mcp.tool()\n"
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n")}


def test_returns_the_seven_top_level_record_keys():
    r = assess_handler(_Source(_MCP), NullCache(), _S, _SUBJECT)
    assert set(r) == {"is_asset", "asset_type", "asset_types",
                      "classification_confidence", "content_fingerprint",
                      "llm_used", "assessment"}


def test_assessment_payload_has_exactly_the_fifteen_keys():
    r = assess_handler(_Source(_MCP), NullCache(), _S, _SUBJECT)
    assert set(r["assessment"]) == {
        "classification", "purpose", "compositions", "composition", "license",
        "coordinates", "versions", "risk", "known_unknowns", "topics",
        "coverage_probes", "promoted_types", "subdir", "asset_id", "source_url"}


def test_interop_is_absent():
    r = assess_handler(_Source(_MCP), NullCache(), _S, _SUBJECT)
    assert "interop" not in r["assessment"]


def test_asset_id_comes_from_subject_key_not_repo_url():
    """Two callers naming the same repo differently must not silently produce
    different asset_ids through the old `repo_id or repo_url` fallback."""
    a = assess_handler(_Source(_MCP), NullCache(), _S, _SUBJECT)
    b = assess_handler(_Source(_MCP), NullCache(), _S,
                       {**_SUBJECT, "repo_url": "https://github.com/other/name"})
    assert a["assessment"]["asset_id"] == b["assessment"]["asset_id"]


def test_empty_files_snapshot_derives_not_an_asset():
    """Task 11 fix round 1: CodeRoot's own pipeline reaches assemble.build
    with empty content whenever acquisition genuinely succeeded and found
    nothing selectable, and derives a normal not_an_asset record rather than
    raising — an empty-but-present `files` dict is a legitimate input, not a
    NotDerivable condition. The parity harness (tests/test_parity.py) caught
    this diverging on octocat/Hello-World."""
    r = assess_handler(_Source({}), NullCache(), _S, _SUBJECT)
    assert r["is_asset"] is False
    assert r["asset_type"] == "not_an_asset"
    assert r["asset_types"] == []


def test_no_snapshot_raises_not_derivable():
    """The actual NotDerivable trigger: the source could not produce a
    snapshot at all (falsy, e.g. None) — distinct from a snapshot that was
    read and legitimately came back empty, per the test above."""
    class _NoSnapshot:
        def acquire(self, repo_url, *, prior): raise AssertionError("not called")
        def snapshot(self, subject): return None
        def metrics(self, subject): return None
        def prior_assessment(self, subject): return None

    with pytest.raises(NotDerivable):
        assess_handler(_NoSnapshot(), NullCache(), _S, _SUBJECT)


def test_mcp_server_is_classified():
    r = assess_handler(_Source(_MCP), NullCache(), _S, _SUBJECT)
    assert r["is_asset"] and "mcp_server" in r["asset_types"]


def test_fingerprint_is_stable_across_two_identical_derives():
    a = assess_handler(_Source(_MCP), NullCache(), _S, _SUBJECT)
    b = assess_handler(_Source(_MCP), NullCache(), _S, _SUBJECT)
    assert a["content_fingerprint"] == b["content_fingerprint"]
