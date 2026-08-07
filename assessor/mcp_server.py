"""The MCP surface — the same handlers, reachable from any MCP client.

This is the second surface that makes this repo genuinely multi-type: an agent
that judges repositories, and an MCP server that exposes that judgement."""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .config import Settings
from .errors import NotDerivable, RepoGone
from .handlers import acquire_handler, assess_handler
from .ports.cache import CachePort
from .ports.source import Source
from .versions import version_payload


def build_mcp(settings: Settings, source: Source, cache: CachePort) -> MCPServer:
    mcp = MCPServer(name="coderoot-repo-assessor")

    @mcp.tool()
    def assess_repository(repo_url: str, subject_key: str = "",
                          subdir: str = "") -> dict:
        """Classify a source repository as an agent, MCP server, skill or prompt.
        Returns the asset types found, a confidence, the evidence behind each
        match, a composition inventory, and an explicit list of what could not
        be determined."""
        subject = {"repo_url": repo_url, "subject_key": subject_key or repo_url,
                   "commit_sha": "", "subdir": subdir}
        # Mirrors app.py's /v1/assess exception mapping exactly (same three
        # typed exceptions, same body shape) so a programmatic caller gets the
        # same discriminator regardless of which surface it used. Returned as
        # a structured payload rather than raised: the SDK's tool runner
        # catches any exception escaping a tool and rewrites it into an opaque
        # ToolError string (mcp/server/mcpserver/tools/base.py), which would
        # destroy the very discriminator this mapping exists to preserve.
        try:
            return assess_handler(source, cache, settings, subject)
        except NotDerivable as exc:
            return {"error": "not_derivable", "reason": str(exc)}
        except RepoGone:
            return {"error": "repo_gone"}
        except ValueError as exc:
            return {"error": "invalid_repo_url", "reason": str(exc)}

    @mcp.tool()
    def acquire_repository(repo_url: str) -> dict:
        """Fetch a repository's file snapshot at its current HEAD, with the
        marker scan and path inventory the classifier uses. Returns the pinned
        commit SHA alongside the selected file bodies."""
        # Mirrors app.py's /v1/acquire exception mapping. NotDerivable is not
        # caught here because acquire_handler can never raise it — only
        # assess_handler's post-snapshot no-snapshot check does — matching
        # the HTTP surface, which also only catches RepoGone/ValueError on
        # this route.
        try:
            return acquire_handler(source, repo_url, None)
        except RepoGone:
            return {"error": "repo_gone"}
        except ValueError as exc:
            return {"error": "invalid_repo_url", "reason": str(exc)}

    @mcp.tool()
    def assessor_version() -> dict:
        """Report the classification registry, selection allowlist and marker
        vocabulary versions. A change in any of them means previously derived
        records are stale and should be re-derived."""
        return version_payload()

    return mcp
