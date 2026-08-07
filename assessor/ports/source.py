"""Where bytes come from, as a port.

DirectSource acquires from GitHub and is what a standalone operator runs.
The CodeRoot-MCP plan adds McpSource, which reads an already-persisted
snapshot — that is what preserves zero-GitHub-cost re-derivation."""
from __future__ import annotations

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
    if len(parts) != 2 or not _valid_slug(parts[0]) or not _valid_slug(parts[1]):
        raise ValueError(f"invalid repo_url: {repo_url!r}")
    return parts[0], parts[1]


def _bucket_b(repo_obj: dict) -> dict:
    lic = repo_obj.get("license") or {}
    spdx = lic.get("spdx_id") if isinstance(lic, dict) else None
    if spdx in ("NOASSERTION", ""):
        spdx = None
    return {"description": repo_obj.get("description"),
            "homepage": repo_obj.get("homepage"),
            "topics": repo_obj.get("topics") or [],
            "license_spdx": spdx}


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
        alv = content_mod.ALLOWLIST_VERSION
        # SHA reuse: the caller already holds this snapshot and the selection
        # allowlist has not widened, so there is nothing to re-read. Metadata is
        # still refreshed — resolve_head returned it and it costs nothing.
        if (prior is not None and prior.get("commit_sha") == sha
                and prior.get("allowlist_version") == alv):
            return {"status": "unchanged", "snapshot": None, "commit_sha": sha,
                    "metadata": meta, "allowlist_version": alv}
        clone_url = f"https://github.com/{owner}/{name}.git"
        files, paths, capped, hits = self.fetcher.fetch(clone_url, f"{owner}/{name}", sha)
        return {"status": "acquired", "commit_sha": sha, "metadata": meta,
                "allowlist_version": alv,
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
