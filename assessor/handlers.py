"""Surface-agnostic request handling. Knows nothing about HTTP or MCP, so both
surfaces serve identical behaviour rather than two implementations that drift."""
from __future__ import annotations

from .assessment import assemble
from .assessment.subject import normalize_subdir
from .config import Settings
from .errors import InvalidSubdir, NotDerivable
from .ports.cache import CachePort
from .ports.source import AcquireResult, Prior, Source, Subject


def acquire_handler(source: Source, repo_url: str,
                    prior: Prior | None) -> AcquireResult:
    return source.acquire(repo_url, prior=prior)


def assess_handler(source: Source, cache: CachePort, settings: Settings,
                   subject: Subject) -> dict:
    # THE subdir validation gate, and it belongs here rather than on either
    # surface: `normalize_subdir`'s own docstring calls itself "the canonical
    # validation gate for a user-supplied subdir", and this module is the one
    # place both surfaces pass through — putting it on app.py alone would
    # leave mcp_server.py's `assess_repository(subdir=...)` unguarded.
    #
    # Nothing called it at all until this fix. On `main` that was safe by
    # construction: the only route into `assemble.build` was CodeRoot's
    # `api/routers/repos.py`, which normalized before dispatching. Putting
    # `assemble.build` behind a public HTTP boundary silently dropped that
    # guarantee, and the raw value reaches `subject.scoped_source_url`, which
    # interpolates it into an f-string — so `subdir="../../other/repo"`
    # yielded a `source_url` naming a DIFFERENT repository, which CodeRoot
    # then persisted into `assessment["source_url"]` and served preferentially
    # (api/routers/assessment.py:50).
    #
    # Normalizing (not merely rejecting) is safe for CodeRoot's existing
    # traffic because `normalize_subdir` is idempotent — verified over
    # '', 'pkg/a', '/pkg/a/', 'pkg//a', 'a/b/c': n(n(x)) == n(x) — and
    # CodeRoot already sends normalized values, so every current request maps
    # to itself. The normalized value replaces the raw one for the WHOLE
    # handler, not just for `assemble.build`: `McpSource.snapshot` and
    # `prior_assessment` also read `subject["subdir"]` and must agree with the
    # subdir the record is ultimately stamped with.
    try:
        subject = {**subject, "subdir": normalize_subdir(subject.get("subdir"))}
    except ValueError as exc:
        raise InvalidSubdir(str(exc)) from exc
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
