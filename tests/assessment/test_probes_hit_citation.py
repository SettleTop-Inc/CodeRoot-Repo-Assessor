"""The LLM must be shown the file where the agent loop was detected — AND the entrypoint.

Measured on the deployed corpus (2026-08-06): all seven agent candidates produced an
EMPTY citation, every refusal saying "only shows an entry point (aider/__main__.py)
calling main()". `_bespoke_run_shape_entrypoint` picked its excerpt by matching PATH
NAMES, while `repo_acquisition.marker_hits` already recorded the file where
agent_run_shape actually fired — and those hits were write-only.

Then the PR #127 deploy measured a REGRESSION: sending the hit file INSTEAD of the
entrypoint dropped agent 2 -> 1. agenticSeek had promoted by citing `cli.py:main()`,
its sole hit is `sources/memory.py`, so replacing the excerpt removed the very file it
cited. The two are complementary — the hit shows the loop, the entrypoint the wiring —
so the prompt carries BOTH.
"""
from assessor.assessment import probes

_LOOP = "class Coder:\n    def run(self):\n        while True:\n            self.send(tool_calls)\n"
_STUB = "from .main import main\nmain()\n"


def _hit(path, line_no=3):
    return {"path": path, "marker_class": "agent_run_shape", "line_no": line_no,
            "line": "while True:", "marker": "tool_calls+while True:"}


# -- _hit_excerpt: picking and framing the run-shape file ----------------------------

def test_hit_excerpt_selects_the_run_shape_file():
    out = probes._hit_excerpt({"aider/coders/base_coder.py": _LOOP},
                              [_hit("aider/coders/base_coder.py")])
    assert "aider/coders/base_coder.py" in out, out
    assert "while True:" in out, out


def test_hit_naming_a_file_we_do_not_hold_is_skipped():
    # marker scanning covers a wider tree than the acquired content, so an
    # unexcerptable hit must fall through rather than blank the excerpt.
    assert probes._hit_excerpt({"a.py": _LOOP}, [_hit("never/fetched.py")]) == ""


def test_only_run_shape_hits_qualify():
    # A provider-identifier hit is not evidence of a loop.
    out = probes._hit_excerpt(
        {"cfg/settings.py": "OPENAI_API_KEY='x'\n"},
        [{"path": "cfg/settings.py", "marker_class": "provider_identifier",
          "line_no": 1, "line": "OPENAI_API_KEY", "marker": "OPENAI_API_KEY"}])
    assert out == "", out


def test_excerpt_is_centred_on_the_hit_line_not_the_file_head():
    # DISCRIMINATING: measured on real content, body[:2000] of aider's 86KB
    # base_coder.py showed only imports, so zero loop tokens reached the model even
    # once the correct file was chosen.
    body = "\n".join(["import os"] * 400 + ["        while True:  # the agent loop"] + ["pass"] * 50)
    out = probes._hit_excerpt({"a/loop.py": body}, [_hit("a/loop.py", line_no=401)])
    assert "while True:  # the agent loop" in out, out[:200]
    assert "run-shape at line 401" in out, out[:200]


def test_window_falls_back_to_head_when_line_no_is_unusable():
    body = "\n".join(f"line{i}" for i in range(100))
    for bad in (None, 0, -5, "12", 99999):
        assert probes._window(body, bad).startswith("line0"), bad


# -- the prompt: both excerpts, deduped ---------------------------------------------

def test_prompt_keeps_the_entrypoint_alongside_the_hit_file():
    """REGRESSION guard (measured in production on the PR #127 deploy).

    agenticSeek promoted to `agent` on a citation of `cli.py:main()`. Its only
    run_shape hit is `sources/memory.py`, so sending the hit INSTEAD of the entrypoint
    took `cli.py` out of the prompt entirely — citation empty, promotion refused,
    corpus agent 2 -> 1."""
    content = {"README.md": "An autonomous agent.",
               "cli.py": "def main():\n    Agent().run()\n",
               "sources/memory.py": _LOOP}
    prompt = probes._bespoke_agent_llm_prompt(
        "possible bespoke agent", content, ("cli.py", "sources/memory.py"),
        hits=[_hit("sources/memory.py")])
    assert "sources/memory.py" in prompt, prompt      # the loop
    assert "cli.py" in prompt, prompt                # the previously-cited entrypoint


def test_prompt_does_not_duplicate_when_hit_and_entrypoint_are_the_same_file():
    content = {"README.md": "x", "cli.py": _LOOP}
    prompt = probes._bespoke_agent_llm_prompt("p", content, ("cli.py",), hits=[_hit("cli.py")])
    assert prompt.count("### cli.py") == 1, prompt


def test_prompt_without_hits_is_unchanged_from_the_path_rule():
    # Additive: a snapshot with no hits must behave exactly as before this feature.
    content = {"README.md": "x", "cli.py": _STUB}
    assert probes._bespoke_agent_llm_prompt("p", content, ("cli.py",), hits=()) \
        == probes._bespoke_agent_llm_prompt("p", content, ("cli.py",))


def test_entrypoint_picker_is_pure_path_rule():
    # `_bespoke_run_shape_entrypoint` owns the ENTRYPOINT slot only; hit selection is
    # `_hit_excerpt`'s job. Collapsing them cost a classification once already.
    out = probes._bespoke_run_shape_entrypoint({"cli.py": _STUB}, ("cli.py",))
    assert "cli.py" in out, out
