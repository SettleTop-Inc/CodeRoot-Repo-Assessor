"""Where bytes come from, as a port.

DirectSource acquires from GitHub and is what a standalone operator runs.
The CodeRoot-MCP plan adds McpSource, which reads an already-persisted
snapshot — that is what preserves zero-GitHub-cost re-derivation."""
from __future__ import annotations

import hashlib
from typing import Literal, Protocol, TypedDict, runtime_checkable
from urllib.parse import urlsplit

from ..assessment import content as content_mod
from ..errors import RepoGone
from ..vendored import _valid_slug


class Subject(TypedDict):
    repo_url: str
    subject_key: str
    commit_sha: str
    subdir: str


class Prior(TypedDict):
    commit_sha: str
    allowlist_version: int


class Snapshot(TypedDict):
    commit_sha: str
    metadata: dict
    tree_paths: tuple[str, ...]
    tree_capped: bool
    marker_hits: tuple[dict, ...]
    files: dict[str, str]
    source_coverage_capped: bool
    allowlist_version: int


class AcquireResult(TypedDict):
    status: Literal["acquired", "unchanged"]
    snapshot: Snapshot | None
    commit_sha: str
    metadata: dict
    repo_meta: dict
    allowlist_version: int


@runtime_checkable
class Source(Protocol):
    def acquire(self, repo_url: str, *, prior: Prior | None) -> AcquireResult: ...
    def snapshot(self, subject: Subject) -> Snapshot: ...
    def metrics(self, subject: Subject) -> dict | None: ...
    def prior_assessment(self, subject: Subject) -> dict | None: ...


def _split(repo_url: str) -> tuple[str, str]:
    # Validate BEFORE any git call: this url came from the caller. Parse via
    # urlsplit rather than taking the last two "/"-segments of the raw string —
    # a naive rsplit lets "https://github.com/../../etc/passwd" through, because
    # its *last* two segments ("etc", "passwd") are themselves valid slugs even
    # though the path is a traversal payload. Requiring the path to resolve to
    # exactly two segments closes that: extra segments of any kind are rejected.
    path = urlsplit(repo_url).path.strip("/")
    parts = path.split("/") if path else []
    if len(parts) != 2:
        raise ValueError(f"invalid repo_url: {repo_url!r}")
    owner, name = parts
    # GitHub's own canonical clone-URL form ("https://github.com/o/n.git") has a
    # trailing ".git" on the repo segment. Callers will paste that form, so strip
    # exactly one suffix before validating rather than querying a repo literally
    # named "n.git" (a 404 that becomes a wrong RepoGone/410 for a repo that
    # exists) and cloning ".../n.git.git".
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if not _valid_slug(owner) or not _valid_slug(name):
        raise ValueError(f"invalid repo_url: {repo_url!r}")
    return owner, name


def _bucket_b(repo_obj: dict) -> dict:
    lic = repo_obj.get("license") or {}
    spdx = lic.get("spdx_id") if isinstance(lic, dict) else None
    if spdx in ("NOASSERTION", ""):
        spdx = None
    return {"description": repo_obj.get("description"),
            "homepage": repo_obj.get("homepage"),
            "topics": repo_obj.get("topics") or [],
            "license_spdx": spdx}


def _repo_meta(repo_obj: dict) -> dict:
    """The FULL metadata CodeRoot persists to `repo_acquisition`, which is a
    superset of `_bucket_b`'s four assess-side fields.

    Kept separate from `_bucket_b` rather than widening it: `assemble.build`
    consumes bucket_b and must not start seeing fields it has no use for, and
    one key name meaning two shapes is exactly the confusion this split avoids.

    An absent field stays None — NEVER a guessed False or "". `creation_info.py`
    reads `repo_fork IS NULL` as "not acquired", so coercing an absent `fork` to
    False would assert the repo is confirmed-not-a-fork on no evidence. Both
    fork pointers are carried: `parent` is what this was forked from, `source`
    is the fork-network root, and only the root names the creator.
    """
    lic = repo_obj.get("license") or {}
    spdx = lic.get("spdx_id") if isinstance(lic, dict) else None
    if spdx in ("NOASSERTION", ""):
        spdx = None
    par = repo_obj.get("parent") or {}
    src = repo_obj.get("source") or {}
    own = repo_obj.get("owner") or {}
    fork = repo_obj.get("fork")
    topics = repo_obj.get("topics")
    return {
        "default_branch": repo_obj.get("default_branch"),
        "description": repo_obj.get("description"),
        "homepage": repo_obj.get("homepage"),
        "topics": topics if isinstance(topics, list) else [],
        "license_spdx": spdx,
        "repo_created_at": repo_obj.get("created_at"),
        "repo_updated_at": repo_obj.get("updated_at"),
        "repo_pushed_at": repo_obj.get("pushed_at"),
        "repo_fork": bool(fork) if fork is not None else None,
        "repo_parent_full_name": par.get("full_name") if isinstance(par, dict) else None,
        "repo_source_full_name": src.get("full_name") if isinstance(src, dict) else None,
        "repo_owner_login": own.get("login") if isinstance(own, dict) else None,
        "repo_owner_type": own.get("type") if isinstance(own, dict) else None,
    }


class DirectSource:
    def __init__(self, settings, http, fetcher) -> None:
        self.settings, self.http, self.fetcher = settings, http, fetcher

    def acquire(self, repo_url: str, *, prior: Prior | None) -> AcquireResult:
        owner, name = _split(repo_url)
        try:
            sha, repo_obj = content_mod.resolve_head(self.http, owner, name)
        except content_mod.RepoGone as exc:
            raise RepoGone(str(exc)) from exc
        meta = _bucket_b(repo_obj)
        repo_meta = _repo_meta(repo_obj)
        alv = content_mod.ALLOWLIST_VERSION
        # SHA reuse: the caller already holds this snapshot and the selection
        # allowlist has not widened, so there is nothing to re-read. Metadata is
        # still refreshed — resolve_head returned it and it costs nothing.
        if (prior is not None and prior.get("commit_sha") == sha
                and prior.get("allowlist_version") == alv):
            return {"status": "unchanged", "snapshot": None, "commit_sha": sha,
                    "metadata": meta, "repo_meta": repo_meta,
                    "allowlist_version": alv}
        clone_url = f"https://github.com/{owner}/{name}.git"
        # GitContentFetcher's repo_id must satisfy _REPO_KEY_RE (hex only — it was
        # a database UUID in CodeRoot and is used as a bare-repo directory name),
        # so "owner/name" itself is rejected before any git call. There is no UUID
        # here; a stable, deterministic hex digest of the slug is the honest
        # equivalent and keeps the on-disk cache path fixed per repo.
        repo_key = hashlib.sha256(f"{owner}/{name}".encode()).hexdigest()
        files, paths, capped, hits = self.fetcher.fetch(clone_url, repo_key, sha)
        return {"status": "acquired", "commit_sha": sha, "metadata": meta,
                "repo_meta": repo_meta, "allowlist_version": alv,
                "snapshot": {"commit_sha": sha, "metadata": meta,
                             "tree_paths": tuple(paths), "tree_capped": capped,
                             "marker_hits": tuple(hits), "files": files,
                             "source_coverage_capped": capped,
                             "allowlist_version": alv}}

    def snapshot(self, subject: Subject) -> Snapshot:
        result = self.acquire(subject["repo_url"], prior=None)
        return result["snapshot"]

    def metrics(self, subject: Subject) -> dict | None:
        return None      # no Aveloxis standalone; license/releases stay unknown

    def prior_assessment(self, subject: Subject) -> dict | None:
        return None      # no assessment history standalone
