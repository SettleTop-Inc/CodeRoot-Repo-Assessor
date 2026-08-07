"""The HTTP surface. Thin: parse, authenticate, delegate to handlers, map typed
errors to status codes. All judgment lives in handlers.py."""
from __future__ import annotations

import secrets

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings, get_settings
from .errors import NotDerivable, RepoGone
from .handlers import acquire_handler, assess_handler
from .ports.cache import CachePort, NullCache
from .ports.source import Source
from .versions import version_payload
from .wiring import build_direct_source


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
    # docs_url/redoc_url/openapi_url disabled: the default /docs, /redoc and
    # /openapi.json are unauthenticated and would disclose the full request/
    # response schema — including /v1/acquire's, which clones a caller-supplied
    # url using the operator's GitHub tokens (see config.py's fail-closed
    # validator comment). No test needs them.
    app = FastAPI(title="CodeRoot Repo Assessor",
                 docs_url=None, redoc_url=None, openapi_url=None)

    def auth(authorization: str | None = Header(default=None)) -> None:
        if settings.assessor_allow_anonymous:
            return
        expected = f"Bearer {settings.assessor_api_token}"
        # Guard the None case before compare_digest, which requires str/bytes
        # on both sides and would raise TypeError on a missing header instead
        # of the intended 401. Compare as bytes, not str: compare_digest
        # rejects non-ASCII *strings* outright (TypeError), so an
        # unauthenticated caller sending a header with any byte >= 0x80 (a
        # pasted token with a smart quote, any multi-byte UTF-8 character)
        # would 500 inside the auth check itself before any bearer was even
        # checked. Encoding removes the restriction entirely rather than
        # special-casing non-ASCII input. Constant-time so a wrong bearer
        # can't be distinguished by timing from a near-miss.
        if authorization is None or not secrets.compare_digest(
                authorization.encode(), expected.encode()):
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
        # ref-pinning is not implemented: DirectSource always resolves HEAD.
        # Silently ignoring a caller's `ref` would clone a different commit
        # than the one they asked for, so refuse explicitly rather than
        # quietly substituting HEAD. The field stays on the model so the
        # request contract shape is stable for when ref-pinning lands.
        if body.ref is not None:
            return JSONResponse(status_code=400,
                                content={"error": "unsupported_field", "field": "ref",
                                         "reason": "ref pinning is not supported by "
                                                   "this deployment"})
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
        # McpSource does not exist yet (a later, separate plan). Silently
        # falling back to DirectSource for `source: "mcp"` would perform a
        # live GitHub acquisition instead of the zero-cost re-derivation the
        # caller asked for, so refuse explicitly. The field stays on the
        # model so the request contract shape is stable for when McpSource
        # lands.
        if body.source != "direct":
            return JSONResponse(status_code=400,
                                content={"error": "unsupported_field", "field": "source",
                                         "reason": f"source {body.source!r} is not "
                                                   "supported by this deployment"})
        try:
            return assess_handler(source, cache, settings, body.subject.model_dump())
        except NotDerivable as exc:
            return JSONResponse(status_code=422,
                                content={"error": "not_derivable", "reason": str(exc)})
        except RepoGone:
            return JSONResponse(status_code=410, content={"error": "repo_gone"})
        except ValueError as exc:
            # Same class of bug the /v1/acquire branch above guards: a
            # malformed repo_url reaches DirectSource._split() via
            # source.snapshot() -> acquire() before any network call, and
            # without this handler it was an unhandled 500 — exactly what the
            # typed error taxonomy exists to prevent.
            return JSONResponse(status_code=400,
                                content={"error": "invalid_repo_url", "reason": str(exc)})

    return app


def create_app() -> FastAPI:
    """Build the production app. A factory, not a module-level instance: importing
    this module must have no side effects, and get_settings() deliberately raises
    when auth is unconfigured (config.py's fail-closed validator)."""
    s = get_settings()
    return build_app(s, build_direct_source(s), NullCache())
