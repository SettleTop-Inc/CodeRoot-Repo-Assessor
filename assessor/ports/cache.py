"""The LLM response cache, as a port.

In CodeRoot this was a SQLAlchemy `session` threaded five levels deep
(service -> assemble -> purpose -> llm.client -> llm.cache) and used at the
terminus for exactly two raw SQL statements, no-opping when None. It was never
an ORM session and carried no schema knowledge, so it becomes two methods."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CachePort(Protocol):
    def get(self, model: str, prompt_sha256: str) -> dict | None: ...
    def put(self, model: str, prompt_sha256: str, response: dict) -> None: ...


class NullCache:
    """The standalone default. The gateway is fully functional without a cache —
    it simply re-calls the model.

    NOT suitable for an event-emitting pipeline: an uncached retry whose model
    returns a different citation yields different `promoted_types`, therefore
    different `asset_types`, therefore a spurious `changed` webhook for a repo
    that did not change. CodeRoot's path must use a real cache (spec §9.6)."""

    def get(self, model: str, prompt_sha256: str) -> dict | None:
        return None

    def put(self, model: str, prompt_sha256: str, response: dict) -> None:
        return None
