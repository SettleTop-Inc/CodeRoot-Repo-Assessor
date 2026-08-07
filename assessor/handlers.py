"""Surface-agnostic request handling. Knows nothing about HTTP or MCP, so both
surfaces serve identical behaviour rather than two implementations that drift."""
from __future__ import annotations

from .assessment import assemble
from .config import Settings
from .errors import NotDerivable
from .ports.cache import CachePort
from .ports.source import AcquireResult, Prior, Source, Subject


def acquire_handler(source: Source, repo_url: str,
                    prior: Prior | None) -> AcquireResult:
    return source.acquire(repo_url, prior=prior)


def assess_handler(source: Source, cache: CachePort, settings: Settings,
                   subject: Subject) -> dict:
    snap = source.snapshot(subject)
    if not snap or not snap.get("files"):
        # Distinct from a 5xx on purpose: the caller should re-acquire, not
        # retry. Deriving from a partial set would produce a record that reads
        # as "we looked and found nothing".
        raise NotDerivable("snapshot has no file bodies")
    metrics = source.metrics(subject) or {}
    return assemble.build(
        subject["repo_url"], snap["files"], snap["commit_sha"],
        metrics.get("license"),
        releases=metrics.get("releases"),
        bucket_b=snap["metadata"],
        source_coverage_capped=snap["source_coverage_capped"],
        cache=cache, settings=settings,
        paths=snap["tree_paths"], hits=snap["marker_hits"],
        subdir=subject["subdir"], subject_key=subject["subject_key"])
