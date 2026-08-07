"""Startup-time configuration. Every value arrives as an environment variable so
that local Docker Desktop and Kubernetes differ only in the injection mechanism.

No credential is ever accepted in a request body — see the spec's §10."""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Raised at construction when the configuration is unsafe to serve with."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False,
                                      extra="ignore")

    # --- model gateway (the six fields llm/client.py actually reads) ---
    llm_provider: str = "none"          # none | openai_compatible
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_s: int = 60
    llm_max_tokens: int = 1024

    # --- acquisition ---
    github_tokens: str = ""             # comma-separated PATs
    acquire_cache_dir: str = "/acquire-cache"
    acquire_timeout_s: int = 120
    blob_limit_bytes: int = 1024 * 1024
    max_tree_entries: int = 200_000

    # --- upstream data plane (unused until the CodeRoot-MCP plan) ---
    coderoot_mcp_url: str | None = None
    coderoot_mcp_token: str | None = None

    # --- inbound auth ---
    assessor_api_token: str | None = None
    assessor_allow_anonymous: bool = False
    assessor_bind_addr: str = "127.0.0.1"

    @property
    def github_token_list(self) -> list[str]:
        return [t.strip() for t in self.github_tokens.split(",") if t.strip()]

    @model_validator(mode="after")
    def _auth_fails_closed(self) -> "Settings":
        # An unauthenticated default would be materially worse here than for a
        # typical service: /v1/acquire clones a CALLER-SUPPLIED url using the
        # operator's GitHub tokens, so an open endpoint is a request-forgery
        # primitive with credentials attached. Refuse to start rather than serve.
        if not self.assessor_api_token and not self.assessor_allow_anonymous:
            raise ConfigError(
                "refusing to start unauthenticated: set ASSESSOR_API_TOKEN, or set "
                "ASSESSOR_ALLOW_ANONYMOUS=true to opt out deliberately")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
