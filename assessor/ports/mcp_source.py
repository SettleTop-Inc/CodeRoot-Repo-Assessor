"""Read an already-persisted snapshot from CodeRoot instead of fetching from GitHub.

This is what preserves zero-GitHub-cost re-derivation: a registry bump re-derives the
whole corpus from bytes CodeRoot already holds. `acquire` is deliberately refused —
this source reads what acquisition produced, it never performs one.

CodeRoot-MCP answers every tool with one of two discriminated failures, and this
module has to keep them APART (their payloads verified against CodeRoot-MCP's
`coderoot_mcp/server.py`, not assumed):

  {"error": "not_acquired"}   CodeRoot's plain 404 — there is no `repo_acquisition`
                              row for this repo. The snapshot cannot be READ, which
                              is exactly what `NotDerivable` means, so it is mapped
                              to one. Spec §8 row 1: 422 not_derivable -> CodeRoot
                              re-arms acquire and skips this assess.
  {"error": "upstream_error"} CodeRoot itself is down or erroring. That means RETRY,
                              not re-acquire, so it is deliberately left to
                              propagate into a 5xx -> AssessorUnavailable -> the
                              assess unit re-arms.

Collapsing them would break in one direction or the other: a `not_acquired` surfacing
as 5xx (what it did before this mapping) retries a repo that will never derive until
acquire runs, and never re-arms acquire — while an `upstream_error` surfacing as 422
would re-acquire a healthy corpus on every CodeRoot blip."""
from __future__ import annotations

from ..errors import NotDerivable
from .source import AcquireResult, Prior, Snapshot, Subject

_META_KEYS = ("description", "homepage", "topics", "license_spdx")


def _is_not_acquired(exc: BaseException) -> bool:
    """True only for CodeRoot-MCP's `{"error": "not_acquired"}` discriminator.

    Matches on the exception TYPE plus the payload's discriminator field, not on
    the stringified message: `McpToolError.__str__` is `f"{tool}: {detail}"`, and
    substring-matching that would also match a repo whose name contained the word.

    The `McpToolClient` import is deferred rather than module-level on purpose.
    `wiring.py` imports this module unconditionally, at import time, on EVERY
    deployment — including the unconfigured one that never speaks MCP at all —
    and `assessor.mcp_client` pulls in the whole `mcp` client stack. Importing it
    here would make `import assessor.wiring` (and therefore `assessor.app`) depend
    on that stack being importable, which is precisely the property wiring.py's
    `_build_mcp_client` docstring exists to protect. This function only runs on an
    exception path, so the deferred import costs a `sys.modules` hit on a call
    that has already failed."""
    from ..mcp_client import McpToolError
    return isinstance(exc, McpToolError) and exc.payload.get("error") == "not_acquired"


class McpSource:
    def __init__(self, client) -> None:
        self.client = client

    def acquire(self, repo_url: str, *, prior: Prior | None) -> AcquireResult:
        raise NotImplementedError(
            "McpSource reads persisted snapshots; use DirectSource to acquire")

    def snapshot(self, subject: Subject) -> Snapshot:
        try:
            return self._snapshot(subject)
        except Exception as exc:
            if _is_not_acquired(exc):
                raise NotDerivable(
                    "CodeRoot holds no acquisition for "
                    f"{subject['subject_key']!r}") from exc
            raise

    def _snapshot(self, subject: Subject) -> Snapshot:
        key = subject["subject_key"]
        s = self.client.get_subject(key, subject["subdir"])
        # `tree_paths` is the FULL tree inventory; `content_paths` is the much smaller
        # set acquisition actually stored bodies for. They are deliberately different
        # sizes — measured on the live corpus: qwen-code has 8144 tree paths and 133
        # stored bodies. Requesting bodies for the whole inventory would put ~8000 paths
        # in `missing` and raise NotDerivable on every repository, so the parity gate
        # could never pass. Ask only for what was stored; a gap in THAT set is a real
        # blob-gone condition.
        paths = tuple(s.get("tree_paths") or ())
        content_paths = list(s.get("content_paths") or ())
        got = self.client.read_files(key, s["commit_sha"], content_paths)
        if got.get("missing"):
            # Distinct from an empty snapshot on purpose: a blob that should exist and
            # does not means re-acquire, not derive-from-what-is-left.
            raise NotDerivable(
                f"{len(got['missing'])} blob(s) missing from the store: "
                f"{got['missing'][:3]}")
        return {"commit_sha": s["commit_sha"],
                "metadata": {k: s.get(k) for k in _META_KEYS},
                "tree_paths": paths, "tree_capped": bool(s.get("tree_capped")),
                "marker_hits": tuple(s.get("marker_hits") or ()),
                "files": got.get("files") or {},
                "source_coverage_capped": bool(s.get("source_coverage_capped")),
                "allowlist_version": s.get("allowlist_version")}

    def metrics(self, subject: Subject) -> dict | None:
        # Mapped for the same reason as `snapshot`, and it is the same
        # condition: CodeRoot-MCP's `get_metrics` calls the SAME
        # `/repos/{id}/subject` endpoint (coderoot_mcp/server.py:78), so its
        # `not_acquired` means "no repo_acquisition row" too, not "no metrics
        # row" (a repo with no metrics is a 200 with nulls). In practice
        # `assess_handler` calls `snapshot` first and that raises before this
        # is reached; mapping here closes the window where the acquisition row
        # is deleted BETWEEN the two calls, which would otherwise still 500.
        try:
            return self.client.get_metrics(subject["subject_key"])
        except Exception as exc:
            if _is_not_acquired(exc):
                raise NotDerivable(
                    "CodeRoot holds no acquisition for "
                    f"{subject['subject_key']!r}") from exc
            raise

    def prior_assessment(self, subject: Subject) -> dict | None:
        return self.client.get_prior_assessment(subject["subject_key"], subject["subdir"])
