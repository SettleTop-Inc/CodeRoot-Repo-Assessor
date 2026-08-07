# tests/test_ports_source.py
import pytest

from assessor.config import Settings
from assessor.errors import RepoGone
from assessor.ports.source import DirectSource, Source

_S = Settings(assessor_api_token="x")


class _Http:
    """Stands in for CodeRoot's HttpClient. resolve_head is the only call
    DirectSource makes through it."""
    def __init__(self, sha="abc123", repo_obj=None, gone=False):
        self.sha, self.repo_obj, self.gone = sha, repo_obj or {}, gone


class _Fetcher:
    def __init__(self):
        self.calls = 0

    def fetch(self, clone_url, repo_id, sha):
        self.calls += 1
        return ({"README.md": "hi"}, ("README.md",), False, [])


def _direct(http, fetcher, monkeypatch):
    from assessor.assessment import content as content_mod

    def fake_resolve(h, owner, name):
        if h.gone:
            raise content_mod.RepoGone(f"{owner}/{name}")
        return h.sha, h.repo_obj

    monkeypatch.setattr(content_mod, "resolve_head", fake_resolve)
    return DirectSource(_S, http, fetcher)


def test_direct_source_satisfies_the_protocol(monkeypatch):
    assert isinstance(_direct(_Http(), _Fetcher(), monkeypatch), Source)


def test_acquire_fetches_when_there_is_no_prior(monkeypatch):
    f = _Fetcher()
    r = _direct(_Http(), f, monkeypatch).acquire("https://github.com/o/n", prior=None)
    assert r["status"] == "acquired"
    assert r["snapshot"]["files"] == {"README.md": "hi"}
    assert f.calls == 1


def test_matching_prior_performs_no_clone(monkeypatch):
    """The SHA-reuse short-circuit. Losing it would never surface as a bug —
    re-cloning returns correct results, just at full cost — so it is asserted
    on the fetcher call count, not on the response body."""
    f = _Fetcher()
    src = _direct(_Http(sha="abc123"), f, monkeypatch)
    r = src.acquire("https://github.com/o/n",
                    prior={"commit_sha": "abc123", "allowlist_version": 7})
    assert r["status"] == "unchanged"
    assert f.calls == 0


def test_prior_with_a_stale_allowlist_version_still_fetches(monkeypatch):
    f = _Fetcher()
    src = _direct(_Http(sha="abc123"), f, monkeypatch)
    r = src.acquire("https://github.com/o/n",
                    prior={"commit_sha": "abc123", "allowlist_version": 6})
    assert r["status"] == "acquired"
    assert f.calls == 1


def test_unchanged_still_returns_refreshed_metadata(monkeypatch):
    """resolve_head returns the repo object alongside the sha, and CodeRoot
    refreshes Bucket B on every run including the reuse path."""
    http = _Http(sha="abc123", repo_obj={"description": "fresh", "topics": ["mcp"]})
    src = _direct(http, _Fetcher(), monkeypatch)
    r = src.acquire("https://github.com/o/n",
                    prior={"commit_sha": "abc123", "allowlist_version": 7})
    assert r["status"] == "unchanged"
    assert r["metadata"]["description"] == "fresh"


def test_repo_gone_propagates(monkeypatch):
    src = _direct(_Http(gone=True), _Fetcher(), monkeypatch)
    with pytest.raises(RepoGone):
        src.acquire("https://github.com/o/n", prior=None)


def test_invalid_repo_url_is_rejected_before_any_clone(monkeypatch):
    f = _Fetcher()
    src = _direct(_Http(), f, monkeypatch)
    with pytest.raises(ValueError):
        src.acquire("https://github.com/../../etc/passwd", prior=None)
    assert f.calls == 0


def test_direct_source_has_no_metrics_and_no_prior_assessment(monkeypatch):
    """A standalone deployment has no Aveloxis and no assessment history. Both
    return None so the record degrades through known_unknowns rather than
    inventing a license or a release list."""
    src = _direct(_Http(), _Fetcher(), monkeypatch)
    subject = {"repo_url": "https://github.com/o/n", "subject_key": "o/n",
               "commit_sha": "abc123", "subdir": ""}
    assert src.metrics(subject) is None
    assert src.prior_assessment(subject) is None
