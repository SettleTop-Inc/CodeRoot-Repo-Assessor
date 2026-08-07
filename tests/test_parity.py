# tests/test_parity.py
"""Behaviour preservation is the acceptance gate for the extraction: the same
corpus must produce byte-identical fingerprints and identical asset_types.

Two test functions live here, and they prove different things:

* `test_record_matches_coderoot` is the real parity check. It is skipped
  unless ASSESSOR_CORPUS_DIR points at an export from
  scripts/export_corpus.py, so the suite stays runnable without a CodeRoot
  database. When it skips, that means parity against CodeRoot was NOT
  verified this run — not merely that an environment variable was unset. A
  skipped run must never be read as a passing parity result.

* `test_harness_replays_a_self_generated_fixture` runs unconditionally
  against tests/fixtures/parity-sample/, which holds records this service
  generated about itself (see that directory's README). It proves the
  harness plumbing works — the JSON loads, `_Fixed` satisfies the `Source`
  protocol assess_handler needs, assess_handler runs end to end, and the
  assertions execute correctly — and it proves NOTHING about parity with the
  original CodeRoot system, because both sides of the comparison are this
  service. Do not mistake a green run of it for a parity result.
"""
import json
import os
from pathlib import Path

import pytest

from assessor.config import Settings
from assessor.handlers import assess_handler
from assessor.ports.cache import NullCache

_DIR = os.environ.get("ASSESSOR_CORPUS_DIR")
_FIXTURES = Path(__file__).parent / "fixtures" / "parity-sample"


class _Fixed:
    """A `Source` fed entirely from a pre-recorded snapshot — no acquisition,
    no network. Satisfies `assessor.ports.source.Source` structurally
    (`Source` is `@runtime_checkable`)."""

    def __init__(self, snapshot, metrics):
        self._snap, self._metrics = snapshot, metrics

    def acquire(self, repo_url, *, prior): raise AssertionError("not called")

    def snapshot(self, subject):
        s = dict(self._snap)
        s["tree_paths"] = tuple(s["tree_paths"])
        s["marker_hits"] = tuple(s["marker_hits"])
        return s

    def metrics(self, subject): return self._metrics
    def prior_assessment(self, subject): return None


def _run_case(case: Path) -> None:
    data = json.loads(case.read_text(encoding="utf-8"))
    record = assess_handler(
        _Fixed(data["snapshot"], data["metrics"]), NullCache(),
        Settings(assessor_api_token="x"), data["subject"])
    assert record["content_fingerprint"] == data["expected"]["content_fingerprint"]
    assert sorted(record["asset_types"]) == sorted(data["expected"]["asset_types"])


# --- Real parity: this service's output vs. CodeRoot's recorded output. ----

def _corpus_cases():
    return sorted(Path(_DIR).glob("*.json")) if _DIR else []


_SKIP_REASON = (
    "parity against CodeRoot was NOT verified this run: ASSESSOR_CORPUS_DIR "
    "is unset, so there is no exported corpus (see scripts/export_corpus.py) "
    "to compare against. This is not a pass — it is the absence of a check.")

# Scoped to this one test, deliberately NOT a module-level `pytestmark`: a
# module-level skipif marks every test collected in the file, which would
# silently skip the unconditional self-check below too and reintroduce the
# exact "green but unverified" failure mode this file exists to avoid.
_skip_without_corpus = pytest.mark.skipif(not _DIR, reason=_SKIP_REASON)


@_skip_without_corpus
@pytest.mark.corpus
@pytest.mark.parametrize("case", _corpus_cases(), ids=lambda p: p.stem)
def test_record_matches_coderoot(case):
    _run_case(case)


# --- Harness self-check: proves the plumbing, not parity. ------------------
# Unconditional — no ASSESSOR_CORPUS_DIR required, no skip marker attached —
# so this always runs, including in CI and a fresh clone.

def _fixture_cases():
    return sorted(_FIXTURES.glob("*.json"))


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda p: p.stem)
def test_harness_replays_a_self_generated_fixture(case):
    """Exercises the harness end to end against records this service
    generated about itself. A green run here proves the JSON shape loads,
    `_Fixed` satisfies `Source`, `assess_handler` runs, and the assertions
    fire — nothing more. It is NOT evidence of parity with CodeRoot: both
    sides of every comparison in this test came from this service."""
    _run_case(case)


def test_fixture_directory_is_not_empty():
    """Guards against the self-check silently asserting nothing because the
    fixtures directory was emptied or renamed."""
    assert _fixture_cases(), "tests/fixtures/parity-sample has no *.json cases"
