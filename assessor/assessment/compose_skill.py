"""skill composition (spec §6.2): per-skill frontmatter facts + allowed-tools risk
surface. Hand-parsed frontmatter (no YAML dependency) — mirrors compose_agent
discipline: Fact envelopes, honesty flags, caps. No LLM calls."""
from __future__ import annotations

import re

from .classify_skill import _skill_md_paths, frontmatter_block
from .risk import _signals
from .shapes import fact

_INV_CAP = 1000
_NAME_RE = re.compile(r"^name:\s*(.*)$", re.I)
_DESC_RE = re.compile(r"^description:\s*(.*)$", re.I)
_ALLOWED_TOOLS_RE = re.compile(r"^allowed-tools:\s*(.*)$", re.I)
_LIST_ITEM_RE = re.compile(r"^-\s*(.+?)\s*$")


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    return s


def _parse_allowed_tools(block: list[str]) -> list[str] | None:
    """`allowed-tools:` may be an inline comma list (optionally bracketed) or a
    YAML block list (`- name` on following indented lines). Absent key -> None
    (undeclared, distinct from an explicit empty list)."""
    for i, line in enumerate(block):
        m = _ALLOWED_TOOLS_RE.match(line.strip())
        if not m:
            continue
        rest = m.group(1).strip()
        if rest:
            rest = rest.strip("[]")
            return [_unquote(x) for x in rest.split(",") if x.strip()]
        items: list[str] = []
        for nxt in block[i + 1:]:
            lm = _LIST_ITEM_RE.match(nxt.strip())
            if not lm:
                break
            items.append(_unquote(lm.group(1)))
        return items
    return None


def _parse_skill(path: str, body: str) -> dict | None:
    """Parse ONE confirmed SKILL.md (leading/closing '---', name: + description:
    both present) into its composition entry. Returns None for an unconfirmed
    body — those are excluded from the `skills` list entirely."""
    block = frontmatter_block(body)
    if block is None:
        return None
    name = None
    has_name = has_desc = False
    desc_present = False
    for line in block:
        s = line.strip()
        m = _NAME_RE.match(s)
        if m and not has_name:
            has_name = True
            name = _unquote(m.group(1))
            continue
        m = _DESC_RE.match(s)
        if m:
            has_desc = True
            desc_present = bool(m.group(1).strip())
    if not (has_name and has_desc):
        return None
    return {"name": name, "description_present": desc_present,
            "allowed_tools": _parse_allowed_tools(block), "path": path}


def compose(content: dict[str, str], *, capped: bool = False,
            paths=(), meta=None) -> dict:
    skill_paths = _skill_md_paths(content, paths)
    bodies_missing = any(content.get(p) is None for p in skill_paths)
    skills: list[dict] = []
    for p in skill_paths:
        body = content.get(p)
        if body is None:
            continue
        parsed = _parse_skill(p, body)
        if parsed is not None:
            skills.append(parsed)
    # sort the FULL candidate set before truncating (mirrors compose_agent's
    # tools cap discipline — which entries survive must not depend on dict order)
    skills.sort(key=lambda s: s["path"])
    inv_capped = len(skills) > _INV_CAP
    skills = skills[:_INV_CAP]

    allowed_declared = any(s["allowed_tools"] for s in skills)
    if bodies_missing:
        complete, reason = False, "SKILL.md bodies not acquired"
    elif capped:
        complete, reason = False, "source coverage capped/truncated"
    elif inv_capped:
        complete, reason = False, f"inventory capped at {_INV_CAP}"
    else:
        complete, reason = True, None

    return {
        "skills": skills,
        "skills_count": fact(len(skills), "SKILL.md frontmatter",
                              [{"path": "skills", "marker": f"{len(skills)} skills"}]),
        "allowed_tools_declared": fact(allowed_declared, "SKILL.md frontmatter allowed-tools"),
        "skills_complete": complete,
        "skills_incomplete_reason": reason,
    }


def fingerprint_facts(match: dict, composition: dict) -> dict:
    """Descriptions are excluded from the fingerprint on purpose (§6.2) — only
    the count and the (sorted) confirmed skill names."""
    return {"marker_tier": match["marker_tier"],
            "skills_count": composition["skills_count"]["value"],
            "skill_names": sorted(s["name"] for s in composition["skills"])}


def risk_signals(composition: dict) -> dict:
    names = set()
    for s in composition.get("skills", []):
        for t in (s.get("allowed_tools") or []):
            names.add(t.lower())
    names_str = " ".join(sorted(names))
    # R2: an undeclared allow-list is NEVER complete, even if skills_complete is
    # True — no allowed-tools means the risk surface is genuinely undeterminable,
    # not "clean". This keeps the flags unknown() rather than fact(False).
    complete = bool(composition.get("skills_complete")) and \
        bool(composition.get("allowed_tools_declared", {}).get("value"))
    basis = composition.get("skills_incomplete_reason") or "allowed-tools not declared"
    secrets_hit = any(s in names_str for s in ("token", "key", "secret"))
    return _signals(names_str, complete, basis, secrets_hit)
