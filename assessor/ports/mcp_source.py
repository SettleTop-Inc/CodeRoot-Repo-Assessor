"""Read an already-persisted snapshot from CodeRoot instead of fetching from GitHub.

This is what preserves zero-GitHub-cost re-derivation: a registry bump re-derives the
whole corpus from bytes CodeRoot already holds. `acquire` is deliberately refused —
this source reads what acquisition produced, it never performs one."""
from __future__ import annotations

from ..errors import NotDerivable
from .source import AcquireResult, Prior, Snapshot, Subject

_META_KEYS = ("description", "homepage", "topics", "license_spdx")


class McpSource:
    def __init__(self, client) -> None:
        self.client = client

    def acquire(self, repo_url: str, *, prior: Prior | None) -> AcquireResult:
        raise NotImplementedError(
            "McpSource reads persisted snapshots; use DirectSource to acquire")

    def snapshot(self, subject: Subject) -> Snapshot:
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
        return self.client.get_metrics(subject["subject_key"])

    def prior_assessment(self, subject: Subject) -> dict | None:
        return self.client.get_prior_assessment(subject["subject_key"], subject["subdir"])
