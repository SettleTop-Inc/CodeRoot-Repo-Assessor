"""Typed failures the surfaces map to responses.

Across a network boundary CodeRoot must be able to tell "re-acquire this" from
"retry me", or a missing blob becomes a silent partial assessment."""
from __future__ import annotations


class NotDerivable(Exception):
    """No snapshot could be produced at all — e.g. missing blobs, a source
    that could not read the repo. Maps to 422. The caller should re-acquire,
    not retry.

    NOT for a snapshot that was read successfully and legitimately contains
    no files: CodeRoot's own pipeline derives a normal not_an_asset record
    for that case rather than refusing, and diverging from it here (Task 11
    fix round 1) previously caused a real parity failure on
    octocat/Hello-World."""


class RepoGone(Exception):
    """Gone, renamed or private. Maps to 410. Terminal — do not retry."""
