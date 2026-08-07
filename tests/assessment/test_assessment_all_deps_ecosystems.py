"""The widened, multi-ecosystem/subdir dep parsing lives in `all_deps_wide`, whose ONLY
consumer is the advisory bespoke-agent candidate probe. The narrow `_all_deps` (root-only,
Python/JS) is what real classification/composition/fingerprint use and must stay exactly
as it was before the widening — the two are pinned apart here on purpose."""
from assessor.assessment.classify_agent import _all_deps, all_deps_wide


def test_parses_go_mod():
    content = {"go.mod": 'module x\n\nrequire (\n\tgithub.com/sashabaranov/go-openai v1.2.3\n)\n'}
    assert "github.com/sashabaranov/go-openai" in all_deps_wide(content)


def test_parses_cargo_toml():
    content = {"Cargo.toml": '[dependencies]\nasync-openai = "0.18"\ntokio = { version = "1" }\n'}
    deps = all_deps_wide(content)
    assert "async-openai" in deps and "tokio" in deps


def test_parses_csproj_package_reference():
    content = {"src/App.csproj": '<Project><ItemGroup>'
                                 '<PackageReference Include="Azure.AI.OpenAI" Version="1.0" />'
                                 '</ItemGroup></Project>'}
    assert "azure.ai.openai" in all_deps_wide(content)


def test_parses_subdir_requirements():
    """gorilla-shaped: the manifest exists only in a subdirectory."""
    assert "openai" in all_deps_wide({"goex/requirements.txt": "openai>=1.0\n"})


def test_root_behavior_unchanged():
    """REGRESSION GUARD: existing root parsing must be byte-identical."""
    content = {"requirements.txt": "openai>=1.0\n# comment\nlitellm==1.0\n"}
    assert _all_deps(content) == {"openai", "litellm"}


# -- the classification/advisory split (adversarial-review IMPORTANT #2) ---------------

_SUBDIR_ONLY = {"packages/toolkit/package.json": '{"dependencies": {"crewai": "^1"}}',
                "goex/requirements.txt": "openai>=1.0\n",
                "crates/agent/Cargo.toml": '[dependencies]\nasync-openai = "0.18"\n'}


def test_narrow_all_deps_ignores_subdir_and_non_py_js_manifests():
    """CLASSIFICATION INVARIANT: a dep hiding in a subdirectory or a non-Python/JS
    manifest must NOT reach `_all_deps`. Its three consumers (classify_agent.classify,
    compose_agent._framework -> fingerprint_facts, compose_mcp._external_delegation)
    are all classification-bearing: a stray `crewai` in one of awslabs/mcp's 63 non-root
    manifests would otherwise be a STRONG `agent` match and seize `primary_type` from
    `mcp_server` via PRECEDENCE."""
    assert _all_deps(_SUBDIR_ONLY) == set()


def test_wide_all_deps_finds_subdir_and_non_py_js_manifests():
    """...while the advisory probe reach (`all_deps_wide`) sees exactly those deps."""
    deps = all_deps_wide(_SUBDIR_ONLY)
    assert {"crewai", "openai", "async-openai"} <= deps


def test_wide_agrees_with_narrow_on_root_manifests():
    """Drift pin: the shared parsers must reproduce the narrow reader's semantics for
    the four root manifests it reads (npm scoped names stay `k.lower()`, PyPI names get
    `_norm`ed) — so the split is a change of REACH only, never of parsing."""
    root = {"package.json": '{"dependencies": {"@openai/agents": "^1", "Express": "^4"},'
                            ' "devDependencies": {"typescript": "^5"}}',
            "requirements.txt": "OpenAI>=1.0\n# c\nlangchain_core==0.1\n",
            "requirements-dev.txt": "pytest\n",
            "pyproject.toml": 'dependencies = ["crew_ai>=0.1"]\n'}
    assert _all_deps(root) == all_deps_wide(root)


def test_go_mod_emits_last_non_version_segment():
    from assessor.assessment.classify_agent import _deps_go_mod
    deps = _deps_go_mod("require (\n\tgithub.com/openai/openai-go/v3 v3.46.0\n"
                        "\tgoogle.golang.org/genai v1.65.0\n"
                        "\tgithub.com/charmbracelet/anthropic-sdk-go v0.0.0\n)\n")
    assert "github.com/openai/openai-go/v3" in deps       # full path preserved
    assert "openai-go" in deps and "genai" in deps and "anthropic-sdk-go" in deps
    assert "v3" not in deps                                 # version suffix is not a name
