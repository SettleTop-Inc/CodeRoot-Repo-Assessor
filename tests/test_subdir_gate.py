"""`subdir` is validated before it reaches `assemble.build`, and the two MCP
failure discriminators keep their distinct status codes.

Two defects, both created by moving `assemble.build` behind a public HTTP
boundary:

  * `normalize_subdir` — whose own docstring calls it "the canonical
    validation gate for a user-supplied subdir" — had ZERO production call
    sites in this service. On `main` that was safe by construction: the only
    route into `assemble.build` was CodeRoot's `api/routers/repos.py`, which
    normalized first. Behind HTTP the raw value reached
    `subject.scoped_source_url`, which interpolates it into an f-string, so a
    crafted subdir produced a `source_url` naming a DIFFERENT repository —
    which CodeRoot then persists into `assessment["source_url"]` and serves
    preferentially (`api/routers/assessment.py:50`).

  * `not_acquired` from CodeRoot-MCP fell through to a 500, so CodeRoot read
    it as `AssessorUnavailable` ("retry") instead of `NotDerivable`
    ("re-arm acquire and skip"). Spec §8 row 1 names this exact condition.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from assessor.app import build_app
from assessor.config import Settings
from assessor.errors import InvalidSubdir, NotDerivable
from assessor.handlers import assess_handler
from assessor.mcp_client import McpToolError
from assessor.mcp_server import build_mcp
from assessor.ports.cache import NullCache

_TRAVERSALS = ["../../other/repo", "..", "pkg/../../etc", "C:/windows",
               "..\\..\\other", "/../x"]


class _RecordingSource:
    """Records the subject dict it is handed, so a test can assert on the
    subdir that actually crossed into the snapshot/derive path rather than on
    the one the request carried."""

    def __init__(self, *, error=None):
        self.subjects = []
        self.error = error

    def acquire(self, repo_url, *, prior):
        raise AssertionError("assess must not acquire")

    def snapshot(self, subject):
        self.subjects.append(dict(subject))
        if self.error is not None:
            raise self.error
        return {"commit_sha": "abc123",
                "metadata": {"description": None, "homepage": None,
                             "topics": [], "license_spdx": None},
                "tree_paths": (), "tree_capped": False, "marker_hits": (),
                "files": {}, "source_coverage_capped": False, "allowlist_version": 7}

    def metrics(self, subject):
        return None

    def prior_assessment(self, subject):
        return None


_S = Settings(assessor_api_token="tok")


def _subject(subdir):
    return {"repo_url": "https://github.com/o/n", "subject_key": "rid-1",
            "commit_sha": "", "subdir": subdir}


def _client(source, settings=_S):
    return TestClient(build_app(settings, source, NullCache(), acquire_source=source))


def _body(subdir):
    return {"subject": _subject(subdir), "source": "direct"}


# --- the gate itself, at the handler both surfaces share -------------------

@pytest.mark.parametrize("subdir", _TRAVERSALS)
def test_a_traversal_subdir_never_reaches_the_source(subdir):
    """The sharp assertion is not "it raised" but "nothing downstream ran".
    A gate placed after `source.snapshot(subject)` would still raise and still
    look fixed, while having already handed the raw value to the source (which
    on the MCP path forwards it to `get_subject`)."""
    source = _RecordingSource()
    with pytest.raises(InvalidSubdir):
        assess_handler(source, NullCache(), _S, _subject(subdir))
    assert source.subjects == []


@pytest.mark.parametrize("raw,normalized", [
    ("", ""), ("pkg/a", "pkg/a"), ("/pkg/a/", "pkg/a"),
    ("pkg//a", "pkg/a"), ("a/b/c", "a/b/c"), (".", ""), ("  /x/  ", "x"),
])
def test_the_normalized_subdir_is_what_the_source_and_derive_see(raw, normalized):
    """Normalizing, not merely rejecting — and the normalized value must
    replace the raw one for the WHOLE handler, because `McpSource.snapshot`
    and `prior_assessment` read `subject["subdir"]` too and must agree with
    the subdir the record is ultimately stamped with."""
    source = _RecordingSource()
    record = assess_handler(source, NullCache(), _S, _subject(raw))
    assert source.subjects == [dict(_subject(raw), subdir=normalized)]
    assert record["assessment"]["subdir"] == normalized


def test_already_normalized_subdirs_pass_through_untouched():
    """Idempotence, stated as the property CodeRoot depends on: CodeRoot
    normalizes before dispatching, so every request it sends must map to
    itself and no existing traffic can shift."""
    for value in ("", "pkg/a", "a/b/c", "packages/agentkit"):
        source = _RecordingSource()
        assess_handler(source, NullCache(), _S, _subject(value))
        assert source.subjects[0]["subdir"] == value


def test_a_traversal_subdir_cannot_move_the_stamped_source_url():
    """The consequence, not just the mechanism: `scoped_source_url` builds
    `https://<host>/<owner>/<name>/tree/<sha>/<subdir>` by raw f-string
    interpolation, so an ungated `../../other/repo` stamps a source_url for a
    repository the assessment is not about. Compare the two records to show
    the traversal cannot produce a url the plain repo path does not already
    dominate."""
    source = _RecordingSource()
    with pytest.raises(InvalidSubdir):
        assess_handler(source, NullCache(), _S, _subject("../../other/repo"))
    clean = assess_handler(source, NullCache(), _S, _subject(""))
    assert clean["assessment"]["source_url"] == "https://github.com/o/n"


# --- the status code the gate maps to -------------------------------------

@pytest.mark.parametrize("subdir", _TRAVERSALS)
def test_http_rejects_a_traversal_subdir_with_400_invalid_subdir(subdir):
    """DECISION: 400, not 422.

    422 is `not_derivable` on this surface, and CodeRoot's `assessor_client`
    maps 422 to "re-arm acquire and skip" — which for a structurally invalid
    subdir would re-acquire and re-fail forever, since no acquisition can make
    `../../other` a valid subtree. A 4xx that is neither 410 nor 422 falls
    through that client's `raise_for_status()` and surfaces loudly as the data
    fault it is. 400 also matches how this surface already answers every other
    malformed-input case (`unsupported_field`, `invalid_repo_url`)."""
    r = _client(_RecordingSource()).post("/v1/assess", json=_body(subdir),
                                         headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_subdir"


def test_a_bad_subdir_is_not_reported_as_a_bad_repo_url():
    """`normalize_subdir` raises a bare `ValueError`, and both surfaces
    already map bare ValueErrors to `invalid_repo_url` (that is what
    `ports.source._split` raises). Letting the subdir fault inherit that
    discriminator would point the operator at the wrong field, so
    `InvalidSubdir` is deliberately not a ValueError subclass."""
    r = _client(_RecordingSource()).post("/v1/assess", json=_body("../x"),
                                         headers={"Authorization": "Bearer tok"})
    assert r.json()["error"] != "invalid_repo_url"


def test_the_mcp_surface_gates_subdir_identically():
    """`assess_repository(subdir=...)` is a second door into the same handler.
    A gate installed on app.py alone would leave the stdio surface — which has
    no `source` parameter for a caller to even inspect — wide open."""
    import asyncio

    source = _RecordingSource()
    server = build_mcp(_S, source, NullCache(), acquire_source=source)
    result = asyncio.run(server.call_tool(
        "assess_repository", {"repo_url": "https://github.com/o/n",
                              "subject_key": "rid-1", "subdir": "../../other"}))
    payload = json.loads(result.content[0].text)
    assert payload["error"] == "invalid_subdir"
    assert source.subjects == []


# --- fix 5: not_acquired is 422, upstream_error stays 5xx -----------------

def _tool_error(kind):
    """Builds the exception the REAL `McpToolClient._call` raises for each of
    CodeRoot-MCP's two discriminated payloads (mcp_client.py:146-147) — the
    real class with the real payload shape, not a stand-in, so a test cannot
    pass against a mapping that only recognises something this file invented."""
    payload = ({"error": "not_acquired"} if kind == "not_acquired"
               else {"error": "upstream_error", "status_code": 503, "detail": "down"})
    return McpToolError("get_subject", payload)


def test_not_acquired_becomes_422_not_derivable_over_http():
    """Spec §8 row 1. CodeRoot's `assessor_client` turns this into
    `NotDerivable`, and `service.py` re-arms acquire and skips — the only
    path by which a never-acquired repo can ever become derivable."""
    source = _RecordingSource(error=NotDerivable("CodeRoot holds no acquisition"))
    r = _client(source).post("/v1/assess", json=_body(""),
                             headers={"Authorization": "Bearer tok"})
    assert r.status_code == 422
    assert r.json()["error"] == "not_derivable"


def test_upstream_error_stays_a_5xx_and_does_not_become_422():
    """The discrimination that keeps the fix honest: `upstream_error` means
    CodeRoot itself is down, i.e. RETRY. Mapping it to 422 would make every
    CodeRoot blip re-arm acquire across the whole corpus. Asserted through a
    real `McpToolError` escaping the source, with `raise_server_exceptions`
    off so the ASGI layer produces the status a real client would see."""
    source = _RecordingSource(error=_tool_error("upstream_error"))
    app = build_app(_S, source, NullCache(), acquire_source=source)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/v1/assess", json=_body(""), headers={"Authorization": "Bearer tok"})
    assert r.status_code >= 500


class _RaisingMcpClient:
    """A CodeRoot-MCP transport that answers every tool with one discriminated
    error, raised the way the REAL `McpToolClient._call` raises it."""

    def __init__(self, kind):
        self.kind = kind

    def _raise(self, tool):
        raise _tool_error(self.kind)

    def get_subject(self, key, subdir=""):
        self._raise("get_subject")

    def read_files(self, key, sha, paths):
        self._raise("read_files")

    def get_metrics(self, key):
        self._raise("get_metrics")

    def get_prior_assessment(self, key, subdir=""):
        self._raise("get_prior_assessment")


def test_the_whole_not_acquired_chain_produces_422_end_to_end():
    """The two tests above split the chain in half: `McpToolError ->
    NotDerivable` lives in test_mcp_client.py, `NotDerivable -> 422` lives
    here. This drives the WHOLE chain through a real `McpSource` and the real
    app, so a regression at either link fails one test rather than needing
    both to be read together. This is the request CodeRoot actually makes for
    a repo whose acquisition row is missing."""
    from assessor.ports.mcp_source import McpSource

    source = McpSource(_RaisingMcpClient("not_acquired"))
    app = build_app(_S, source, NullCache(), acquire_source=source)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/v1/assess", json=_body(""), headers={"Authorization": "Bearer tok"})
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "not_derivable"


def test_the_whole_upstream_error_chain_stays_5xx_end_to_end():
    """Same chain, other discriminator. `upstream_error` must never reach 422,
    or every CodeRoot blip re-arms acquire across the corpus."""
    from assessor.ports.mcp_source import McpSource

    source = McpSource(_RaisingMcpClient("upstream_error"))
    app = build_app(_S, source, NullCache(), acquire_source=source)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/v1/assess", json=_body(""), headers={"Authorization": "Bearer tok"})
    assert r.status_code >= 500


def test_the_two_mcp_discriminators_do_not_collapse():
    """Stated as a relation so a mapping that sent BOTH to 422 (or both to
    500) fails here even if each single-condition test above were weakened."""
    not_acq = _client(_RecordingSource(
        error=NotDerivable("no acquisition"))).post(
            "/v1/assess", json=_body(""), headers={"Authorization": "Bearer tok"})
    upstream_source = _RecordingSource(error=_tool_error("upstream_error"))
    upstream = TestClient(
        build_app(_S, upstream_source, NullCache(), acquire_source=upstream_source),
        raise_server_exceptions=False).post(
            "/v1/assess", json=_body(""), headers={"Authorization": "Bearer tok"})
    assert not_acq.status_code == 422
    assert upstream.status_code >= 500
    assert not_acq.status_code != upstream.status_code
