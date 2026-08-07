"""Pure subdir subject helpers — no DB, no I/O.

`subdir=""` is the whole-repo default and must reproduce current behavior
exactly. A non-empty subdir scopes identity (asset_id, source_url) and
content filtering to a `<subdir>/` subtree of an already-fetched repo.
"""
from __future__ import annotations

import hashlib


def normalize_subdir(raw: str | None) -> str:
    """Normalize a user-supplied subdir path.

    Normalizes '\\' to '/' first (so a Windows-typed path works AND a
    backslash-delimited traversal is caught), strips leading/trailing '/',
    collapses internal '//', and returns "" for None / empty / ".". Raises
    ValueError on any ".." path segment or an absolute/drive-letter path.
    This is the canonical validation gate for a user-supplied subdir.
    """
    if raw is None:
        return ""
    s = raw.replace("\\", "/").strip().strip("/")
    if s in ("", "."):
        return ""
    if ":" in s:
        # Windows drive letter (e.g. "C:/x") or any other embedded colon.
        raise ValueError(f"invalid subdir: {raw!r}")
    segments = [seg for seg in s.split("/") if seg != ""]
    if any(seg == ".." for seg in segments):
        raise ValueError(f"invalid subdir: {raw!r}")
    return "/".join(segments)


def asset_id(repo_id: str, subdir: str) -> str:
    """Deterministic id for a (repo, subdir) subject."""
    return hashlib.sha256(f"{repo_id}:{subdir}".encode()).hexdigest()[:32]


def scoped_source_url(host: str, owner: str, name: str, commit_sha: str, subdir: str) -> str:
    """Commit-pinned source URL, scoped to a subdir when non-empty.

    A subdir cannot be represented without a pinned commit (GitHub `/tree/<ref>/<path>`
    needs a ref), so a missing commit_sha falls back to the plain repo URL rather than
    emitting a malformed `/tree//<subdir>`. In the real pipeline the commit is always
    present by assess time; this is defensive.
    """
    base = f"https://{host}/{owner}/{name}"
    if not subdir or not commit_sha:
        return base
    return f"{base}/tree/{commit_sha}/{subdir}"


def _in_subdir(path: str, subdir: str) -> bool:
    return path == subdir or path.startswith(subdir + "/")


def _reroot(path: str, subdir: str) -> str:
    if path == subdir:
        return path.rsplit("/", 1)[-1]
    return path[len(subdir) + 1:]


def filter_content_to_subdir(content: dict[str, str], subdir: str) -> dict[str, str]:
    """Keep only entries under `<subdir>/`, re-rooted relative to subdir.

    `subdir == ""` returns the input unchanged (whole-repo).

    RE-ROOTING IS LOAD-BEARING FOR DEPENDENCIES, DELIBERATELY. Re-rooting turns
    `<subdir>/pyproject.toml` into `pyproject.toml`, so the root-only classification
    dep reader (`classify_agent._all_deps`) reads the SUBDIR'S OWN manifest as its
    root manifest. That is the intended semantics: for a subdir subject, its own
    manifest IS its root manifest — a marketplace-ingested `packages/agentkit` asset
    is classified from `packages/agentkit/pyproject.toml`, not from the monorepo's.

    This only became observable once acquire widened to fetch non-root dependency
    manifests (`content.ALLOWLIST_VERSION` 4->5, 2026-07-25); before that the file was
    never in `content`, so subdir subjects fell through to `not_an_asset` purely for
    lack of the fetch. The narrow/wide dep split keeps this contained: the subdir's own
    manifest classifies the SUBDIR subject only — a whole-repo assessment of the same
    content still reads only the repo-root manifest, so a subpackage's `crewai` can
    never make the whole repo an `agent`. `test_assessment_recall_drift` pins both
    halves of that pairing.
    """
    if not subdir:
        return content
    return {_reroot(p, subdir): v for p, v in content.items() if _in_subdir(p, subdir)}


def filter_paths_to_subdir(paths, subdir: str) -> tuple[str, ...]:
    """Same filter+re-root as filter_content_to_subdir, over a path tuple."""
    if not subdir:
        return tuple(paths)
    return tuple(_reroot(p, subdir) for p in paths if _in_subdir(p, subdir))
