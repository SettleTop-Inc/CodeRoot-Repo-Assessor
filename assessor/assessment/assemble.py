"""Assemble the draft assessment from the extractors (pure — no DB, no I/O)."""
from __future__ import annotations

from . import content as content_mod
from . import coords, license as license_mod, probes, purpose, subject, versions
from .shapes import fact, unknown
from .fingerprint import build_payload, compute_fingerprint
from .registry import (TYPE_MODULES, TIER_CONF, REGISTRY_VERSION, FULL_TAXONOMY,
                       _pick_primary, _merge_risk, _apply_repo_shape_suppressor,
                       _apply_declared_identity_suppressor, repo_is_scaffold,
                       promote_from_probes)


def _parse_repo_url(repo_url: str) -> tuple[str, str, str]:
    """Split `https://{host}/{owner}/{name}` into (host, owner, name)."""
    rest = repo_url.split("://", 1)[-1]
    parts = rest.split("/")
    host = parts[0] if len(parts) > 0 else ""
    owner = parts[1] if len(parts) > 1 else ""
    name = parts[2] if len(parts) > 2 else ""
    return host, owner, name


def _owned_known_unknowns(compositions, lic, purpose_fields) -> list[dict]:
    out, seen = [], set()

    def add(owner, code, detail):
        if detail and (owner, detail) not in seen:
            seen.add((owner, detail))
            out.append({"asset_type": owner, "code": code, "detail": detail})

    for tname, comp in compositions.items():
        for field in comp.values():
            if isinstance(field, dict) and field.get("known_unknown"):
                add(tname, "undeclared", field["known_unknown"])
        for key, val in comp.items():
            if key.endswith("_complete") and val is False:
                add(tname, "incomplete", comp.get(key[:-len("_complete")] + "_incomplete_reason")
                    or f"{key[:-len('_complete')]} inventory not fully determinable")
    for part in ({"license": lic["spdx"]}, purpose_fields):
        for field in part.values():
            if isinstance(field, dict) and field.get("known_unknown"):
                add("global", "undeclared", field["known_unknown"])
    return out


