"""Acquisition coordinates (Fact) — the real install refs. Absent → omitted, never guessed."""
from __future__ import annotations

import json
import re

_PY_NAME = re.compile(r'^\s*name\s*=\s*["\']([^"\']+)["\']', re.M)
_IMAGE = re.compile(r'image:\s*([^\s"\']+)', re.I)


def _pkg(content):
    try:
        v = json.loads(content.get("package.json", "") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def extract(content: dict[str, str], repo_url: str) -> list[dict]:
    coords: list[dict] = []

    pkg = _pkg(content)
    if pkg.get("name"):
        coords.append({"kind": "npm", "ref": f"npx {pkg['name']}",
                       "evidence": {"path": "package.json", "marker": "name"}})

    m = _PY_NAME.search(content.get("pyproject.toml", ""))
    if m:
        coords.append({"kind": "pip", "ref": f"pip install {m.group(1)}",
                       "evidence": {"path": "pyproject.toml", "marker": "[project].name"}})

    if "Dockerfile" in content:
        img = _IMAGE.search(content.get("smithery.yaml", "")) or _IMAGE.search(content.get("README.md", ""))
        ref = f"docker run {img.group(1)}" if img else "docker build ."
        coords.append({"kind": "docker", "ref": ref, "evidence": {"path": "Dockerfile", "marker": "present"}})

    endpoint_found = False
    for manifest in ("smithery.yaml", "mcp.json", "server.json"):
        for url in re.findall(r'https?://[^\s"\']+', content.get(manifest, "")):
            if "/mcp" in url or "/sse" in url:  # the transport url, not a docs/repo url
                coords.append({"kind": "endpoint", "ref": url,
                               "evidence": {"path": manifest, "marker": "http transport url"}})
                endpoint_found = True
                break
        if endpoint_found:
            break

    coords.append({"kind": "git", "ref": repo_url, "evidence": {"path": "repo", "marker": "vcs url"}})
    return coords
