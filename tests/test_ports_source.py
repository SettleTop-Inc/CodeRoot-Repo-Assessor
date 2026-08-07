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


def test_split_strips_a_single_trailing_dot_git_suffix():
    """"https://github.com/o/n.git" is GitHub's own canonical clone-URL form, so
    callers will paste it. Without stripping the suffix, resolve_head would
    query a repo literally named "n.git" (a wrong RepoGone/410 for a repo that
    exists) and the acquire branch would clone ".../n.git.git"."""
    from assessor.ports.source import _split

    assert _split("https://github.com/o/n.git") == ("o", "n")


def test_fetcher_repo_id_satisfies_the_real_validator(monkeypatch):
    """The `_Fetcher` test double ignores repo_id entirely, so none of the
    tests above can catch a repo_id that GitContentFetcher's real precondition
    would reject. repo_id must be hex-only (it doubled as a database UUID in
    CodeRoot and is used as a bare-repo cache directory name), so the naive
    "owner/name" string fails validation and every real, non-short-circuited
    acquire would raise before any git call. Exercise the real validator
    against what DirectSource actually generates, for a realistic owner/name."""
    from assessor.assessment.git_fetch import _validate_repo_key

    captured = {}

    class _CapturingFetcher:
        def fetch(self, clone_url, repo_id, sha):
            captured["repo_id"] = repo_id
            return ({}, (), False, [])

    src = _direct(_Http(), _CapturingFetcher(), monkeypatch)
    src.acquire("https://github.com/SettleTop-Inc/CodeRoot-MCP", prior=None)
    # Raises ContentUnavailable if the real validator rejects the generated key.
    assert _validate_repo_key(captured["repo_id"]) == captured["repo_id"]


_EXPECTED_REPO_META_KEYS = {
    "default_branch", "description", "homepage", "topics", "license_spdx",
    "repo_created_at", "repo_updated_at", "repo_pushed_at", "repo_fork",
    "repo_parent_full_name", "repo_source_full_name", "repo_owner_login",
    "repo_owner_type",
}


def test_repo_meta_carries_every_column_coderoot_persists():
    """CodeRoot's repo_acquisition stores 13 fields from the GitHub repo object
    and creation_info.py — the live marketplace feed — reads 8 of them. A
    narrower payload nulls them out silently."""
    from assessor.ports.source import _repo_meta
    repo_obj = {
        "default_branch": "main", "description": "d", "homepage": "h",
        "topics": ["a"], "license": {"spdx_id": "MIT"},
        "created_at": "2020-01-01T00:00:00Z", "updated_at": "2021-01-01T00:00:00Z",
        "pushed_at": "2022-01-01T00:00:00Z", "fork": True,
        "parent": {"full_name": "p/p"}, "source": {"full_name": "s/s"},
        "owner": {"login": "o", "type": "Organization"},
    }
    assert _repo_meta(repo_obj) == {
        "default_branch": "main", "description": "d", "homepage": "h",
        "topics": ["a"], "license_spdx": "MIT",
        "repo_created_at": "2020-01-01T00:00:00Z",
        "repo_updated_at": "2021-01-01T00:00:00Z",
        "repo_pushed_at": "2022-01-01T00:00:00Z",
        "repo_fork": True, "repo_parent_full_name": "p/p",
        "repo_source_full_name": "s/s",
        "repo_owner_login": "o", "repo_owner_type": "Organization"}


def test_absent_fields_stay_none_never_guessed():
    """NULL means 'not acquired'. Coercing an absent `fork` to False would tell
    creation_info.py the repo is confirmed-not-a-fork (creation_info.py:14)."""
    from assessor.ports.source import _repo_meta

    m = _repo_meta({})
    assert m["repo_fork"] is None
    assert m["repo_parent_full_name"] is None
    assert m["repo_source_full_name"] is None
    assert m["repo_owner_login"] is None
    assert m["repo_owner_type"] is None
    assert m["license_spdx"] is None
    assert m["topics"] == []


def test_noassertion_license_is_normalized_to_none():
    """Matches CodeRoot's acquire.py:49 — GitHub returns NOASSERTION for
    unrecognized licences and storing that string would be a false positive."""
    from assessor.ports.source import _repo_meta

    assert _repo_meta({"license": {"spdx_id": "NOASSERTION"}})["license_spdx"] is None
    assert _repo_meta({"license": {"spdx_id": ""}})["license_spdx"] is None


def test_acquire_result_carries_repo_meta_on_both_branches(monkeypatch):
    """`unchanged` (SHA reuse) must carry refreshed metadata too — spec §5.1:
    Bucket B is refreshed on every run including the reuse path."""
    repo_obj = {
        "default_branch": "main", "description": "d", "homepage": "h",
        "topics": ["a"], "license": {"spdx_id": "MIT"},
        "created_at": "2020-01-01T00:00:00Z", "updated_at": "2021-01-01T00:00:00Z",
        "pushed_at": "2022-01-01T00:00:00Z", "fork": True,
        "parent": {"full_name": "p/p"}, "source": {"full_name": "s/s"},
        "owner": {"login": "o", "type": "Organization"},
    }
    http = _Http(sha="abc123", repo_obj=repo_obj)

    # acquired branch: no prior, so DirectSource clones.
    src = _direct(http, _Fetcher(), monkeypatch)
    r = src.acquire("https://github.com/o/n", prior=None)
    assert r["status"] == "acquired"
    assert set(r["repo_meta"]) == _EXPECTED_REPO_META_KEYS
    assert r["repo_meta"]["repo_owner_login"] == "o"

    # unchanged branch: matching prior short-circuits the clone, metadata
    # (including repo_meta) is still refreshed from resolve_head.
    src2 = _direct(http, _Fetcher(), monkeypatch)
    r2 = src2.acquire("https://github.com/o/n",
                       prior={"commit_sha": "abc123", "allowlist_version": 7})
    assert r2["status"] == "unchanged"
    assert set(r2["repo_meta"]) == _EXPECTED_REPO_META_KEYS
    assert r2["repo_meta"]["repo_owner_login"] == "o"


def test_snapshot_returns_the_acquired_snapshot(monkeypatch):
    """snapshot() ignores subject["commit_sha"] and always re-resolves HEAD via
    prior=None — this is deliberate (Task 9's MCP tool passes commit_sha: "" for
    exactly this reason), pinned here so the choice is explicit rather than
    accidental."""
    src = _direct(_Http(sha="abc123"), _Fetcher(), monkeypatch)
    subject = {"repo_url": "https://github.com/o/n", "subject_key": "o/n",
               "commit_sha": "deadbeef", "subdir": ""}
    snap = src.snapshot(subject)
    assert snap["commit_sha"] == "abc123"
    assert snap["files"] == {"README.md": "hi"}
