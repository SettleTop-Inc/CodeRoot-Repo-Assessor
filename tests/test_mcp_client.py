"""Drives McpSource/McpCache through a REAL `McpToolClient`, over a REAL
Streamable-HTTP connection (a genuine TCP socket, real JSON-RPC framing, a
real uvicorn ASGI server), against a REAL `MCPServer` -- never a
hand-written double standing in for `McpToolClient` itself.

Plan 1's only Critical was exactly that gap: a production adapter that did
not implement the interface its caller used, caught by nothing because every
test stopped short of the real wiring (`create_app()` built a bare
`httpx.Client` where the moved code called `http.get_json(...)`; every real
request 500'd, every test passed). This file's whole job is to make that
impossible here -- every test below constructs an actual `McpToolClient(url)`
pointed at an actual server process-thread listening on an actual port, the
same transport (`MCPServer.streamable_http_app()`) a deployed CodeRoot-MCP
would run behind `settings.coderoot_mcp_url`.

Two tiers:

  1. `test_*_over_a_real_mcp_connection` / `test_an_error_payload_raises_*` /
     `test_prior_assessment_*` / `test_cache_*` (`_minimal_server`, always
     run): a small MCPServer built directly in this file, reproducing the six
     CodeRoot-MCP tool contracts from their VERIFIED shapes (mcp_source.py,
     mcp_cache.py, and the sibling repo's coderoot_mcp/server.py, all read
     before writing this file). This proves McpToolClient's own method
     name / argument shape / unwrap / raise contract against a real MCP
     server and a real network round trip, independent of whether the
     sibling repo happens to be checked out on this machine.

  2. `test_snapshot_matches_the_real_coderoot_mcp_tool_contract` (skipped
     unless `D:/Development/SettleTop/CodeRoot-MCP` is present next to this
     repo): imports CodeRoot-MCP's own unmodified `build_server`, serves the
     REAL production tool implementations, and only doubles the boundary
     CodeRoot-MCP's OWN test suite (tests/test_server.py) already doubles --
     the CodeRoot HTTP backend underneath it. This is the strongest evidence
     available in this environment: not just "a server with the right
     shape," but the actual server this deployment would talk to. It is
     skipped, not weakened, when that repo is absent (e.g. a CI checkout of
     only this repo) -- see the module-level skip condition below for
     exactly what triggers that.

What tier 1 does NOT establish on its own: that CodeRoot-MCP's real tools
actually match the shapes reproduced here. That is only proven when tier 2
runs. On this development machine, as of this task, tier 2 DOES run (the
sibling repo is present) -- see the report for its captured output.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer

from assessor.errors import NotDerivable
from assessor.mcp_client import McpToolClient, McpToolError
from assessor.ports.mcp_cache import McpCache
from assessor.ports.mcp_source import McpSource

_SUBJECT = {"repo_url": "https://github.com/o/n", "subject_key": "rid-1",
            "commit_sha": "", "subdir": ""}


# --- serving a real MCPServer over a real HTTP port -------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _serve(mcp_server: MCPServer):
    """Serve a real MCPServer over a real HTTP port with uvicorn in a
    background thread, and yield the base URL `McpToolClient` should be
    pointed at. `asyncio.run(server.serve())` (not `server.run()`) is
    deliberate: `.run()` installs OS signal handlers, which only the main
    thread may do -- `.serve()` alone does not, so this is safe to run in a
    background thread."""
    port = _free_port()
    app = mcp_server.streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn server did not start within 5s"
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# --- tier 1: a locally-built server reproducing the verified tool shapes ----

def _minimal_server(*, subject_error=None, prior_found=True, prior_error=None,
                    cache_hit=False, cache_error=None):
    """Reproduces CodeRoot-MCP's six tool contracts from the shapes verified
    in mcp_source.py/mcp_cache.py's own docstrings and (where importable) the
    sibling repo's coderoot_mcp/server.py -- not a copy of that file."""
    mcp = MCPServer(name="test-coderoot-mcp")

    @mcp.tool()
    def get_subject(repo_id: str, subdir: str = "") -> dict:
        if subject_error:
            return subject_error
        return {"commit_sha": "abc123", "description": "d", "homepage": None,
                "topics": ["mcp"], "license_spdx": "MIT", "license": "MIT",
                "releases": [{"tag": "v1"}],
                "tree_paths": ["a.py", "big.bin"], "content_paths": ["a.py"],
                "tree_capped": False, "marker_hits": [], "source_coverage_capped": False,
                "allowlist_version": 7}

    @mcp.tool()
    def read_files(repo_id: str, commit_sha: str, paths: list[str]) -> dict:
        return {"files": {p: "x" for p in paths}, "missing": []}

    @mcp.tool()
    def get_metrics(repo_id: str) -> dict:
        return {"license": "Apache-2.0", "releases": [{"tag": "v1"}]}

    @mcp.tool()
    def get_prior_assessment(repo_id: str, subdir: str = "") -> dict:
        if prior_error:
            return prior_error
        if prior_found:
            return {"found": True, "assessment": {"content_fingerprint": "fp",
                                                   "asset_types": ["mcp_server"]}}
        return {"found": False, "assessment": None}

    @mcp.tool()
    def llm_cache_get(model: str, prompt_sha256: str) -> dict:
        if cache_error:
            return cache_error
        if cache_hit:
            return {"hit": True, "response": {"asset_types": ["agent"]}}
        return {"hit": False, "response": None}

    @mcp.tool()
    def llm_cache_put(model: str, prompt_sha256: str, response: dict) -> dict:
        return {"stored": True}

    return mcp


