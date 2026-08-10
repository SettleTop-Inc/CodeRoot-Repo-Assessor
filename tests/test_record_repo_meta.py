# tests/test_record_repo_meta.py
"""Task 5: declared_* keys on repo_meta.

DirectSource.acquire's `acquired` branch parses asset-record.json (via
assessor.assessment.record.parse_record) out of the freshly fetched `files`
dict and merges three declared_* keys into repo_meta. The `unchanged` branch
fetches no files, so it is deliberately left without these keys — emitting
None there would look identical to "the record was removed" and CodeRoot's
executor (a later task) relies on that distinction to preserve prior DB
values rather than nulling them out.
"""
import json

from assessor.assessment import content as content_mod
from assessor.assessment import record
from assessor.config import Settings
from assessor.ports.source import DirectSource

_S = Settings(assessor_api_token="x")


class _Http:
    """Stands in for CodeRoot's HttpClient. resolve_head is the only call
    DirectSource makes through it."""
    def __init__(self, sha="abc123", repo_obj=None, gone=False):
        self.sha, self.repo_obj, self.gone = sha, repo_obj or {}, gone


class _RecordFetcher:
    """Returns files containing a valid root asset-record.json."""
    def __init__(self):
        self.calls = 0

    def fetch(self, clone_url, repo_id, sha):
        self.calls += 1
        body = json.dumps({
            "record_version": 1,
            "created_by": "settletop-niles",
            "created_at": "2026-08-09T00:00:00Z",
            "maintained_by": "SettleTop-Inc",
        })
        return ({record.RECORD_BASENAME: body}, (record.RECORD_BASENAME,), False, [])


class _NoRecordFetcher:
    """Returns files with no asset-record.json at all."""
    def __init__(self):
        self.calls = 0

    def fetch(self, clone_url, repo_id, sha):
        self.calls += 1
        return ({"README.md": "hi"}, ("README.md",), False, [])


def _direct(http, fetcher, monkeypatch):
    def fake_resolve(h, owner, name):
        if h.gone:
            raise content_mod.RepoGone(f"{owner}/{name}")
        return h.sha, h.repo_obj

    monkeypatch.setattr(content_mod, "resolve_head", fake_resolve)
    return DirectSource(_S, http, fetcher)


def test_acquired_branch_carries_declared_keys(monkeypatch):
    src = _direct(_Http(), _RecordFetcher(), monkeypatch)
    result = src.acquire("https://github.com/o/n", prior=None)
    assert result["status"] == "acquired"
    assert result["repo_meta"]["declared_created_by"] == "settletop-niles"
    assert result["repo_meta"]["declared_maintained_by"] == "SettleTop-Inc"
    assert result["repo_meta"]["declared_created_at"] == "2026-08-09T00:00:00Z"


def test_no_record_yields_none_keys(monkeypatch):
    """No asset-record.json in the fetched files: the three declared_* keys
    are still present on repo_meta (the acquired branch always emits them),
    just None rather than dropped."""
    src = _direct(_Http(), _NoRecordFetcher(), monkeypatch)
    result = src.acquire("https://github.com/o/n", prior=None)
    assert result["status"] == "acquired"
    assert result["repo_meta"]["declared_created_by"] is None
    assert result["repo_meta"]["declared_maintained_by"] is None
    assert result["repo_meta"]["declared_created_at"] is None


def test_unchanged_branch_has_no_declared_keys(monkeypatch):
    """prior matches sha+allowlist -> status unchanged, no files fetched, so
    the record is unreadable there. The keys must be absent (not None) so
    CodeRoot's executor can tell "no info this run" apart from "record
    removed" and preserve prior DB values instead of nulling them out."""
    f = _RecordFetcher()
    src = _direct(_Http(sha="abc123"), f, monkeypatch)
    result = src.acquire(
        "https://github.com/o/n",
        prior={"commit_sha": "abc123", "allowlist_version": content_mod.ALLOWLIST_VERSION},
    )
    assert result["status"] == "unchanged"
    assert "declared_created_by" not in result["repo_meta"]
    assert "declared_maintained_by" not in result["repo_meta"]
    assert "declared_created_at" not in result["repo_meta"]
    assert f.calls == 0
