"""Spec §7 channel 2: an adversarial record generates ZERO marker hits."""
import json

from assessor.assessment import markers
from assessor.assessment.record import RECORD_BASENAME

ADVERSARIAL_BODY = json.dumps({
    "record_version": 1,
    "created_by": "x",
    "technologies": {"framework": "mcp server ANTHROPIC_BASE_URL OPENAI_API_BASE",
                     "dependencies": ["langgraph", "crewai", "claude-opus-5"]},
})


def test_scan_text_on_record_body_would_hit():
    # Control: the body IS marker-bearing when scanned — proving the exclusion
    # below is load-bearing, not vacuous.
    hits = markers.scan_text("some/other/file.py", ADVERSARIAL_BODY)
    assert hits, "adversarial body must trip markers when NOT excluded"


def test_record_path_is_never_scanned():
    # The exclusion is basename-keyed at the git_fetch scan sites. This test
    # pins the helper those sites share (add `_skip_marker_scan(path)` or
    # equivalent — adjust the import to the implementation).
    from assessor.assessment.git_fetch import _skip_marker_scan
    assert _skip_marker_scan(RECORD_BASENAME) is True
    assert _skip_marker_scan(f"pkg/{RECORD_BASENAME}") is True
    assert _skip_marker_scan("src/main.py") is False
    # Near-miss basenames must NOT be swept in by a loose (substring/prefix) match —
    # the exclusion is exact-basename only (Task 3 review).
    assert _skip_marker_scan("asset-record.json.bak") is False
    assert _skip_marker_scan("notasset-record.json") is False


def test_scan_present_blobs_skips_the_record():
    # End-to-end at the shared scan helper: an adversarial record body mixed in
    # with an ordinary file produces hits only for the ordinary file.
    from assessor.assessment.git_fetch import _scan_present_blobs
    bodies = {
        RECORD_BASENAME: ADVERSARIAL_BODY,
        "src/main.py": "os.environ['ANTHROPIC_API_KEY']\n",
    }
    hits = _scan_present_blobs(bodies)
    assert hits, "the ordinary file must still be scanned"
    assert all(h["path"] != RECORD_BASENAME for h in hits)