def test_snapshot_has_the_expected_shape_over_a_real_mcp_connection():
    with _serve(_minimal_server()) as url:
        snap = McpSource(McpToolClient(url)).snapshot(_SUBJECT)
        assert snap["commit_sha"] == "abc123"
        assert snap["files"] == {"a.py": "x"}
        assert snap["metadata"]["license_spdx"] == "MIT"
        assert snap["tree_paths"] == ("a.py", "big.bin")
        assert set(snap) == {"commit_sha", "metadata", "tree_paths", "tree_capped",
                             "marker_hits", "files", "source_coverage_capped",
                             "allowlist_version"}


def test_metrics_come_back_over_a_real_mcp_connection():
    with _serve(_minimal_server()) as url:
        metrics = McpSource(McpToolClient(url)).metrics(_SUBJECT)
        assert metrics == {"license": "Apache-2.0", "releases": [{"tag": "v1"}]}


def test_an_error_payload_raises_instead_of_a_corrupt_snapshot():
    """The scenario the brief calls out by name: get_subject returning
    {"error": ...} must not let McpSource crash on a bare KeyError while
    trying to read `s["commit_sha"]` off it -- it must raise something
    legible instead."""
    err = {"error": "upstream_error", "status_code": 503, "detail": "down"}
    with _serve(_minimal_server(subject_error=err)) as url:
        with pytest.raises(McpToolError) as exc_info:
            McpSource(McpToolClient(url)).snapshot(_SUBJECT)
        assert exc_info.value.payload == err


def test_prior_assessment_unwraps_a_hit_to_the_bare_assessment_dict():
    with _serve(_minimal_server(prior_found=True)) as url:
        prior = McpSource(McpToolClient(url)).prior_assessment(_SUBJECT)
        assert prior == {"content_fingerprint": "fp", "asset_types": ["mcp_server"]}


def test_prior_assessment_unwraps_a_miss_to_none_not_the_found_wrapper():
    with _serve(_minimal_server(prior_found=False)) as url:
        prior = McpSource(McpToolClient(url)).prior_assessment(_SUBJECT)
        assert prior is None


def test_llm_cache_get_is_not_unwrapped_but_mcpcache_still_reads_it():
    """llm_cache_get must come back as the raw {"hit", "response"} shape
    (McpCache does its own unwrapping) -- checked directly against the
    client, then again through McpCache to prove the two actually compose."""
    with _serve(_minimal_server(cache_hit=True)) as url:
        client = McpToolClient(url)
        assert client.llm_cache_get("m", "h") == {
            "hit": True, "response": {"asset_types": ["agent"]}}
        assert McpCache(client).get("m", "h") == {"asset_types": ["agent"]}


