"""Regression cover for the acceptance gate's GitHub-request recorder.

`scripts/verify_mcp_parity.py` proves the MCP path re-derives the corpus with
ZERO GitHub requests. That claim rests entirely on `watch_outbound_hosts`, and
a counter that can only ever report 0 is indistinguishable from a broken one --
so these tests exist to keep the counter falsifiable.

Two real defects motivated them, both found on the reframed gate's first run:

  1. The recorder patched only `httpx`. This environment has TWO HTTP
     libraries -- `httpx` (what `assessor/http_client.py` uses for GitHub) and
     `httpx2` (MCP SDK 2.0's fork; `create_mcp_http_client` is typed
     `-> httpx2.AsyncClient`). Patching one meant no MCP traffic was observed
     at all, so the recorder looked dead while still reporting a clean
     "0 GitHub requests", AND a GitHub call issued via `httpx2` would not have
     been counted.
  2. Nothing proved the counter could report non-zero.

No network: every request goes through a `MockTransport`, which still routes
through `Client.send` and so is observed exactly as a real request would be.
No credential appears anywhere in this file.
"""
import sys
from pathlib import Path

import httpx
import httpx2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from verify_mcp_parity import _github_hosts, watch_outbound_hosts  # noqa: E402

_OK = lambda request: httpx.Response(200, json={})          # noqa: E731
_OK2 = lambda request: httpx2.Response(200, json={})        # noqa: E731

_PATCHED = [(httpx, "Client"), (httpx, "AsyncClient"),
            (httpx2, "Client"), (httpx2, "AsyncClient")]


def test_counts_a_github_request_made_through_httpx():
    """The library `assessor/http_client.py` actually uses for GitHub."""
    with watch_outbound_hosts() as seen:
        with httpx.Client(transport=httpx.MockTransport(_OK)) as c:
            c.get("https://api.github.com/repos/o/n")
    assert _github_hosts(seen) == [("httpx", "api.github.com")]


@pytest.mark.anyio
async def test_counts_a_github_request_made_through_httpx2():
    """The library the MCP transport uses. This is the case the original
    single-library patch would have missed entirely -- a GitHub request on the
    'zero GitHub requests' path, uncounted."""
    with watch_outbound_hosts() as seen:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(_OK2)) as c:
            await c.get("https://api.github.com/repos/o/n")
    assert _github_hosts(seen) == [("httpx2", "api.github.com")]


@pytest.mark.anyio
async def test_observes_mcp_traffic_so_liveness_is_provable():
    """The positive control. The gate asserts total observed calls > 0 to
    distinguish 'no GitHub traffic' from 'recorder never ran'; that assertion
    only works because non-GitHub httpx2 traffic is recorded too."""
    with watch_outbound_hosts() as seen:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(_OK2)) as c:
            await c.get("http://127.0.0.1:8300/mcp")
    assert seen == [("httpx2", "127.0.0.1")]
    assert _github_hosts(seen) == []


def test_every_patched_send_is_restored_on_exit():
    """A leaked patch would make later runs in the same process record into a
    dead list -- the counter reporting 0 forever, for the wrong reason."""
    before = {(m.__name__, c): getattr(m, c).send for m, c in _PATCHED}
    with watch_outbound_hosts():
        during = {(m.__name__, c): getattr(m, c).send for m, c in _PATCHED}
        assert all(during[k] is not before[k] for k in before), (
            "at least one client class was not patched inside the block")
    after = {(m.__name__, c): getattr(m, c).send for m, c in _PATCHED}
    assert after == before


def test_restores_even_when_the_body_raises():
    before = {(m.__name__, c): getattr(m, c).send for m, c in _PATCHED}
    with pytest.raises(RuntimeError):
        with watch_outbound_hosts():
            raise RuntimeError("boom")
    assert {(m.__name__, c): getattr(m, c).send for m, c in _PATCHED} == before


def test_records_nothing_after_the_block_exits():
    with watch_outbound_hosts() as seen:
        pass
    with httpx.Client(transport=httpx.MockTransport(_OK)) as c:
        c.get("https://api.github.com/repos/o/n")
    assert seen == []
