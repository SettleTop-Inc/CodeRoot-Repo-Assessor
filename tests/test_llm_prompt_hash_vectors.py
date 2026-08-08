"""Drift guard for the dual-homed `prompt_hash` (the cross-service cache key).

`prompt_hash(system, user, schema_name)` is deliberately copied into BOTH
CodeRoot (`coderoot_oss/llm/cache.py`) and the Repo Assessor
(`assessor/llm/cache_helpers.py`): the Assessor computes it, ships it opaquely
over MCP, and CodeRoot stores/looks it up in `coderoot.llm_cache`. Change the
separator, the field order, or the encoding in either copy and every lookup
misses PERMANENTLY AND SILENTLY -- no error, just a slow path and (per spec
§9.6) non-deterministic promotions, because a hash mismatch looks exactly like
"never cached before" rather than like a bug.

Both repos assert the SAME fixture and THIS FILE IS BYTE-IDENTICAL IN BOTH
REPOS -- the only per-repo difference is which package the import below
resolves to. See `test_assessment_subject_vectors.py` for the established
pattern this follows.

The vectors were derived by RUNNING the real function, not from a design
sketch; see the fixture's own `_comment` for which cases were additionally
cross-checked by hand (independent of Python/hashlib) against the documented
`f"{schema_name}\\x1f{system}\\x1f{user}"` layout.
"""
import hashlib
import json
import pathlib

try:                                        # CodeRoot-OpenSource
    from coderoot_oss.llm.cache import prompt_hash
except ModuleNotFoundError:                 # CodeRoot-Repo-Assessor
    from assessor.llm.cache_helpers import prompt_hash

_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "llm_prompt_hash_vectors.json").read_text(
        encoding="utf-8"))
_CASES = _FIXTURE["cases"]


def test_prompt_hash_vectors_match_this_repo_s_copy():
    # Guard the guard: an empty/renamed fixture must fail rather than vacuously pass.
    assert len(_CASES) >= 8

    for case in _CASES:
        assert prompt_hash(case["system"], case["user"], case["schema_name"]) == case["sha256"], case["name"]


def test_prompt_hash_vectors_cover_every_drift_shape():
    """The fixture must actually exercise each thing that could silently drift,
    not just happen to contain enough cases."""
    names = {c["name"] for c in _CASES}
    assert {
        "separator_pair_a", "separator_pair_b",  # the \x1f separator
        "order_asymmetry",                        # field order != argument order
        "non_ascii",                               # UTF-8 encoding
        "empty_all",                               # empty strings
    } <= names


def test_separator_pair_vectors_are_not_a_coincidental_collision():
    """Without `\\x1f`, `"ab"+"c"` and `"a"+"bc"` concatenate identically, so a
    copy that drops the separator would make these two DIFFERENT (system, user)
    pairs hash the same. This is the direct, from-first-principles proof that
    the fixture's own recorded digests differ -- not just an assertion that
    happens to be true of the current committed values."""
    a = next(c for c in _CASES if c["name"] == "separator_pair_a")
    b = next(c for c in _CASES if c["name"] == "separator_pair_b")
    assert a["schema_name"] == b["schema_name"]
    assert a["system"] + a["user"] == b["system"] + b["user"]  # same naive concatenation
    assert a["sha256"] != b["sha256"]                          # must diverge with the separator

    # Reference computation, independent of the imported `prompt_hash`: proves
    # the fixture's own numbers are internally consistent with the documented
    # separator-joined layout, not merely with whatever this repo's function emits.
    def _reference_hash(system: str, user: str, schema_name: str) -> str:
        return hashlib.sha256(f"{schema_name}\x1f{system}\x1f{user}".encode("utf-8")).hexdigest()

    assert _reference_hash(a["system"], a["user"], a["schema_name"]) == a["sha256"]
    assert _reference_hash(b["system"], b["user"], b["schema_name"]) == b["sha256"]
