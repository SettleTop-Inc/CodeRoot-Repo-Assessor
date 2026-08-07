"""Deterministic skill classification (spec §6.1): the Anthropic Skills format —
a SKILL.md file (repo root or skills/<name>/SKILL.md) with YAML frontmatter
declaring `name:` + `description:`. File markers ONLY — no LLM, no meta (meta is
accepted and ignored)."""
from __future__ import annotations

import re

NAME = "skill"

_MAX_EVIDENCE_PATHS = 3

# The Anthropic Skills format is POSITIONAL: a published skill lives at the repo root
# (`SKILL.md`) or at `skills/<name>/SKILL.md` — exactly what this module's docstring has
# always claimed. Matching the basename at ANY depth instead made every agent-HOST repo a
# "skill" at strong tier: `.codex/skills/` (openai/codex, 13 files), `.cline/skills/`,
# `.agents/skills/`, `.claude/skills/` (electron/electron -> primary_type 'skill'),
# `.qwen/skills/`, `internal/skills/builtin/` (crush), and a lone
# `evals/harbor/.agents/skills/compare_tasks/SKILL.md` fixture that classified goose.
#
# Those directories are AGENT CONFIGURATION — evidence the repo HOSTS agents and skills,
# not that it authors one. A positive positional rule fixes every observed case without a
# per-directory blocklist that would need a new entry for each new agent vendor.
#
# Deliberate consequence: skills nested under a package/plugin subtree (awslabs/mcp's
# `src/<pkg>/skills/`, stripe/agent-toolkit's `providers/claude/plugin/skills/`) no longer
# register. Sub-directory asset granularity is deferred; those repos classify on their own
# root markers, and none of them holds `skill` as `primary_type`.
_AUTHORED_SKILL_RE = re.compile(r"^(?:SKILL\.md|skills/[^/]+/SKILL\.md)$")


def _skill_md_paths(content: dict, paths) -> list[str]:
    """Authored-skill SKILL.md paths, from the repo-wide inventory unioned with whatever
    bodies acquire fetched — root or `skills/<name>/SKILL.md` only, sorted for stable
    evidence/cap order. `compose_skill` imports this, so `skills_count`/`skills_complete`
    inherit the same rule."""
    found = set(paths) | set(content.keys())
    return sorted(p for p in found if _AUTHORED_SKILL_RE.match(p))


def frontmatter_block(body: str) -> list[str] | None:
    """Return the lines strictly between a leading '---' line and its closing
    '---', or None if the body has no such block (minimal hand-parse — no YAML dep)."""
    lines = body.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:i]
    return None


def _has_frontmatter(body: str) -> bool:
    block = frontmatter_block(body)
    if block is None:
        return False
    has_name = any(line.strip().lower().startswith("name:") for line in block)
    has_desc = any(line.strip().lower().startswith("description:") for line in block)
    return has_name and has_desc


def classify(content: dict[str, str], *, paths=(), meta=None):
    skill_paths = _skill_md_paths(content, paths)
    if not skill_paths:
        return None
    confirmed, any_body = [], False
    for p in skill_paths:
        body = content.get(p)
        if body is None:
            continue
        any_body = True
        if _has_frontmatter(body):
            confirmed.append(p)
    if not confirmed and any_body:
        return None    # bodies were acquired but none pass the frontmatter shape — not a skill
    # else: either >=1 confirmed, OR paths are known but no bodies were acquired yet
    # (pre-widening snapshot) — composition flags the incompleteness, classify stays strong.
    ev = [{"path": p, "marker": "SKILL.md"} for p in skill_paths[:_MAX_EVIDENCE_PATHS]]
    ev.append({"path": "skills", "marker": f"{len(skill_paths)} SKILL.md files"})
    return {"asset_type": NAME, "marker_tier": "strong", "evidence": ev}
