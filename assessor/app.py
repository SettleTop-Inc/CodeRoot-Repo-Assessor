"""The HTTP surface. Thin: parse, authenticate, delegate to handlers, map typed
errors to status codes. All judgment lives in handlers.py."""
from __future__ import annotations

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .assessment.git_fetch import GitContentFetcher
from .config import Settings, get_settings
from .errors import NotDerivable, RepoGone
from .handlers import acquire_handler, assess_handler
from .ports.cache import CachePort, NullCache
from .ports.source import DirectSource, Source
from .versions import version_payload


class SubjectIn(BaseModel):
    repo_url: str
    subject_key: str
    commit_sha: str = ""
    subdir: str = ""


class PriorIn(BaseModel):
    commit_sha: str
    allowlist_version: int


class AcquireIn(BaseModel):
    repo_url: str
    ref: str | None = None
    prior: PriorIn | None = None


class AssessIn(BaseModel):
    subject: SubjectIn
    source: str = "direct"


def build_app(settings: Settings, source: Source, cache: CachePort) -> FastAPI:
    app = FastAPI(title="CodeRoot Repo Assessor")

    def auth(authorization: str | None = Header(default=None)) -> None:
        if settings.assessor_allow_anonymous:
            return
        expected = f"Bearer {settings.assessor_api_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict:
        # A down model gateway is silent — it produces the same empty result as
        # a model that declined. Report reachability explicitly so the operator
        # can tell those apart without reading assessment output.
        if settings.llm_provider == "none" or not settings.llm_base_url:
            return {"status": "ok", "llm": "off"}
        try:
            r = httpx.get(settings.llm_base_url.rstrip("/") + "/models", timeout=5)
            return {"status": "ok", "llm": "up" if r.status_code < 500 else "down"}
        except Exception:
            return {"status": "ok", "llm": "unreachable"}

    @app.get("/v1/version", dependencies=[Depends(auth)])
    def version() -> dict:
        return version_payload()

    @app.post("/v1/acquire", dependencies=[Depends(auth)])
    def acquire(body: AcquireIn):
        try:
            return acquire_handler(source, body.repo_url,
                                   body.prior.model_dump() if body.prior else None)
        except RepoGone:
            return JSONResponse(status_code=410, content={"error": "repo_gone"})
        except ValueError as exc:
            return JSONResponse(status_code=400,
                                content={"error": "invalid_repo_url", "reason": str(exc)})

    @app.post("/v1/assess", dependencies=[Depends(auth)])
    def assess(body: AssessIn):
        try:
            return assess_handler(source, cache, settings, body.subject.model_dump())
        except NotDerivable as exc:
            return JSONResponse(status_code=422,
                                content={"error": "not_derivable", "reason": str(exc)})
        except RepoGone:
            return JSONResponse(status_code=410, content={"error": "repo_gone"})

    return app


def create_app() -> FastAPI:
    """Build the production app. A factory, not a module-level instance: importing
    this module must have no side effects, and get_settings() deliberately raises
    when auth is unconfigured (config.py's fail-closed validator)."""
    s = get_settings()
    http = httpx.Client(timeout=s.acquire_timeout_s,
                        headers={"Authorization": f"Bearer {s.github_token_list[0]}"}
                        if s.github_token_list else {})
    fetcher = GitContentFetcher(s.acquire_cache_dir, blob_limit=s.blob_limit_bytes,
                                timeout_s=s.acquire_timeout_s,
                                max_entries=s.max_tree_entries)
    return build_app(s, DirectSource(s, http, fetcher), NullCache())
