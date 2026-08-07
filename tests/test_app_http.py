import asyncio

from fastapi.testclient import TestClient

from assessor.app import build_app
from assessor.config import Settings
from assessor.errors import NotDerivable, RepoGone
from assessor.ports.cache import NullCache
from assessor.ports.source import DirectSource

_MCP = {"server.py": (
    "from mcp.server.fastmcp import FastMCP\n"
    "mcp = FastMCP('demo')\n"
    "@mcp.tool()\n"
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n")}


class _Source:
    def __init__(self, *, gone=False, empty=False, no_snapshot=False):
        self.gone, self.empty, self.no_snapshot = gone, empty, no_snapshot

    def acquire(self, repo_url, *, prior):
        if self.gone:
            raise RepoGone("o/n")
        return {"status": "acquired", "snapshot": None, "commit_sha": "abc123",
                "metadata": {}, "allowlist_version": 7}

    def snapshot(self, subject):
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


def _client(settings=None, source=None):
    s = settings or Settings(assessor_api_token="tok")
    return TestClient(build_app(s, source or _Source(), NullCache()))


_BODY = {"subject": {"repo_url": "https://github.com/o/n", "subject_key": "rid-1",
                     "commit_sha": "abc123", "subdir": ""},
         "source": "direct"}


def test_missing_bearer_is_rejected_when_a_token_is_configured():
    assert _client().post("/v1/assess", json=_BODY).status_code == 401


def test_wrong_bearer_is_rejected():
    r = _client().post("/v1/assess", json=_BODY, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_correct_bearer_is_accepted():
    r = _client().post("/v1/assess", json=_BODY, headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert "mcp_server" in r.json()["asset_types"]


def test_anonymous_mode_needs_no_bearer():
    c = _client(Settings(assessor_allow_anonymous=True))
    assert c.post("/v1/assess", json=_BODY).status_code == 200


def test_empty_snapshot_derives_not_an_asset_not_422():
    """Task 11 fix round 1: an empty-but-present `files` dict is a legitimate,
    derivable input (CodeRoot's own pipeline reaches this state whenever
    acquisition succeeds and finds nothing selectable) — it must not be
    treated the same as a snapshot that could not be read at all."""
    c = _client(source=_Source(empty=True))
    r = c.post("/v1/assess", json=_BODY, headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.json()["asset_types"] == []


def test_no_snapshot_is_422_not_derivable_not_a_500():
    c = _client(source=_Source(no_snapshot=True))
    r = c.post("/v1/assess", json=_BODY, headers={"Authorization": "Bearer tok"})
    assert r.status_code == 422 and r.json()["error"] == "not_derivable"


def test_repo_gone_is_410():
    c = _client(source=_Source(gone=True))
    r = c.post("/v1/acquire", json={"repo_url": "https://github.com/o/n", "prior": None},
               headers={"Authorization": "Bearer tok"})
    assert r.status_code == 410 and r.json()["error"] == "repo_gone"


def test_version_endpoint_reports_all_three_versions():
    r = _client().get("/v1/version", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert set(r.json()) == {"registry_version", "allowlist_version",
                             "marker_vocab_version"}
    assert r.json()["registry_version"] == 9
    assert r.json()["allowlist_version"] == 7


def test_healthz_needs_no_auth():
    assert _client().get("/healthz").status_code == 200


def test_readyz_reports_llm_off_without_failing():
    r = _client().get("/readyz")
    assert r.status_code == 200 and r.json()["llm"] == "off"


def test_assess_with_malformed_repo_url_via_real_direct_source_is_400_not_500():
    # Regression for fix round 1: the `_Source` double above never validates
    # repo_url, so it cannot exercise this path. DirectSource._split() raises
    # a plain ValueError for a malformed url, BEFORE any network/git call —
    # http and fetcher are never touched, so None stand-ins are safe here.
    s = Settings(assessor_api_token="tok")
    real_source = DirectSource(s, None, None)
    c = TestClient(build_app(s, real_source, NullCache()))
    body = {"subject": {"repo_url": "https://github.com/../../etc/passwd",
                        "subject_key": "rid-1", "commit_sha": "", "subdir": ""},
            "source": "direct"}
    r = c.post("/v1/assess", json=body, headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_repo_url"


def test_acquire_rejects_a_ref_as_unsupported():
    r = _client().post("/v1/acquire",
                       json={"repo_url": "https://github.com/o/n", "ref": "v1.2.3",
                             "prior": None},
                       headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "unsupported_field" and body["field"] == "ref"


def test_assess_rejects_a_non_direct_source_as_unsupported():
    r = _client().post("/v1/assess", json={**_BODY, "source": "mcp"},
                       headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "unsupported_field" and body["field"] == "source"


def test_openapi_and_docs_are_not_served():
    c = _client()
    assert c.get("/openapi.json").status_code == 404
    assert c.get("/docs").status_code == 404
    assert c.get("/redoc").status_code == 404


def test_non_ascii_bearer_header_is_401_not_500():
    # Regression for fix round 2: secrets.compare_digest raises TypeError on a
    # non-ASCII *str* (fixed by comparing .encode()d bytes instead). httpx's
    # client-side header encoding rejects a non-ASCII header value before it
    # ever reaches the app, so TestClient(...).get(..., headers=...) CANNOT
    # exercise this path -- it would raise UnicodeEncodeError in the test
    # itself, never touching assessor.app's auth(). Drive the ASGI app
    # directly instead, with the header injected as raw latin-1-encoded bytes
    # into the scope, matching how a real non-ASCII header arrives over the
    # wire (ASGI headers are always raw bytes; latin-1 is what an ASGI server
    # decodes/carries them as per the spec).
    app = build_app(Settings(assessor_api_token="tok"), _Source(), NullCache())
    headers = [(b"authorization", "Bearer ünicode".encode("latin-1"))]
    scope = {"type": "http", "method": "GET", "path": "/v1/version",
             "raw_path": b"/v1/version", "query_string": b"", "headers": headers,
             "client": ("test", 123), "server": ("test", 80), "scheme": "http",
             "http_version": "1.1"}
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    assert status == 401