def test_llm_cache_put_round_trips_over_a_real_mcp_connection():
    with _serve(_minimal_server()) as url:
        client = McpToolClient(url)
        McpCache(client).put("m", "h", {"a": 1})   # must not raise


def test_a_cache_error_payload_degrades_to_a_miss_not_a_crash():
    """McpCache wraps every call in try/except specifically so a raise from
    the client degrades to "no cache" (mcp_cache.py:28-40) -- this proves
    that swallowing actually happens with a REAL raising client, not a
    hand-written double that merely asserts the same intent."""
    err = {"error": "upstream_error", "status_code": 503, "detail": "down"}
    with _serve(_minimal_server(cache_error=err)) as url:
        client = McpToolClient(url)
        assert McpCache(client).get("m", "h") is None   # must not raise
        McpCache(client).put("m", "h", {"a": 1})          # must not raise


# --- tier 2: the real CodeRoot-MCP server, skipped if unavailable ----------

_SIBLING_REPO = Path("D:/Development/SettleTop/CodeRoot-MCP")
_SIBLING_SERVER = _SIBLING_REPO / "coderoot_mcp" / "server.py"


def _import_real_build_server():
    if not _SIBLING_SERVER.exists():
        return None
    if str(_SIBLING_REPO) not in sys.path:
        sys.path.insert(0, str(_SIBLING_REPO))
    try:
        from coderoot_mcp.server import build_server
    except ImportError:
        return None
    return build_server


_build_server = _import_real_build_server()


class _CodeRootHttpDouble:
    """Mirrors CodeRoot-MCP's own tests/test_server.py::_Client -- the
    boundary their OWN test suite already doubles (the CodeRoot HTTP
    backend). Everything above that boundary here is real, unmodified
    CodeRoot-MCP code: the real build_server(), the real MCP tool
    definitions, the real JSON-RPC dispatch this task's McpToolClient has to
    interoperate with."""

    def get_subject(self, repo_id, subdir):
        return {"commit_sha": "abc123", "description": "d", "homepage": None,
                "topics": ["mcp"], "license_spdx": "MIT",
                "tree_paths": ["a.py", "big.bin"], "content_paths": ["a.py"],
                "tree_capped": False, "marker_hits": [], "source_coverage_capped": False,
                "allowlist_version": 7, "license": "MIT", "releases": [{"tag": "v1"}]}

    def get_files(self, repo_id, commit_sha, paths):
        return {"files": {p: "x" for p in paths}, "missing": []}

    def get_prior_assessment(self, repo_id, subdir):
        return {"content_fingerprint": "fp", "asset_types": ["mcp_server"]}

    def cache_get(self, model, h):
        return None

    def cache_put(self, model, h, response):
        pass


@pytest.mark.skipif(_build_server is None,
                    reason="CodeRoot-MCP sibling repo not checked out next to this one "
                           "(expected at D:/Development/SettleTop/CodeRoot-MCP)")
def test_snapshot_matches_the_real_coderoot_mcp_tool_contract():
    server = _build_server(_CodeRootHttpDouble())
    with _serve(server) as url:
        snap = McpSource(McpToolClient(url)).snapshot(_SUBJECT)
        assert snap["commit_sha"] == "abc123"
        assert snap["files"] == {"a.py": "x"}
        assert snap["metadata"]["license_spdx"] == "MIT"

        prior = McpSource(McpToolClient(url)).prior_assessment(_SUBJECT)
        assert prior == {"content_fingerprint": "fp", "asset_types": ["mcp_server"]}

        metrics = McpSource(McpToolClient(url)).metrics(_SUBJECT)
        assert metrics == {"license": "MIT", "releases": [{"tag": "v1"}]}


@pytest.mark.skipif(_build_server is None,
                    reason="CodeRoot-MCP sibling repo not checked out next to this one "
                           "(expected at D:/Development/SettleTop/CodeRoot-MCP)")
