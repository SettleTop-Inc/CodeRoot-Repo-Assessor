"""The single seam the codebase uses to talk to a model (centralized LLM gateway)."""
from __future__ import annotations

from typing import Protocol


class LlmError(Exception):
    """Base for gateway failures — always caught best-effort by the client."""


class LlmHttpError(LlmError):
    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(f"llm http {status}: {detail}")
        self.status = status


class LlmTimeout(LlmError):
    """The model call exceeded its timeout (distinct from unreachable/other http)."""


class Provider(Protocol):
    def chat(self, system: str, user: str, *, json_mode: bool,
             temperature: float, max_tokens: int, timeout_s: float) -> str: ...
