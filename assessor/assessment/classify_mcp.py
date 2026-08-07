"""Deterministic mcp_server classification (Fact) — file/manifest markers only."""
from __future__ import annotations

import json
import re

NAME = "mcp_server"

_STRONG = 0.95
_MEDIUM = 0.6

_MCP_TOPICS = {"mcp", "mcp-server", "mcp-servers", "modelcontextprotocol", "model-context-protocol"}
_TEACHING_DIRS = (
    "examples/", "example/", "skills/", "references/", "reference/",
    "docs/", "doc/", "templates/", "template/", "fixtures/", "samples/",
    "sample/", "tutorials/", "tutorial/",
)
_MCP_DESC_RE = re.compile(r"\bmcp servers?\b|\bmodel context protocol\b", re.IGNORECASE)


def _pkg(content):
    """Parsed package.json, coerced to a dict (a top-level array/scalar → {})."""
    try:
        v = json.loads(content.get("package.json", "") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _is_teaching_path(path: str) -> bool:
    """True if `path` lives under a teaching/example directory (not a shipped source path)."""
    low = path.lower()
    return any(
        low == d.rstrip("/") or low.startswith(d) or ("/" + d) in low
        for d in _TEACHING_DIRS
    )


def _declares_mcp(meta) -> tuple[bool, str | None]:
    """Whether the maintainer's declared identity (topics/description) confirms MCP.

    Returns (matched, which) where `which` is "topics" or "description" (topics preferred).
    """
    if not isinstance(meta, dict):
        return False, None
    topics = meta.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    if any(str(t).lower() in _MCP_TOPICS for t in topics):
        return True, "topics"
    description = meta.get("description") or ""
    if _MCP_DESC_RE.search(description):
        return True, "description"
    return False, None


def classify(content: dict[str, str], *, paths=(), meta=None) -> dict:
    ev: list[dict] = []
    pkg = _pkg(content)
    deps: dict = {}
    for key in ("dependencies", "devDependencies"):
        d = pkg.get(key)
        if isinstance(d, dict):
            deps.update(d)
    strong = False
    if "@modelcontextprotocol/sdk" in deps:
        ev.append({"path": "package.json", "marker": "dep @modelcontextprotocol/sdk"})
        strong = True
    pyproject = content.get("pyproject.toml", "")
    if "modelcontextprotocol" in pyproject or "\nmcp" in pyproject or '"mcp"' in pyproject:
        ev.append({"path": "pyproject.toml", "marker": "python mcp package"})
        strong = True
    for m in ("mcp.json", "server.json"):
        if m in content:
            ev.append({"path": m, "marker": "MCP manifest present"})
            strong = True
    if "startCommand" in content.get("smithery.yaml", ""):
        ev.append({"path": "smithery.yaml", "marker": "smithery startCommand"})
        strong = True

    weak = False
    blob = (content.get("README.md", "") + content.get("README.rst", "")).lower()
    if "model-context-protocol" in blob or "mcp server" in blob or "an mcp" in blob:
        ev.append({"path": "README.md", "marker": "mcp keyword"})
        weak = True
    for path, text in content.items():
        if _is_teaching_path(path):
            continue
        if path.endswith((".ts", ".js", ".py")) and (
                "new McpServer(" in text or "FastMCP(" in text or "new Server(" in text):
            ev.append({"path": path, "marker": "server construction"})
            weak = True
            break

    if not strong and weak:
        declared, which = _declares_mcp(meta)
        if declared:
            ev.append({"path": which, "marker": "declared MCP identity"})
            strong = True

    if strong:
        return {"asset_type": "mcp_server", "marker_tier": "strong", "evidence": ev}
    if weak:
        return {"asset_type": "mcp_server", "marker_tier": "weak", "evidence": ev}
    return None
