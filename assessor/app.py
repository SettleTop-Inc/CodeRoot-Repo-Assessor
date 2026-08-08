"""The HTTP surface. Thin: parse, authenticate, delegate to handlers, map typed
errors to status codes. All judgment lives in handlers.py."""
from __future__ import annotations

import secrets

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings, get_settings
from .errors import InvalidSubdir, NotDerivable, RepoGone
from .handlers import acquire_handler, assess_handler
from .ports.cache import CachePort
from .ports.source import Source
from .versions import version_payload
from .wiring import build_acquire_source, build_assess_source, build_cache


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


def build_app(settings: Settings, source: Source, cache: CachePort, *,
              acquire_source: Source) -> FastAPI:
    """`source` serves /v1/assess; `acquire_source` serves /v1/acquire.

    Two arguments, not one, and `acquire_source` is keyword-ONLY and has no
    default: on the deployment shape production runs (compose sets both
    GITHUB_TOKENS and CODEROOT_MCP_URL on one assessor service) a single
    source made /v1/acquire an unconditional 500, because the MCP-configured
    selection handed the acquire route an `McpSource` whose `acquire` raises
    NotImplementedError by design. Requiring the caller to name the acquire
    source means a call site that collapses the two back together has to do so
    visibly, rather than by omitting an argument. See wiring.py's docstring."""
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
        #
        # `acquire` reports the same class of silent degradation for the OTHER
        # credential this service holds. With no GITHUB_TOKENS configured,
        # /v1/acquire still works — HttpClient sends no Authorization header
        # rather than raising — but at GitHub's anonymous 60 req/hr instead of
        # 5000, which surfaces downstream only as acquire units mysteriously
        # exhausting their attempts. Refusing the request outright would be
        # worse (anonymous acquisition of public repos is a legitimate
        # standalone configuration), so report the state instead of changing
        # it. Reports the CONFIGURATION, not reachability: unlike the model
        # gateway there is nothing to probe that would not itself spend quota.
        acquire = "authenticated" if settings.github_token_list else "anonymous"
        if settings.llm_provider == "none" or not settings.llm_base_url:
            return {"status": "ok", "llm": "off", "acquire": acquire}
        try:
            r = httpx.get(settings.llm_base_url.rstrip("/") + "/models", timeout=5)
            return {"status": "ok", "llm": "up" if r.status_code < 500 else "down",
                    "acquire": acquire}
        except Exception:
            return {"status": "ok", "llm": "unreachable", "acquire": acquire}

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
            # `acquire_source`, never `source`: this route always contacts
            # GitHub (spec §5.1), including on an MCP-configured deployment
            # where `source` is an McpSource that cannot acquire at all.
            return acquire_handler(acquire_source, body.repo_url,
                                   body.prior.model_dump() if body.prior else None)
        except RepoGone:
            return JSONResponse(status_code=410, content={"error": "repo_gone"})
        except ValueError as exc:
            return JSONResponse(status_code=400,
                                content={"error": "invalid_repo_url", "reason": str(exc)})

    @app.post("/v1/assess", dependencies=[Depends(auth)])
    def assess(body: AssessIn):
        # commit_sha pinning is not implemented: ports/source.py's snapshot()
        # always calls self.acquire(subject["repo_url"], prior=None), and
        # acquire() always resolves current HEAD via resolve_head — there is
        # no code path that pins a caller-supplied sha. Silently ignoring a
        # caller's `commit_sha` would derive a confidently-labelled assessment
        # of a DIFFERENT commit than the one they asked for, so refuse
        # explicitly rather than quietly substituting HEAD, matching the `ref`
        # and `source` guards below. The field stays on the model so the
        # request contract shape is stable for when pinning is implemented;
        # an empty string (what every current caller sends) stays valid.
        if body.subject.commit_sha:
            return JSONResponse(status_code=400,
                                content={"error": "unsupported_field", "field": "commit_sha",
                                         "reason": "commit_sha pinning is not supported "
                                                   "by this deployment"})
        # "direct" always works. "mcp" only works when this deployment is
        # actually wired to CodeRoot-MCP (wiring.build_assess_source picks McpSource
        # over DirectSource from settings.coderoot_mcp_url) — silently
        # falling back to DirectSource for an unconfigured deployment would
        # perform a live GitHub acquisition instead of the zero-cost
        # re-derivation the caller asked for, so an unconfigured deployment
        # keeps refusing explicitly rather than quietly substituting direct.
        # Anything else is refused outright: the field stays on the model so
        # the request contract shape is stable as new sources land.
        if body.source not in ("direct", "mcp"):
            return JSONResponse(status_code=400,
                                content={"error": "unsupported_field", "field": "source",
                                         "reason": f"source {body.source!r} is not "
                                                   "supported by this deployment"})
        if body.source == "mcp" and not settings.coderoot_mcp_url:
            return JSONResponse(status_code=400,
                                content={"error": "unsupported_field", "field": "source",
                                         "reason": "source 'mcp' requires an MCP URL "
                                                   "to be configured on this deployment"})
        try:
            return assess_handler(source, cache, settings, body.subject.model_dump())
        except InvalidSubdir as exc:
            # 400, NOT 422, and the distinction is load-bearing across the
            # network boundary. CodeRoot's `assessor_client` maps 422 to
            # `NotDerivable`, whose documented action is "re-arm acquire and
            # skip" — for a subdir that is structurally invalid that would
            # re-acquire and re-fail forever, because no amount of acquiring
            # can make `../../other-repo` a valid subtree. A 4xx that is
            # neither 410 nor 422 falls through to that client's
            # `raise_for_status()` and surfaces loudly as the data fault it is.
            # 400 also matches how this surface already answers every other
            # malformed-input case (`unsupported_field`, `invalid_repo_url`);
            # 422 here is reserved for `not_derivable`, a condition about the
            # snapshot rather than about the request.
            return JSONResponse(status_code=400,
                                content={"error": "invalid_subdir", "reason": str(exc)})
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
    return build_app(s, build_assess_source(s), build_cache(s),
                     acquire_source=build_acquire_source(s))