def _failing_backend(status: int):
    """A CodeRoot HTTP backend double whose `get_subject` raises the given
    status, so the REAL CodeRoot-MCP server picks its own discriminator for
    it (`_http_error_payload`: 404 -> not_acquired, anything else ->
    upstream_error). The status is the only thing these tests choose; the
    payload comes from CodeRoot-MCP's unmodified code."""
    import httpx

    class _Failing(_CodeRootHttpDouble):
        def get_subject(self, repo_id, subdir):
            request = httpx.Request("GET", "http://api.test/x")
            response = httpx.Response(status, request=request)
            raise httpx.HTTPStatusError(f"{status} error", request=request,
                                        response=response)

    return _Failing()


@pytest.mark.skipif(_build_server is None,
                    reason="CodeRoot-MCP sibling repo not checked out next to this one "
                           "(expected at D:/Development/SettleTop/CodeRoot-MCP)")
def test_real_coderoot_mcp_not_acquired_becomes_not_derivable():
    """Spec §8 row 1: "CodeRoot has no acquisition row" is a 422
    `not_derivable`, which makes CodeRoot re-arm acquire and skip the assess.

    Before this mapping the chain ran: CodeRoot 404 -> CodeRoot-MCP
    `{"error": "not_acquired"}` -> `McpToolError` -> uncaught by both
    `McpSource.snapshot` and app.py's mapping -> 500 -> `AssessorUnavailable`
    -> the assess unit retried until it exhausted its attempts, and acquire
    was NEVER re-armed, so the repo could never become derivable. The whole
    condition is unreadable-snapshot, which is precisely `NotDerivable`.

    Driven through the REAL `build_server` and a REAL socket, doubling only
    the CodeRoot HTTP backend -- so the discriminator under test is the one
    CodeRoot-MCP actually emits, not one this file invented."""
    with _serve(_build_server(_failing_backend(404))) as url:
        with pytest.raises(NotDerivable):
            McpSource(McpToolClient(url)).snapshot(_SUBJECT)


@pytest.mark.skipif(_build_server is None,
                    reason="CodeRoot-MCP sibling repo not checked out next to this one "
                           "(expected at D:/Development/SettleTop/CodeRoot-MCP)")
def test_real_coderoot_mcp_upstream_error_does_not_become_not_derivable():
    """The other half, and the one that keeps the fix honest. `not_acquired`
    means RE-ACQUIRE; `upstream_error` means RETRY -- CodeRoot itself is down.
    Mapping both to NotDerivable would make every CodeRoot blip re-arm acquire
    across the whole corpus, so this asserts the raise is still an
    `McpToolError` carrying the upstream discriminator, i.e. still a 5xx.

    `NotDerivable` is checked explicitly rather than relying on
    `pytest.raises(McpToolError)` alone: the two classes are unrelated, so a
    regression that mapped everything would fail here loudly rather than
    quietly satisfying a broader `Exception` match."""
    with _serve(_build_server(_failing_backend(503))) as url:
        with pytest.raises(McpToolError) as exc_info:
            McpSource(McpToolClient(url)).snapshot(_SUBJECT)
        assert not isinstance(exc_info.value, NotDerivable)
        assert exc_info.value.payload["error"] == "upstream_error"


@pytest.mark.skipif(_build_server is None,
                    reason="CodeRoot-MCP sibling repo not checked out next to this one "
                           "(expected at D:/Development/SettleTop/CodeRoot-MCP)")
def test_real_coderoot_mcp_metrics_discriminates_the_same_two_conditions():
    """`McpSource.metrics` reads the SAME `/repos/{id}/subject` endpoint
    through CodeRoot-MCP's `get_metrics` (coderoot_mcp/server.py:78), so its
    `not_acquired` means "no acquisition row" too, and must map identically.
    `assess_handler` calls `snapshot` first, so this only fires when the row
    disappears between the two calls -- but that window produced a 500 too."""
    with _serve(_build_server(_failing_backend(404))) as url:
        with pytest.raises(NotDerivable):
            McpSource(McpToolClient(url)).metrics(_SUBJECT)
    with _serve(_build_server(_failing_backend(503))) as url:
        with pytest.raises(McpToolError) as exc_info:
            McpSource(McpToolClient(url)).metrics(_SUBJECT)
        assert not isinstance(exc_info.value, NotDerivable)
