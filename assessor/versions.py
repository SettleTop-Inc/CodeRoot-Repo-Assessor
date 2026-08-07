"""The versions CodeRoot polls to decide when to re-arm.

REGISTRY_VERSION drives an assess re-arm; ALLOWLIST_VERSION drives an acquire
re-arm. In CodeRoot these were bumped by hand-written migrations; once the
registry lives here, the trigger inverts and CodeRoot polls /v1/version."""
from __future__ import annotations

from .assessment.content import ALLOWLIST_VERSION
from .assessment.markers import MARKER_VOCAB_VERSION
from .assessment.registry import REGISTRY_VERSION


def version_payload() -> dict:
    return {"registry_version": REGISTRY_VERSION,
            "allowlist_version": ALLOWLIST_VERSION,
            "marker_vocab_version": MARKER_VOCAB_VERSION}