def build(repo_url: str, content: dict[str, str], commit_sha: str | None,
          fallback_license: str | None, *, releases: list[dict] | None = None,
          bucket_b: dict | None = None, source_coverage_capped: bool = False, cache=None,
          settings=None, paths: tuple = (), hits: tuple = (), subdir: str = "",
          subject_key: str | None = None) -> dict:
    if subdir:
        content = subject.filter_content_to_subdir(content, subdir)
        paths = subject.filter_paths_to_subdir(paths, subdir)
    b = bucket_b or {}
    meta = {"description": b.get("description"),
            "topics": [t.lower() for t in (b.get("topics") or []) if isinstance(t, str)],
            "homepage": b.get("homepage")}
    # Declared identity (topics + description) is the maintainer's claim about the WHOLE
    # REPO — it says nothing about one subtree. Feeding it to classify/suppress for a
    # subdir assessment attributes the repo's declaration to the subdir: every subdir of
    # `modelcontextprotocol/servers` that merely mentions "mcp" in prose was promoted
    # weak→strong (0.6→0.95) by the repo's topics, so a `docs/` subtree read as a
    # confidently-classified MCP server. That contradicts the path-scoping honesty rule
    # below, and is the same leak the coverage-probe gate closes further down. The
    # topics/description are still SERVED on a subdir record (carrying explicit
    # repo-level provenance) — they just don't get to classify the subtree.
    identity_meta = ({**meta, "description": None, "topics": []} if subdir else meta)
    mods = {m.name: m for m in TYPE_MODULES}
    matches = [dict(r) for M in TYPE_MODULES
               if (r := M.classify(content, paths=paths, meta=identity_meta)) is not None]
    for m in matches:
        m["confidence"] = TIER_CONF[m["marker_tier"]]          # R6: spine stamps confidence
    matches, suppressed = _apply_repo_shape_suppressor(matches, repo_url, content)
    matches, id_suppressed = _apply_declared_identity_suppressor(matches, identity_meta)
    suppressed = suppressed + id_suppressed
    # PAYLOAD SPLIT (Task 7). `det_matches`/`det_asset_types` are the DETERMINISTIC
    # classification — the only thing `build_payload` ever hashes. A citation-backed
    # coverage-probe promotion is applied further down, to `matches`/`asset_types`
    # only, so `content_fingerprint` is byte-identical with and without a promotion
    # (hence no REGISTRY_VERSION bump and no marketplace-wide fingerprint churn).
    det_matches = list(matches)     # a COPY, so the split is structural: even if the
                                    # promotion below started mutating `matches` in place
                                    # instead of rebinding it, the hashed list would not move.
    is_asset = bool(matches)
    det_asset_types = sorted(m["asset_type"] for m in det_matches)
    compositions = {m["asset_type"]: mods[m["asset_type"]].compose(
        content, capped=source_coverage_capped, paths=paths, meta=identity_meta)
        for m in matches}

    if matches:
        primary, tiebroken, tie_set = _pick_primary(matches)
        primary_type, confidence = primary["asset_type"], primary["confidence"]
    else:
        primary, tiebroken, tie_set = None, False, []
        primary_type, confidence = "not_an_asset", 0.0

    lic = license_mod.detect(content, fallback_license, repo_spdx=b.get("license_spdx"))
    coordinates = coords.extract(content, repo_url)
    purpose_fields = purpose.extract(content, cache=cache, settings=settings, repo_description=b.get("description"))
    risk_flags = _merge_risk([(t, mods[t].risk_signals(compositions[t])) for t in det_asset_types])

    spdx = lic["spdx"].get("value")
    checked = sorted(m.name for m in TYPE_MODULES)
    per_type = {m["asset_type"]: mods[m["asset_type"]].fingerprint_facts(
        m, compositions[m["asset_type"]]) for m in det_matches}
    fingerprint = compute_fingerprint(build_payload(
        asset_types=det_asset_types, types_checked=checked, registry_version=REGISTRY_VERSION,
        per_type=per_type, coordinates=coordinates, spdx=spdx))

    known = _owned_known_unknowns(compositions, lic, purpose_fields)
    if source_coverage_capped and not any(
            p.rsplit("/", 1)[-1] in ("agents.yaml", "tasks.yaml", "langgraph.json",
                                     "agent.json", "agent-card.json") for p in content):
        known.append({"asset_type": "global", "code": "incomplete",
                      "detail": "agent manifests may be missing — source coverage capped/truncated"})
    # Dependency-manifest incompleteness is its OWN known-unknown, not
    # `source_coverage_capped` (content.py's dep pre-pass): the dep pass runs last, is
    # budget-isolated, and its input is advisory, so folding it into the shared signal
    # would assert two false things — that the source scan was truncated (mcp
    # `tools_complete=False`) and that agent manifests may be missing.
    #
    # HONESTY: state the COUNT, not a CAUSE. `paths` is the full ls-tree inventory; the
    # partial clone (`git_fetch`, --filter=blob:limit=1MiB) drops oversized blobs before
    # they ever become candidates, so a manifest counted in `dep_seen` but missing from
    # `dep_got` may have been size-filtered rather than cap-dropped. We have not
    # established which, so we don't name one.
    #
    # `paths` is the tree inventory (`repo_acquisition.tree_paths`, threaded through
    # `service.build_record`). It can be NULL/empty — pre-inventory snapshots, or any
    # acquire path that stored no ls-tree — and then it is not a zero-manifest repo, it
    # is a MISSING DENOMINATOR: `dep_seen` would be 0, `dep_seen > dep_got` could never
    # fire, and the shortfall would go SILENT exactly when coverage is least verifiable
    # (every dep manifest could have been dropped and we would say nothing). A
    # known-unknown must degrade loudly, so state that the coverage is UNDETERMINED —
    # which is all we actually know — instead of implying completeness by omission.
    dep_seen = len(content_mod.dep_manifest_paths(paths))
    dep_got = len(content_mod.dep_manifest_paths(content))
    if content and not paths:
        known.append({"asset_type": "global", "code": "incomplete",
                      "detail": "dependency-manifest coverage undetermined — no repo "
                                "path inventory recorded for this snapshot, so whether "
                                "any dependency manifest was missed cannot be "
                                "determined; the dep-based agent-candidate signal may "
                                "be incomplete"})
    elif dep_seen > dep_got:
        known.append({"asset_type": "global", "code": "incomplete",
                      "detail": f"dependency manifests incomplete ({dep_got} of "
                                f"{dep_seen} acquired) — the dep-based agent-candidate "
                                f"signal may be incomplete"})
    if subdir and not matches:
        # Path-scoping honesty: a subdir with no path-local marker is indeterminate,
        # never attribute whole-repo signals to the subdir (Global Constraints).
        known.append({"asset_type": "global", "code": "incomplete",
                      "detail": "subdir_composition_indeterminate"})

    # DP3 (spec §6): additive coverage-probe pass, AFTER matches/compositions are
    # finalized (post shape + declared-identity suppressors) so it reads the final
    # kept set. `name` is a probe-only trigger — it is NOT added to `meta` and never
    # reaches classify/compose/build_payload (see probes.py's module invariant).
    #
    # WHOLE-REPO ONLY: probes read the whole-repo declared identity (repo name +
    # topics + description), which has no meaning for a subdir subtree. Running them
    # for a subdir would falsely attribute the repo's declaration to the subtree
    # (e.g. a `docs/` subdir of `sentry-mcp` → "declared mcp_server but no tool
    # registrations found") and fire a wasted advisory LLM call — contradicting the
    # path-scoping honesty rule above. So a subdir assessment carries no probes.
    name = repo_url.rstrip("/").rsplit("/", 1)[-1].lower()
    shape_suppressed = repo_is_scaffold(repo_url, content)
    coverage_probes = [] if subdir else probes.detect(
        matches, compositions, meta, name, content,
        capped=source_coverage_capped, shape_suppressed=shape_suppressed,
        paths=paths, hits=hits, cache=cache, settings=settings)

    # Task 7: promote a citation-backed candidate INTO classification. Deliberately the
    # LAST step, after everything the fingerprint hashes has already been computed from
    # `det_matches`/`det_asset_types` above — that ordering IS invariant 4 (identical
    # `content_fingerprint` with and without a promotion), not a coincidence to be
    # refactored away. `registry.promote_from_probes` owns the gates (candidate-only,
    # a citation that RESOLVES against what we acquired, known type, never a type
    # without a probe, never a suppressed type) and stamps `PROMOTED_TIER` = weak.
    # `_pick_primary` ranks every deterministic match above every promoted one, so
    # re-ranking below can only give a promotion the primary slot when there is no
    # deterministic match at all.
    #
    # `known_paths` is content ∪ paths — both already subdir-filtered at the top of
    # this function, so a citation is checked against the SUBJECT's files, not the
    # whole repo's. (Subdir assessments carry no probes at all, so this is belt and
    # braces rather than a live path today.)
    #
    # No composition is synthesized for a promoted type, on purpose: composition is a
    # deterministic inventory of what the code was observed to contain, and the reason
    # this type was a candidate rather than a classification is precisely that no
    # deterministic marker was found. Running a compose module that already found
    # nothing would manufacture an empty inventory that reads as "we looked and it is
    # empty". The honest form is the named gap appended to `known_unknowns` instead —
    # and it keeps `risk` (computed from the deterministic compositions) unmoved too.
    promoted, promoted_types, refusals = promote_from_probes(
        matches, coverage_probes, suppressed, known_paths=set(content) | set(paths or ()))
    # A refused promotion is a NAMED gap, not silence: "no candidates existed" and
    # "candidates existed but every citation was rejected" otherwise look identical
    # (`promoted_types == []`, nothing logged), and a loosely-citing local model makes
    # the second the likely first-sweep outcome. Deduped on detail — `known` is served.
    for refusal in refusals:
        if not any(k["detail"] == refusal["detail"] for k in known):
            known.append({"asset_type": refusal["asset_type"], "code": "incomplete",
                          "detail": refusal["detail"]})
    if promoted:
        matches = matches + promoted
        is_asset = True
        primary, tiebroken, tie_set = _pick_primary(matches)
        primary_type, confidence = primary["asset_type"], primary["confidence"]
        for entry in promoted_types:
            known.append({
                "asset_type": entry["asset_type"], "code": "incomplete",
                "detail": (f"{entry['asset_type']} promoted from a coverage-probe candidate "
                           f"on an LLM code citation ({entry['citation']}) — no deterministic "
                           f"marker matched, so its composition inventory was not derived")})
    asset_types = sorted(m["asset_type"] for m in matches)

    classification = {
        "matches": matches, "suppressed": suppressed,
        "primary_tiebroken": tiebroken, "tie_set": tie_set,
        "types_checked": checked, "registry_version": REGISTRY_VERSION,
        "taxonomy_uncovered": sorted(set(FULL_TAXONOMY) - set(checked)),
        # back-compat reads (router serves classification as an opaque dict):
        "is_asset": is_asset, "asset_type": primary_type, "confidence": confidence,
        "evidence": (primary or {}).get("evidence", []),
    }
    topics = (fact(sorted(meta["topics"]), "repo object (github topics)",
                   [{"path": "topics", "marker": "github repo topics"}])
              if meta["topics"] else unknown("no repo topics declared"))
    assessment = {
        "classification": classification, "purpose": purpose_fields,
        "compositions": compositions,
        "composition": compositions.get(primary_type),         # legacy = primary's (§9)
        "license": {"spdx": lic["spdx"], "caveat": lic["caveat"]},
        "coordinates": coordinates, "versions": versions.build(releases, commit_sha),
        "risk": risk_flags, "known_unknowns": known, "topics": topics,
        "coverage_probes": coverage_probes,
        # Audit trail for invariant-gated promotions (Task 7): which type was promoted,
        # from which probe state, on which LLM citation, at which confidence. Empty for
        # every repo that had no citation-backed candidate — i.e. almost all of them.
        "promoted_types": promoted_types,
    }
    host, owner, name = _parse_repo_url(repo_url)
    assessment["subdir"] = subdir
    assessment["asset_id"] = subject.asset_id(subject_key or repo_url, subdir)
    assessment["source_url"] = subject.scoped_source_url(host, owner, name, commit_sha or "", subdir)
    # Provenance: was any served field LLM-derived? A promotion is the STRONGEST case —
    # the record's `asset_types` exists only because of an LLM citation — so it must set
    # this too, not just the purpose extractor.
    llm_used = ("confidence" in purpose_fields.get("business_domain", {})
                or bool(promoted_types))
    return {"is_asset": is_asset, "asset_type": primary_type, "asset_types": asset_types,
            "classification_confidence": confidence, "content_fingerprint": fingerprint,
            "llm_used": llm_used, "assessment": assessment}
