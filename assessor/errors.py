"""Typed failures the surfaces map to responses.

Across a network boundary CodeRoot must be able to tell "re-acquire this" from
"retry me", or a missing blob becomes a silent partial assessment."""
from __future__ import annotations


class NotDerivable(Exception):
    """The snapshot cannot produce a record — missing bodies, empty content.
    Maps to 422. The caller should re-acquire, not retry."""


class RepoGone(Exception):
    """Gone, renamed or private. Maps to 410. Terminal — do not retry."""
