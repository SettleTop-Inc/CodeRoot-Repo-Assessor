"""prompt composition (spec §7.2-§7.4): path-only inventory of a prompt collection
— no bodies to parse (prompt text carries no derivable side-effects), so this
module works entirely off the repo-wide `paths` inventory. Mirrors compose_skill's
Fact/cap/honesty discipline. No LLM calls."""
from __future__ import annotations

from .classify_prompt import prompt_paths
from .risk import _signals
from .shapes import fact

_INV_CAP = 1000


def _prompt_name(path: str) -> str:
    """The pattern/prompt directory name for a matched path — the immediate parent
    directory's basename (Fabric-pattern style: `data/patterns/<name>/system.md`
    -> `<name>`), or the basename with a recognized prompt extension stripped when
    the file has no parent directory of its own."""
    if "/" in path:
        return path.rsplit("/", 1)[0].rsplit("/", 1)[-1]
    base = path
    for ext in (".prompt.md", ".prompt", ".prompty"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def compose(content: dict[str, str], *, capped: bool = False,
            paths=(), meta=None) -> dict:
    matched = prompt_paths(paths)
    kinds = {k for _, k in matched}
    if kinds == {"system_md"}:
        fmt = "system/user-pair"
    elif kinds == {"other"}:
        fmt = "prompt-file"
    elif kinds:
        fmt = "mixed"
    else:
        fmt = None

    full_names = sorted({_prompt_name(p) for p, _ in matched})
    inv_capped = len(full_names) > _INV_CAP
    names = full_names[:_INV_CAP]

    if capped:
        complete, reason = False, "source coverage capped/truncated"
    elif inv_capped:
        complete, reason = False, f"inventory capped at {_INV_CAP}"
    else:
        complete, reason = True, None

    return {
        "prompts_count": fact(len(matched), "path inventory",
                              [{"path": "prompts", "marker": f"{len(matched)} prompt files"}]),
        "prompt_names": names,
        "prompts_complete": complete,
        "prompts_incomplete_reason": reason,
        "format": fact(fmt, "prompt path shape"),
    }


def fingerprint_facts(match: dict, composition: dict) -> dict:
    return {"marker_tier": match["marker_tier"],
            "prompts_count": composition["prompts_count"]["value"],
            "prompt_names": sorted(composition["prompt_names"])}


def risk_signals(composition: dict) -> dict:
    """Prompts are inert text — no tool wiring, no derivable side-effects. All
    shared flags are fact(False) when the inventory is complete, else unknown()."""
    complete = bool(composition.get("prompts_complete"))
    basis = composition.get("prompts_incomplete_reason") or "prompt inventory incomplete"
    return _signals("", complete, basis, secrets_hit=False)
