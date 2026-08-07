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
    if not snap:
        # Distinct from a 5xx on purpose: the caller should re-acquire, not
        # retry. This is the "could not be READ" state — no snapshot at all —
        # not "was read and found nothing". An empty-but-present `files` dict
        # is a legitimate input: CodeRoot's own pipeline reaches assemble.build
        # with empty content whenever acquisition genuinely succeeded and
        # found nothing selectable, and derives a normal not_an_asset record
        # rather than refusing. Conflating the two here (checking
        # `snap.get("files")` too) previously raised NotDerivable for those
        # repos, diverging from CodeRoot's behaviour — see the Task 11 parity
        # harness, which is what caught it.
        raise NotDerivable("no snapshot available")
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
