from fastapi.testclient import TestClient

from assessor.app import build_app
from assessor.config import Settings
from assessor.errors import NotDerivable, RepoGone
from assessor.ports.cache import NullCache

_MCP = {"server.py": (
    "from mcp.server.fastmcp import FastMCP\n"
    "mcp = FastMCP('demo')\n"
    "@mcp.tool()\n"
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n")}


class _Source:
    def __init__(self, *, gone=False, empty=False):
        self.gone, self.empty = gone, empty

    def acquire(self, repo_url, *, prior):
        if self.gone:
            raise RepoGone("o/n")
        return {"status": "acquired", "snapshot": None, "commit_sha": "abc123",
                "metadata": {}, "allowlist_version": 7}

    def snapshot(self, subject):
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


def test_empty_snapshot_is_422_not_derivable_not_a_500():
    c = _client(source=_Source(empty=True))
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
