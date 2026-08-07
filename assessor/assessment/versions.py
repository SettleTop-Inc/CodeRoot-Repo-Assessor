"""Version tracking (Fact) from already-collected GitHub releases (Bucket A).

Reads repo_metrics.releases (list of {tag, name, published_at, is_prerelease}).
Pure — no I/O. Absent releases -> an explicit gap, never guessed.
"""
from __future__ import annotations

from .shapes import fact, unknown

_SRC = "collected: github releases"


def build(releases: list[dict] | None, commit_sha: str | None) -> dict:
    rels = [r for r in (releases or []) if isinstance(r, dict)]
    out: dict = {}
    if rels:
        latest = max(rels, key=lambda r: (r.get("published_at") or ""))
        out["latest_release"] = fact(
            {"tag": latest.get("tag"), "name": latest.get("name"),
             "published_at": latest.get("published_at"), "is_prerelease": bool(latest.get("is_prerelease"))},
            _SRC, [{"path": "repo_metrics.releases", "marker": "latest release"}])
        out["release_count"] = fact(len(rels), _SRC, [{"path": "repo_metrics.releases", "marker": "count"}])
    else:
        out["latest_release"] = unknown("no published releases")
        out["release_count"] = fact(0, _SRC, [])
    out["assessed_commit"] = (
        fact(commit_sha, "assessed default-branch commit", [{"path": "repo", "marker": "commit sha"}])
        if commit_sha else unknown("no commit sha"))
    return out
