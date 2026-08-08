"""Drift guard for the dual-homed `normalize_subdir` (extraction spec 6.4).

`assessment/subject.py` is deliberately copied into BOTH CodeRoot and the Repo
Assessor: a network hop for a 20-line pure validator would be absurd, and one
copy cannot serve both CodeRoot's request path (`api/routers/assessment.py`,
`api/routers/repos.py`, `api/routers/subjects.py`) and the Assessor's derive
path. The cost of that choice is silent drift, so both repos assert the SAME
fixture and THIS FILE IS BYTE-IDENTICAL IN BOTH REPOS -- the only per-repo
difference is which package the import below resolves to.

The reject vectors are the point. `normalize_subdir` is the canonical validation
gate for an attacker-supplied subdir, so a copy that quietly stops rejecting a
'..' segment is a path-traversal hole in whichever repo drifted, and a green
suite in the other repo would not notice.

The vectors were derived by RUNNING every candidate through the implementation
rather than from a design sketch; see the fixture's own `_comment` for the three
inputs whose real behaviour contradicted the sketch.
"""
import json
import pathlib

import pytest

try:                                        # CodeRoot-OpenSource
    from coderoot_oss.assessment.subject import normalize_subdir
except ModuleNotFoundError:                 # CodeRoot-Repo-Assessor
    from assessor.assessment.subject import normalize_subdir

_VECTORS = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "subject_vectors.json").read_text(encoding="utf-8"))


def test_subject_vectors_match_this_repo_s_copy():
    # Guard the guard: an empty/renamed fixture must fail rather than vacuously pass.
    assert len(_VECTORS["accept"]) >= 15 and len(_VECTORS["reject"]) >= 14

    for case in _VECTORS["accept"]:
        assert normalize_subdir(case["in"]) == case["out"], case

    for bad in _VECTORS["reject"]:
        with pytest.raises(ValueError):
            normalize_subdir(bad)
