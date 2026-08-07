import json

from assessor.assessment.purpose import extract


from conftest import _S


class _Stub:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, system, user, **kw):
        return self._reply


_GOOD = json.dumps({"intended_use": "search the web", "category": "search",
                    "business_domain": "Knowledge & Research", "out_of_scope_use": "not for payments"})


def test_description_is_a_fact_and_llm_fields_assessed():
    content = {"package.json": json.dumps({"description": "an MCP web search server"}),
               "README.md": "# thing"}
    r = extract(content, provider=_Stub(_GOOD), settings=_S)
    assert r["description"]["value"] == "an MCP web search server"
    assert r["description"]["source"].startswith("repo-declared")
    assert r["business_domain"]["value"] == "Knowledge & Research" and "confidence" in r["business_domain"]


def test_llm_off_yields_known_unknown():
    r = extract({"README.md": "# thing\nan mcp server"}, provider=None, settings=_S)
    assert r["description"]["value"] == "thing"           # deterministic fact still present
    assert r["business_domain"]["value"] is None and r["business_domain"]["known_unknown"]


def test_out_of_enum_domain_rejected_to_known_unknown():
    bad = json.dumps({"intended_use": "x", "category": "y", "business_domain": "Not A Domain", "out_of_scope_use": "z"})
    r = extract({"README.md": "# t"}, provider=_Stub(bad), settings=_S)
    assert r["business_domain"]["known_unknown"]  # enum-constrained → validation fails → gap


def test_description_prefers_repo_object_and_skips_badge():
    content = {"README.md": "[![build](x)](y)\n# Title\nprose"}
    r = extract(content, provider=None, repo_description="clean repo description", settings=_S)
    assert r["description"]["value"] == "clean repo description"
    assert r["description"]["source"].startswith("repo object")


def test_description_readme_fallback_skips_badge_lines():
    content = {"README.md": "[![build](x)](y)\n<!-- comment -->\nThe real one-liner."}
    r = extract(content, provider=None, settings=_S)
    assert r["description"]["value"] == "The real one-liner."      # badge + comment skipped


def test_partial_salvage_keeps_valid_fields_on_bad_enum():
    bad = json.dumps({"intended_use": "run web searches", "category": "search",
                      "business_domain": "Not A Domain", "out_of_scope_use": "no payments"})
    r = extract({"README.md": "# t"}, provider=_Stub(bad), settings=_S)
    assert r["intended_use"]["value"] == "run web searches"                 # salvaged
    assert r["intended_use"]["evidence"][0]["marker"].startswith("partial salvage")
    assert r["business_domain"]["known_unknown"]                            # only the offender is a gap
