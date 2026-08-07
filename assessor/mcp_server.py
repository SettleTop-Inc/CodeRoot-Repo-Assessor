"""The MCP surface — the same handlers, reachable from any MCP client.

This is the second surface that makes this repo genuinely multi-type: an agent
that judges repositories, and an MCP server that exposes that judgement."""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .config import Settings
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
        return assess_handler(source, cache, settings, subject)

    @mcp.tool()
    def acquire_repository(repo_url: str) -> dict:
        """Fetch a repository's file snapshot at its current HEAD, with the
        marker scan and path inventory the classifier uses. Returns the pinned
        commit SHA alongside the selected file bodies."""
        return acquire_handler(source, repo_url, None)

    @mcp.tool()
    def assessor_version() -> dict:
        """Report the classification registry, selection allowlist and marker
        vocabulary versions. A change in any of them means previously derived
        records are stale and should be re-derived."""
        return version_payload()

    return mcp
