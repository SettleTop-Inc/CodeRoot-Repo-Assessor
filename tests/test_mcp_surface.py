"""The MCP surface must expose exactly the three handler-backed tools and must
never drift from the HTTP surface: same handler, same record, two doors in."""
from __future__ import annotations

import json

import pytest

from assessor.config import Settings
from assessor.mcp_server import build_mcp
from assessor.ports.cache import NullCache

_MCP = {"server.py": (
    "from mcp.server.fastmcp import FastMCP\n"
    "mcp = FastMCP('demo')\n"
    "@mcp.tool()\n"
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n")}


class _Source:
    def acquire(self, repo_url, *, prior):
        return {"status": "acquired", "snapshot": None, "commit_sha": "abc123",
                "metadata": {}, "allowlist_version": 7}

    def snapshot(self, subject):
        return {"commit_sha": "abc123",
                "metadata": {"description": None, "homepage": None,
                             "topics": [], "license_spdx": None},
                "tree_paths": (), "tree_capped": False, "marker_hits": (),
                "files": _MCP, "source_coverage_capped": False,
                "allowlist_version": 7}

    def metrics(self, subject): return None
    def prior_assessment(self, subject): return None


def _mcp():
    return build_mcp(Settings(assessor_api_token="x"), _Source(), NullCache())


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
    subject = {"repo_url": "https://github.com/o/n", "subject_key": "rid-1",
               "commit_sha": "abc123", "subdir": ""}
    direct = assess_handler(_Source(), NullCache(),
                            Settings(assessor_api_token="x"), subject)
    result = await _mcp().call_tool("assess_repository",
                                    {"repo_url": "https://github.com/o/n",
                                     "subject_key": "rid-1", "subdir": ""})
    payload = json.loads(result.content[0].text)
    assert payload["asset_types"] == direct["asset_types"]
    assert payload["content_fingerprint"] == direct["content_fingerprint"]
