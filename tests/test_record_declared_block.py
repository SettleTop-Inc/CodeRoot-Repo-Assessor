"""The `declared` block on the served assessment (Task 6, spec §4).

`assemble.build` computes `declared_block(content)` (record.py, Task 1) and serves
it at `assessment["declared"]` — but must NOT let it leak into `build_payload`/
`compute_fingerprint`: the fingerprint-parity guard is the existing fingerprint
tests (test_assessment_fingerprint.py, test_assessment_recall_drift.py), which must
stay green untouched by this change.
"""
import json

from assessor.assessment import assemble
from assessor.assessment.record import RECORD_BASENAME

from conftest import _S

# Copied from tests/test_record.py's GOOD (Task 1's contract) rather than imported,
# so this test file does not depend on test_record.py's module layout.
GOOD = {
    "record_version": 1,
    "created_by": "settletop-niles",
    "created_at": "2026-08-09T00:00:00Z",
    "source_repo": {"host": "github.com", "owner": "SettleTop-Inc", "name": "example"},
    "maintained_by": "SettleTop-Inc",
    "technologies": {"language": "python", "framework": "mcp",
                     "runtime": "python>=3.11", "dependencies": ["mcp", "httpx"]},
    "model_access": {"mode": "byo", "provider": None, "model": None},
    "confirmation": {"mode": "elicitation",
                     "confirmed": ["created_by", "maintained_by", "model_access.mode"],
                     "complete": True},
}

# A content fixture that actually classifies (mirrors golden_mcp's "strong-dep" corpus
# entry), so the test exercises a real `assemble.build` asset path, not `not_an_asset`.
MCP_FIXTURE_CONTENT = {
    "package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1.0"}}),
    "src/index.ts": 'srv.registerTool("search", {});',
}


def test_declared_block_served_when_record_present():
    content = {**MCP_FIXTURE_CONTENT, RECORD_BASENAME: json.dumps(GOOD)}
    out = assemble.build("https://github.com/o/n", content, "sha", None, settings=_S)
    assert out["assessment"]["declared"]["technologies"]["language"]["value"] == "python"
    assert (out["assessment"]["declared"]["technologies"]["language"]["source"]
            == "declared: asset-record.json")


def test_declared_none_when_absent():
    out = assemble.build("https://github.com/o/n", MCP_FIXTURE_CONTENT, "sha", None, settings=_S)
    assert out["assessment"]["declared"] is None


def test_declared_block_served_for_subdir_subject():
    """A subdir subject's OWN asset-record.json — re-rooted by
    `subject.filter_content_to_subdir` before `record.declared_block` ever reads
    `content` (assemble.py's re-rooting-path comment) — still populates
    `assessment["declared"]`, not just the whole-repo case above."""
    subdir = "mypkg"
    content = {
        f"{subdir}/package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1.0"}}),
        f"{subdir}/src/index.ts": 'srv.registerTool("search", {});',
        f"{subdir}/{RECORD_BASENAME}": json.dumps(GOOD),
    }
    out = assemble.build("https://github.com/o/n", content, "sha", None, settings=_S, subdir=subdir)
    assert out["assessment"]["declared"] is not None
    assert out["assessment"]["declared"]["technologies"]["language"]["value"] == "python"
