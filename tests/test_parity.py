# tests/test_parity.py
"""Behaviour preservation is the acceptance gate for the extraction.

WHAT THIS HARNESS ACTUALLY MEASURES, WITH THE LLM OFF (`llm_provider="none"`,
pinned via the shared `_S` from conftest.py — not left to the field default,
which a stray .env or exported LLM_PROVIDER could silently override):
`content_fingerprint` is compared UNCONDITIONALLY, for every
case, because `assemble.build` computes it from `det_asset_types` — the
deterministic classification only — before any citation-backed promotion is
applied (see assemble.py's PAYLOAD SPLIT comment). Promotion can never move
the hash. A fingerprint mismatch is therefore always a real divergence.

`asset_types` is a different story: CodeRoot's recorded `expected.asset_types`
can include types added by a citation-backed LLM promotion
(`assessment.promoted_types`), which this llm_provider=none harness can never
reproduce. Comparing against the raw `expected.asset_types` would report a
promoted repo as a parity failure when it is really an LLM-off run being
compared against an LLM-on expectation — that was a real bug in fix round 0
of this file, caught on the first live 130-repo run (3 of 4 failures were
exactly the 3 promoted repos; content_fingerprint matched on all 130). The fix
(fix round 1): compare `asset_types` against `expected.asset_types` MINUS
`expected.promoted_types` — the deterministic portion, which is what an
LLM-off run can actually be expected to reproduce. Whenever that set is
non-empty, the assertion message says so explicitly, so a failure reads as
"N promoted type(s) excluded, here's why" rather than a silent drop.

That means this harness proves fingerprint parity in full, and asset_types
parity for the deterministic path only. It does NOT independently verify that
CodeRoot's own promotions were sound — only that the deterministic classifier
this service extracted still agrees with CodeRoot's deterministic classifier.

Two test functions carry the actual comparisons, and they prove different
things (the rest of the file is regression coverage and guards for both):

* `test_record_matches_coderoot` is the real parity check, against real
  CodeRoot-recorded data. It is skipped unless ASSESSOR_CORPUS_DIR points at
  an export from scripts/export_corpus.py, so the suite stays runnable
  without a CodeRoot database. When it skips, that means parity against
  CodeRoot was NOT verified this run — not merely that an environment
  variable was unset. A skipped run must never be read as a passing parity
  result. `test_corpus_directory_is_not_empty_when_configured` guards the
  adjacent failure mode: ASSESSOR_CORPUS_DIR set but pointing at nothing,
  which would otherwise look like the same harmless skip instead of the
  operator error it is.

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

from conftest import _S
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


def _check_record(record: dict, expected: dict, label: str) -> None:
    # content_fingerprint hashes only the deterministic classification (see
    # module docstring) — a promotion can never move it, so this comparison
    # is unconditional and is the real gate.
    assert record["content_fingerprint"] == expected["content_fingerprint"]

    # asset_types can legitimately differ from CodeRoot's recorded value by
    # exactly the LLM-promoted types: this harness runs with llm_provider=
    # none and cannot reproduce a citation-backed promotion. Compare against
    # the deterministic portion only, and say so on failure.
    promoted = set(expected.get("promoted_types") or [])
    deterministic_expected = sorted(set(expected["asset_types"]) - promoted)
    note = (f" ({len(promoted)} LLM-promoted type(s) excluded from this "
             f"llm_provider=none comparison: {sorted(promoted)})" if promoted else "")
    assert sorted(record["asset_types"]) == deterministic_expected, (
        f"deterministic asset_types mismatch for {label}{note}")


def _derive(data: dict) -> dict:
    # `_S` (tests/conftest.py) pins llm_provider="none" explicitly rather than
    # relying on the field default: Settings reads the real environment and
    # .env (assessor/config.py), so a stray .env or an exported LLM_PROVIDER
    # would otherwise silently flip a corpus run LLM-on — firing live HTTP
    # calls across the whole corpus and producing false failures on any repo
    # the local model happened to promote. Using the one shared, already-
    # audited definition instead of a second ad hoc Settings(...) here means
    # there is exactly one place this precondition can drift from true.
    return assess_handler(
        _Fixed(data["snapshot"], data["metrics"]), NullCache(), _S, data["subject"])


def _run_case(case: Path) -> None:
    data = json.loads(case.read_text(encoding="utf-8"))
    _check_record(_derive(data), data["expected"], case.stem)


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


def test_corpus_directory_is_not_empty_when_configured():
    """Mirrors test_fixture_directory_is_not_empty, for the directory that
    matters more: if ASSESSOR_CORPUS_DIR is set but misspelled or points at
    an empty directory, `_corpus_cases()` silently returns [] and pytest's
    default empty-parametrize skip reports a generic "empty parameter set"
    reason — indistinguishable, without -rs, from `_SKIP_REASON`'s honest
    "no corpus configured" skip. An operator who typos the acceptance-gate
    path would see "1 skipped" either way and have no signal that their
    corpus was never read. This test fails loudly instead, naming the
    configured path, whenever ASSESSOR_CORPUS_DIR is set."""
    if not _DIR:
        pytest.skip("ASSESSOR_CORPUS_DIR is unset — nothing to check here; "
                    "see test_record_matches_coderoot's skip reason for what "
                    "an unset corpus dir means for parity verification.")
    assert _corpus_cases(), (
        f"ASSESSOR_CORPUS_DIR={_DIR!r} is set but contains no *.json files — "
        "check the path for a typo, or re-run scripts/export_corpus.py")


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


# --- Direct regression coverage for the fix-round-1 promoted-types bug -----
# None of the self-generated fixtures above have a real promotion (there is
# no LLM available to produce one here), so the two tests below simulate one
# by editing `expected` in memory on top of a real derived record. They pin
# the exact bug the live 130-repo run found: comparing the raw
# `expected.asset_types` (fix round 0) fails on a promoted repo even though
# the deterministic classifier agrees; the fix must both tolerate a declared
# promotion and still catch a genuine mismatch.

def test_promoted_types_are_excluded_from_the_comparison():
    """If CodeRoot recorded an extra asset_type but named it in
    `promoted_types`, the comparison must pass: that type exists only
    because of an LLM citation this llm_provider=none harness cannot
    reproduce."""
    data = json.loads((_FIXTURES / "demo__mcp-server.json").read_text(encoding="utf-8"))
    record = _derive(data)
    assert record["asset_types"] == ["mcp_server"]  # sanity: matches the fixture as-is
    synthetic_expected = {**data["expected"],
                          "asset_types": sorted(data["expected"]["asset_types"] + ["agent"]),
                          "promoted_types": ["agent"]}
    _check_record(record, synthetic_expected, "synthetic-promotion")  # must not raise


def test_an_unmarked_extra_type_still_fails_the_comparison():
    """The promoted_types subtraction must not become a blanket pass: an
    asset_type CodeRoot recorded WITHOUT naming it in promoted_types (i.e. a
    genuine deterministic mismatch) must still fail."""
    data = json.loads((_FIXTURES / "demo__mcp-server.json").read_text(encoding="utf-8"))
    record = _derive(data)
    synthetic_expected = {**data["expected"],
                          "asset_types": sorted(data["expected"]["asset_types"] + ["agent"]),
                          "promoted_types": []}  # NOT declared as promoted
    with pytest.raises(AssertionError, match="deterministic asset_types mismatch"):
        _check_record(record, synthetic_expected, "synthetic-missing-promotion-tag")
