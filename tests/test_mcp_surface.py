"""The MCP surface must expose exactly the three handler-backed tools and must
never drift from the HTTP surface: same handler, same record, two doors in —
on the happy path AND on the error path. A typed exception (NotDerivable,
RepoGone, ValueError) must map to the same discriminated body on both
surfaces, or a programmatic caller sees different failure shapes depending on
which door it walked through."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from assessor.app import build_app
from assessor.config import Settings
from assessor.errors import RepoGone
from assessor.mcp_server import build_mcp
from assessor.ports.cache import NullCache
from assessor.ports.source import DirectSource

_MCP = {"server.py": (
    "from mcp.server.fastmcp import FastMCP\n"
    "mcp = FastMCP('demo')\n"
    "@mcp.tool()\n"
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n")}


class _Source:
    # gone/empty/no_snapshot mirror test_app_http.py's double exactly, so the
    # same fixture shapes the same failure on both surfaces.
    def __init__(self, *, gone=False, empty=False, no_snapshot=False):
        self.gone, self.empty, self.no_snapshot = gone, empty, no_snapshot

    def acquire(self, repo_url, *, prior):
        if self.gone:
            raise RepoGone("o/n")
        return {"status": "acquired", "snapshot": None, "commit_sha": "abc123",
                "metadata": {}, "allowlist_version": 7}

    def snapshot(self, subject):
        if self.gone:
            raise RepoGone("o/n")
        if self.no_snapshot:
            return None
        return {"commit_sha": "abc123",
                "metadata": {"description": None, "homepage": None,
                             "topics": [], "license_spdx": None},
                "tree_paths": (), "tree_capped": False, "marker_hits": (),
                "files": {} if self.empty else _MCP,
                "source_coverage_capped": False, "allowlist_version": 7}

    def metrics(self, subject): return None
    def prior_assessment(self, subject): return None


def _mcp(source=None, acquire_source=None):
    """Single double for both sources by default — same reasoning as
    test_app_http.py's `_client`; the split is covered in its own file."""
    src = source or _Source()
    return build_mcp(Settings(assessor_api_token="x"), src, NullCache(),
                     acquire_source=acquire_source or src)


async def _call(tool, args, source=None):
    result = await _mcp(source).call_tool(tool, args)
    return json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_three_tools_are_registered():
    names = {t.name for t in await _mcp().list_tools()}
    assert names == {"assess_repository", "acquire_repository", "assessor_version"}


@pytest.mark.anyio
async def test_every_tool_has_a_description():
    for tool in await _mcp().list_tools():
        assert tool.description and len(tool.description) > 20


@pytest.mark.anyio
async def test_assess_tool_returns_the_same_record_as_the_http_surface():
    from assessor.handlers import assess_handler
    # commit_sha deliberately differs from the "abc123" _Source.snapshot()
    # above always returns for commit_sha, so this fixture can't be misread
    # as evidence that a caller-supplied commit_sha is honored (it is not —
    # see test_app_http.py's commit_sha guard tests).
    subject = {"repo_url": "https://github.com/o/n", "subject_key": "rid-1",
               "commit_sha": "caller-requested-and-ignored", "subdir": ""}
    direct = assess_handler(_Source(), NullCache(),
                            Settings(assessor_api_token="x"), subject)
    result = await _mcp().call_tool("assess_repository",
                                    {"repo_url": "https://github.com/o/n",
                                     "subject_key": "rid-1", "subdir": ""})
    payload = json.loads(result.content[0].text)
    assert payload["asset_types"] == direct["asset_types"]
    assert payload["content_fingerprint"] == direct["content_fingerprint"]


# --- error-path parity -------------------------------------------------
# The SDK's tool runner catches any exception escaping a tool body and
# rewrites it into an opaque `ToolError(f"Error executing tool {name}: {e}")`
# (mcp/server/mcpserver/tools/base.py), destroying the typed discriminator.
# Each tool must therefore catch NotDerivable/RepoGone/ValueError itself and
# return the same body shape app.py returns, one test per type per tool
# where that tool's handler can actually raise it.

