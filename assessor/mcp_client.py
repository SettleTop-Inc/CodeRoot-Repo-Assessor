"""The real MCP transport adapter connecting McpSource/McpCache
(`ports/mcp_source.py`, `ports/mcp_cache.py`) to a live CodeRoot-MCP server.

Built to the interface both ports actually call (verified by reading them,
not assumed):

    get_subject(key, subdir)                     -> mcp_source.py:24
    read_files(key, commit_sha, content_paths)    -> mcp_source.py:34
    get_metrics(subject_key)                      -> mcp_source.py:50
    get_prior_assessment(subject_key, subdir)     -> mcp_source.py:53
    llm_cache_get(model, prompt_sha256)           -> mcp_cache.py:30
    llm_cache_put(model, prompt_sha256, response) -> mcp_cache.py:39

Plan 1's only Critical was a production adapter that did not implement the
interface its caller used -- `create_app()` built a bare `httpx.Client`
where the moved code called `http.get_json(...)`. Every real request
returned 500, and every test passed, because nothing constructed the
production path. `tests/test_mcp_client.py` exists specifically so that
cannot happen again here: it drives McpSource/McpCache through a REAL
`McpToolClient`, over a REAL HTTP connection, against a REAL `MCPServer` --
never a hand-written double standing in for this class.

CodeRoot-MCP's six tools (coderoot_mcp/server.py, verified against that file
directly) return one of three shapes on every call:
  - a normal payload (subject fields, {"files": ..., "missing": ...}, etc.)
  - {"found": bool, "assessment": dict | None}       (get_prior_assessment only)
  - {"error": "not_acquired" | "upstream_error", ...} (any tool, on failure)

The two consumers need different unwrapping, which is the actual risk this
adapter exists to get right:
  - McpSource reads fields directly off get_subject's / get_metrics' /
    read_files' raw dicts (ports/mcp_source.py), and returns
    prior_assessment's value straight through as the `Source` protocol's own
    declared `dict | None` -- so get_prior_assessment's found/assessment
    wrapper must be unwrapped HERE, or a miss (`{"found": False, ...}`)
    would hand back a truthy dict where `None` was expected.
  - McpCache (ports/mcp_cache.py) was written in Task 8 to parse the raw
    `{"hit": bool, "response": ...}` shape itself, so llm_cache_get/put must
    NOT unwrap that one -- confirmed by reading mcp_cache.py before writing
    this file, per this plan's own warning that most of its briefs have
    contained a defect.

An `{"error": ...}` payload is raised as `McpToolError` rather than handed
back as a dict: McpSource has no error branch of its own (it would KeyError
reading a field off an error payload -- an outage surfacing as a confusing
crash instead of a clear failure), while McpCache's `get()`/`put()` already
wrap every call in `try/except Exception` specifically so a raise degrades
to a cache miss rather than failing the request (mcp_cache.py:28-40).

The MCP client API (checked against the installed `mcp==2.0.0` package
rather than assumed -- 2.0's client layer differs from 1.x the same way its
server layer does): a plain URL string handed to `mcp.client.Client(url)`
opens a Streamable-HTTP transport with a bare, header-less `httpx2.
AsyncClient`, which has no way to carry a bearer token. Building the
transport explicitly (`streamable_http_client(url, http_client=
create_mcp_http_client(headers=...))`) and passing THAT to `Client(...)` is
the only way to attach one.

Every public method here is synchronous, matching how McpSource/McpCache
(and the hand-written doubles in tests/test_ports_mcp_*.py) already call
this class. The underlying `Client` API is async-only, so each method opens
its own connection with `asyncio.run(...)` and tears it down afterward --
this requires no event loop already running in the calling thread, which
holds for a sync FastAPI route handler and a sync FastMCP tool function
(both dispatched to a worker thread) but would NOT hold for something
awaiting this from the event loop thread itself. Reconnecting per call costs
a handshake per tool call rather than reusing one session; a persistent
background-thread connection would remove that cost but is deferred as a
follow-up rather than built here -- this task's whole point is correctness
of the wiring, not connection reuse."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import Any

from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


class McpToolError(Exception):
    """A CodeRoot-MCP tool call returned a discriminated `{"error": ...}`
    payload (coderoot_mcp/server.py's `_upstream_error_payload`/
    `_http_error_payload`: `"not_acquired"` or `"upstream_error"`) instead of
    a normal result, or the MCP call itself failed (an exception escaped the
    remote tool body, which the SDK's tool runner rewrites into an opaque
    `is_error=True` result -- see coderoot_mcp/server.py's own docstrings on
    why every real tool traps this itself, and why callers still must not
    assume it always will). Raised rather than handed back as a dict so a
    caller that does not itself branch on `"error"` (McpSource) fails loudly
    instead of KeyError-ing on a field the error payload never had."""

    def __init__(self, tool: str, payload: dict) -> None:
        self.tool = tool
        self.payload = payload
        detail = payload.get("detail") or payload.get("error") or "mcp tool error"
        super().__init__(f"{tool}: {detail}")


class McpToolClient:
    """Synchronous facade over a live MCP (Streamable-HTTP) connection to
    CodeRoot-MCP, exposing exactly the six methods McpSource/McpCache call."""

    def __init__(self, url: str, token: str | None = None) -> None:
        self._url = url
        self._token = token

    # --- McpSource's four methods (ports/mcp_source.py) --------------------

    def get_subject(self, key: str, subdir: str = "") -> dict:
        return self._call("get_subject", {"repo_id": key, "subdir": subdir})

    def read_files(self, key: str, commit_sha: str, content_paths: Iterable[str]) -> dict:
        return self._call("read_files", {"repo_id": key, "commit_sha": commit_sha,
                                          "paths": list(content_paths)})

    def get_metrics(self, subject_key: str) -> dict:
        return self._call("get_metrics", {"repo_id": subject_key})

    def get_prior_assessment(self, subject_key: str, subdir: str = "") -> dict | None:
        wrapped = self._call("get_prior_assessment",
                             {"repo_id": subject_key, "subdir": subdir})
        # Unwrap here: McpSource.prior_assessment returns this value straight
        # through as `dict | None`. Handing back the {"found": ...} wrapper
        # itself would make a miss (`{"found": False, "assessment": None}`)
        # read as a truthy prior.
        return wrapped.get("assessment") if wrapped.get("found") else None

    # --- McpCache's two methods (ports/mcp_cache.py) ------------------------

    def llm_cache_get(self, model: str, prompt_sha256: str) -> dict:
        # Deliberately NOT unwrapped: McpCache.get() parses the raw
        # {"hit": bool, "response": ...} shape itself (mcp_cache.py:28-35).
        return self._call("llm_cache_get", {"model": model, "prompt_sha256": prompt_sha256})

    def llm_cache_put(self, model: str, prompt_sha256: str, response: dict) -> dict:
        return self._call("llm_cache_put", {"model": model, "prompt_sha256": prompt_sha256,
                                             "response": response})

    # --- transport -----------------------------------------------------------

    def _call(self, tool: str, arguments: dict) -> dict:
        payload = asyncio.run(self._call_async(tool, arguments))
        if isinstance(payload, dict) and "error" in payload:
            raise McpToolError(tool, payload)
        return payload

    async def _call_async(self, tool: str, arguments: dict) -> Any:
        # A bare URL string handed to Client() builds a header-less
        # streamable_http_client internally (mcp/client/client.py's
        # __post_init__), which has no way to carry a bearer token -- so the
        # transport is built explicitly here instead, whenever there is a
        # token to attach.
        #
        # Passing a pre-built http_client to streamable_http_client() marks
        # it caller-owned (its `client_provided` check): that client is
        # entered into the transport's own exit stack only when
        # streamable_http_client() itself created it, so when WE hand one
        # in, we must close it ourselves or its connection leaks past
        # asyncio.run() returning -- observed as Windows ProactorEventLoop
        # "unclosed transport" ResourceWarnings before this `async with` was
        # added.
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        async with create_mcp_http_client(headers=headers) as http_client:
            transport = streamable_http_client(self._url, http_client=http_client)
            async with Client(transport, mode="auto") as client:
                result = await client.call_tool(tool, arguments)
                if result.is_error:
                    # An exception escaped the remote tool body without being
                    # caught into a discriminated payload (every real
                    # CodeRoot-MCP tool catches its own upstream failures, so
                    # this is not the expected path -- see the module
                    # docstring for why this is still handled rather than
                    # assumed away). content[0].text here is an opaque
                    # human-readable string, not JSON; carry it as `detail`
                    # rather than let json.loads raise a confusing
                    # JSONDecodeError in its place.
                    detail = result.content[0].text if result.content else str(result)
                    raise McpToolError(tool, {"error": "tool_error", "detail": detail})
                # Every CodeRoot-MCP tool returns a dict on every branch
                # (coderoot_mcp/server.py), specifically so this is always
                # populated -- there is no other content shape to handle.
                return json.loads(result.content[0].text)
