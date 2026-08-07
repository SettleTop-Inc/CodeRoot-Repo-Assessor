"""Deterministic prompt-collection classification (spec §7.1) — PRECISION-FIRST.

A `prompt` asset is a repo that *is* a prompt collection, not a tool that merely
*contains* one. The motivating negative example is `danielmiessler/Fabric`: it
ships thousands of `data/patterns/*/system.md` files but is a Go CLI (`go.mod`,
`cmd/`) — it must classify as `not_an_asset`. File/meta markers ONLY — no LLM.
"""
from __future__ import annotations

import json
import re

from .content import _is_source

NAME = "prompt"

_PROMPT_MIN = 5
_MAX_EVIDENCE_PATHS = 3
_PROMPT_EXTS = (".prompt", ".prompt.md", ".prompty")
_APP_MANIFESTS = {"go.mod", "Cargo.toml", "pom.xml", "build.gradle",
                   "build.gradle.kts", "Dockerfile"}
_IDENTITY_RE = re.compile(r"\b(framework|tool|cli|app|application|library|sdk|server|engine|runtime)\b",
                          re.I)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _under_prompts_dir(path: str) -> bool:
    parts = path.split("/")[:-1]   # directory components only, filename excluded
    return "prompts" in parts


def _prompt_kind(path: str) -> str | None:
    """Which positive marker this path matches, or None. `system.md` (Fabric-pattern
    shape) is checked first — a path can also live under `prompts/`, but the
    system/user-pair shape is the more specific signal for `format` purposes."""
    if _basename(path) == "system.md":
        return "system_md"
    if _under_prompts_dir(path) or path.lower().endswith(_PROMPT_EXTS):
        return "other"
    return None


def prompt_paths(paths) -> list[tuple[str, str]]:
    """Sorted (path, kind) pairs for every path matching a prompt-file marker."""
    return sorted((p, k) for p in paths if (k := _prompt_kind(p)) is not None)


def _package_json_has_bin(body: str) -> bool:
    try:
        pkg = json.loads(body or "{}")
    except ValueError:
        return False
    return isinstance(pkg, dict) and bool(pkg.get("bin"))


def _pyproject_has_scripts(body: str) -> bool:
    in_scripts = False
    for line in (body or "").splitlines():
        s = line.strip()
        if s.startswith("["):
            in_scripts = s == "[project.scripts]"
            continue
        if in_scripts and s and "=" in s:
            return True
    return False


def _guard_no_app_manifest(paths, content) -> bool:
    """Guard #1: no build/application manifest — basename `_APP_MANIFESTS`, a
    `package.json` declaring `bin`, or a `pyproject.toml` `[project.scripts]` table."""
    basenames = {_basename(p) for p in paths}
    if basenames & _APP_MANIFESTS:
        return False
    if _package_json_has_bin(content.get("package.json", "")):
        return False
    if _pyproject_has_scripts(content.get("pyproject.toml", "")):
        return False
    return True


def _guard_identity_ok(meta) -> bool:
    """Guard #3: declared identity (meta description + topics) does not claim
    tool/framework. An empty/absent declaration is silent, not a failure."""
    if not meta:
        return True
    desc = meta.get("description") or ""
    topics = [t for t in (meta.get("topics") or []) if isinstance(t, str)]
    text = " ".join([desc, *topics]).strip().lower()
    if not text:
        return True
    return not _IDENTITY_RE.search(text)


def classify(content: dict[str, str], *, paths=(), meta=None):
    matched = prompt_paths(paths)
    count = len(matched)
    if count < _PROMPT_MIN:
        return None

    guard1 = _guard_no_app_manifest(paths, content)
    source_count = sum(1 for p in paths if _is_source(p))
    guard2 = count > source_count          # #2: prompt files dominate the tree
    guard3 = _guard_identity_ok(meta)
    if not (guard1 and guard2 and guard3):
        return None

    ev = [{"path": "prompts", "marker": f"{count} prompt files"}]
    for p, _ in matched[:_MAX_EVIDENCE_PATHS]:
        ev.append({"path": p, "marker": "prompt file"})
    ev.append({"path": "guards",
               "marker": f"no_app_manifest={guard1} prompt_dominant={guard2} identity_ok={guard3}"})
    return {"asset_type": NAME, "marker_tier": "weak", "evidence": ev}