@pytest.mark.anyio
async def test_assess_repository_maps_not_derivable_to_the_http_body_shape():
    payload = await _call("assess_repository",
                          {"repo_url": "https://github.com/o/n", "subject_key": "rid-1"},
                          source=_Source(no_snapshot=True))
    assert payload == {"error": "not_derivable", "reason": "no snapshot available"}


@pytest.mark.anyio
async def test_assess_repository_derives_not_an_asset_for_empty_files_not_error():
    """Task 11 fix round 1, mirrored on the MCP door: an empty-but-present
    `files` dict must derive a normal record on this surface too, not just
    the HTTP one — the whole point of this file is that the two doors cannot
    drift."""
    payload = await _call("assess_repository",
                          {"repo_url": "https://github.com/o/n", "subject_key": "rid-1"},
                          source=_Source(empty=True))
    assert "error" not in payload
    assert payload["asset_types"] == []


@pytest.mark.anyio
async def test_assess_repository_maps_repo_gone_to_the_http_body_shape():
    payload = await _call("assess_repository",
                          {"repo_url": "https://github.com/o/n", "subject_key": "rid-1"},
                          source=_Source(gone=True))
    assert payload == {"error": "repo_gone"}


@pytest.mark.anyio
async def test_acquire_repository_maps_repo_gone_to_the_http_body_shape():
    payload = await _call("acquire_repository",
                          {"repo_url": "https://github.com/o/n"},
                          source=_Source(gone=True))
    assert payload == {"error": "repo_gone"}


# ValueError needs a real DirectSource: _Source above never validates
# repo_url, so it cannot exercise DirectSource._split()'s traversal check
# (same reasoning as test_app_http.py's malformed-url regression test).
# Both assess_repository and acquire_repository reach _split() before any
# network/git call, so a bare `DirectSource(settings, None, None)` is safe.

@pytest.mark.anyio
async def test_assess_repository_maps_invalid_repo_url_to_the_http_body_shape():
    s = Settings(assessor_api_token="tok")
    real = DirectSource(s, None, None)
    payload = await _call("assess_repository",
                          {"repo_url": "https://github.com/../../etc/passwd",
                           "subject_key": "rid-1"}, source=real)
    assert payload["error"] == "invalid_repo_url"
    assert "reason" in payload


@pytest.mark.anyio
async def test_acquire_repository_maps_invalid_repo_url_to_the_http_body_shape():
    s = Settings(assessor_api_token="tok")
    real = DirectSource(s, None, None)
    payload = await _call("acquire_repository",
                          {"repo_url": "https://github.com/../../etc/passwd"}, source=real)
    assert payload["error"] == "invalid_repo_url"
    assert "reason" in payload


# --- the test that pins the property: the two surfaces cannot drift ----

@pytest.mark.anyio
async def test_invalid_repo_url_body_is_byte_identical_between_mcp_and_http():
    s = Settings(assessor_api_token="tok")
    bad_url = "https://github.com/../../etc/passwd"

    http_source = DirectSource(s, None, None)
    http_client = TestClient(build_app(s, http_source, NullCache(),
                                       acquire_source=http_source))
    http_body = {"subject": {"repo_url": bad_url, "subject_key": "rid-1",
                             "commit_sha": "", "subdir": ""}, "source": "direct"}
    http_response = http_client.post("/v1/assess", json=http_body,
                                     headers={"Authorization": "Bearer tok"})
    assert http_response.status_code == 400

    mcp_source = DirectSource(s, None, None)
    mcp_payload = await _call("assess_repository",
                              {"repo_url": bad_url, "subject_key": "rid-1"},
                              source=mcp_source)

    assert mcp_payload == http_response.json()
