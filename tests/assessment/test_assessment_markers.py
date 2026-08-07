from assessor.assessment.markers import (
    AGENT_FRAMEWORK_CONSTRUCTS, AGENT_HOST_DIRS, AGENT_LOOP_MARKERS,
    LLM_INTERACTION_MARKERS, MARKER_VOCAB_VERSION, PROVIDER_ENV_MARKERS,
    is_scannable, scan_text,
)


def test_provider_identifier_hit_records_path_line_and_class():
    hits = scan_text("src/llm.py", 'KEY = os.environ["ANTHROPIC_API_KEY"]\n')
    assert len(hits) == 1
    h = hits[0]
    assert h["marker"] == "ANTHROPIC_API_KEY"
    assert h["marker_class"] == "provider_identifier"
    assert h["path"] == "src/llm.py" and h["line_no"] == 1
    assert "ANTHROPIC_API_KEY" in h["line"]


def test_line_numbers_are_one_based_and_correct():
    hits = scan_text("a.py", "x = 1\ny = 2\nOPENAI_API_KEY\n")
    assert [h["line_no"] for h in hits] == [3]


def test_long_lines_are_truncated_to_200_chars():
    hits = scan_text("a.py", "OPENAI_API_KEY" + "x" * 500 + "\n")
    assert len(hits[0]["line"]) == 200


def test_base_url_and_model_id_markers_are_recognised():
    assert scan_text("a.py", "OPENAI_BASE_URL=1\n")[0]["marker_class"] == "provider_identifier"
    assert scan_text("a.py", 'model="claude-sonnet-4-5"\n')[0]["marker_class"] == "provider_model_id"


def test_agent_host_dir_hit_is_classed_separately():
    hits = scan_text(".codex/skills/code-review/SKILL.md", "---\nname: x\n---\n")
    assert hits and hits[0]["marker_class"] == "agent_host_config"


def test_agent_run_shape_construct_is_recognised():
    hits = scan_text("app.py", "graph = StateGraph(State)\n")
    assert hits[0]["marker_class"] == "agent_run_shape"


def test_vendored_lock_and_binary_paths_are_not_scannable():
    for p in ("node_modules/x/index.js", "vendor/a.go", "package-lock.json",
              "poetry.lock", "logo.png", "app.wasm"):
        assert is_scannable(p) is False, p


def test_ordinary_source_and_docs_are_scannable():
    for p in ("src/llm.py", "README.md", ".env.example", "docker-compose.yml"):
        assert is_scannable(p) is True, p


def test_a_clean_file_yields_no_hits():
    assert scan_text("a.py", "def add(a, b):\n    return a + b\n") == []


def test_vocab_version_is_an_int():
    assert isinstance(MARKER_VOCAB_VERSION, int) and MARKER_VOCAB_VERSION >= 1


def test_agent_host_dirs_cover_the_observed_vendors():
    for d in (".claude/", ".codex/", ".cline/", ".agents/", ".qwen/", ".crush/"):
        assert d in AGENT_HOST_DIRS, d


# -- fix round 1 coverage (CRITICAL-1) -----------------------------------------------------

def test_vendored_dir_matching_is_by_path_segment_not_substring():
    # "build/" as a bare substring would wrongly exclude "rebuild" and "overbuild".
    assert is_scannable("packages/build/index.ts") is False
    assert is_scannable("src/rebuild/agent.py") is True
    assert is_scannable("src/overbuild/x.py") is True


# -- fix round 1 (IMPORTANT-2) --------------------------------------------------------------

def test_tool_calls_and_function_call_are_not_run_shape_markers():
    # v3 UPDATE: these tokens are now vocabulary (LLM_INTERACTION_MARKERS) rather than
    # absent, so the two membership assertions this test used to make against the flat
    # AGENT_RUN_SHAPE tuple no longer describe the design. What they were PROTECTING --
    # that a bare LLM response-field read is not an agent -- is unchanged and is what the
    # behavioural assertions below (and `test_bare_tool_calls_alone_is_not_run_shape`)
    # still pin: on their own, with no loop bound and no agent identity, they emit nothing.
    assert "tool_calls" not in AGENT_FRAMEWORK_CONSTRUCTS
    assert "function_call" not in AGENT_FRAMEWORK_CONSTRUCTS
    assert scan_text("a.py", "resp.choices[0].message.tool_calls\n") == []
    assert scan_text("a.py", "data['function_call']\n") == []


# -- fix round 1 (IMPORTANT-3) --------------------------------------------------------------

def test_azure_openai_key_yields_a_single_longest_match():
    hits = scan_text("a.py", "AZURE_OPENAI_API_KEY=x\n")
    assert len(hits) == 1
    assert hits[0]["marker"] == "AZURE_OPENAI_API_KEY"


# -- fix round 2 (IMPORTANT-3 regression): dedup must compare match SPANS, not marker NAMES --

def test_openai_key_alone_yields_a_single_hit():
    hits = scan_text("a.py", "OPENAI_API_KEY=x\n")
    assert len(hits) == 1
    assert hits[0]["marker"] == "OPENAI_API_KEY"


def test_distinct_openai_and_azure_keys_on_one_line_both_survive():
    # Regression case introduced by the round-1 name-based fix: OPENAI_API_KEY here is a
    # genuinely separate declaration from AZURE_OPENAI_API_KEY, not a substring artifact of
    # it, so both must be reported.
    hits = scan_text("a.py", "OPENAI_API_KEY=x; AZURE_OPENAI_API_KEY=y\n")
    assert len(hits) == 2
    assert {h["marker"] for h in hits} == {"OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"}


def test_same_marker_repeated_on_one_line_yields_one_hit():
    hits = scan_text("a.py", "OPENAI_API_KEY=a OPENAI_API_KEY=b\n")
    assert len(hits) == 1
    assert hits[0]["marker"] == "OPENAI_API_KEY"


# -- fix round 1 (IMPORTANT-4) ---------------------------------------------------------------

def test_model_id_regex_does_not_match_inside_ordinary_identifiers():
    assert scan_text("a.py", "photo1-2.png\n") == []
    assert scan_text("a.py", "ISO1-2022\n") == []


def test_model_id_regex_still_matches_real_model_ids():
    assert scan_text("a.py", 'model="o3-mini"\n')[0]["marker_class"] == "provider_model_id"
    assert scan_text("a.py", 'model="claude-sonnet-4-5"\n')[0]["marker_class"] == "provider_model_id"


# -- fix round 1 (IMPORTANT-5): every vocab entry must be exercised, so a deletion fails ----

def test_every_provider_env_marker_produces_a_hit():
    for m in PROVIDER_ENV_MARKERS:
        hits = scan_text("a.py", f"{m}=1\n")
        assert any(h["marker_class"] == "provider_identifier" and h["marker"] == m
                    for h in hits), m


def test_every_agent_framework_construct_produces_a_hit():
    # v3 UPDATE: only FRAMEWORK constructs are unambiguous enough to fire standalone; the
    # other three groups are exercised in their co-occurring pairs by the two tests below,
    # so a deletion from any group still fails the suite (the round-1 IMPORTANT-5 intent).
    for c in AGENT_FRAMEWORK_CONSTRUCTS:
        hits = scan_text("a.py", f"{c}\n")
        assert any(h["marker_class"] == "agent_run_shape" for h in hits), c


def test_every_llm_interaction_marker_produces_a_hit_alongside_a_loop_bound():
    for m in LLM_INTERACTION_MARKERS:
        hits = scan_text("a.py", f"while True:\n    {m}\n")
        assert any(h["marker_class"] == "agent_run_shape" for h in hits), m


def test_every_agent_loop_marker_produces_a_hit_alongside_an_interaction():
    for c in AGENT_LOOP_MARKERS:
        hits = scan_text("a.py", f"{c}\n    msg.tool_calls\n")
        assert any(h["marker_class"] == "agent_run_shape" for h in hits), c


def test_every_agent_host_dir_produces_a_hit():
    for d in AGENT_HOST_DIRS:
        hits = scan_text(f"{d}config.yml", "irrelevant\n")
        assert hits and hits[0]["marker_class"] == "agent_host_config", d


# -- fix round 1 (MINOR-7) -------------------------------------------------------------------

def test_gpt_oss_family_is_recognised():
    hits = scan_text("a.py", 'model="gpt-oss-20b"\n')
    assert hits and hits[0]["marker_class"] == "provider_model_id"


# -- v3: agent_run_shape is a FILE-LEVEL co-occurrence decision ------------------------------

def test_bare_tool_calls_alone_is_not_run_shape():
    # A one-shot completion: the exact false positive that justified removing the bare token.
    body = "resp = client.chat.completions.create(...)\nfor c in resp.choices[0].message.tool_calls:\n    pass\n"
    assert [h for h in scan_text("one_shot.py", body) if h["marker_class"] == "agent_run_shape"] == []


def test_tool_calls_plus_loop_bound_is_run_shape():
    # An agent loop: same token, but bounded iteration alongside it.
    body = "max_iterations = 20\nwhile True:\n    for tc in msg.tool_calls:\n        run(tc)\n"
    hits = [h for h in scan_text("loop.py", body) if h["marker_class"] == "agent_run_shape"]
    assert hits, "LLM-interaction + loop bound must yield agent_run_shape"


def test_max_iterations_alone_is_not_run_shape():
    # cline/OpenHands have this, but a bare retry bound is not an agent.
    assert [h for h in scan_text("retry.py", "max_retries = 3\nmax_iterations = 5\n")
            if h["marker_class"] == "agent_run_shape"] == []


def test_agent_identity_plus_interaction_is_run_shape():
    body = "class ResearchAgent:\n    def act(self):\n        return self.llm.messages.create(...)\n"
    assert [h for h in scan_text("a.py", body) if h["marker_class"] == "agent_run_shape"]


def test_framework_construct_still_fires_standalone():
    for c in ("StateGraph(", "AgentExecutor(", "create_react_agent(", "Crew("):
        assert [h for h in scan_text("f.py", f"g = {c}x)\n") if h["marker_class"] == "agent_run_shape"], c


def test_cross_language_identity_markers():
    for path, body in (("a.go", "type CoderAgent struct {}\nfunc (a *CoderAgent) Run() { a.llm.ChatCompletion() }\n"),
                       ("a.rs", "struct SessionAgent;\nimpl SessionAgent { fn run(&self) { self.chat_completions(); } }\n")):
        assert [h for h in scan_text(path, body) if h["marker_class"] == "agent_run_shape"], path


def test_identity_marker_does_not_match_inside_another_word():
    # DISCRIMINATING: without a leading \b the `class` alternative matches inside `subclass`,
    # so this prose comment + an interaction marker scores identity-and-interaction and emits
    # a run_shape hit off a sentence. Fails (hit present) if the \b is removed.
    body = "# subclass BaseAgent to add tools\nr = client.chat.completions.create(m)\n"
    assert not [h for h in scan_text("d.py", body) if h["marker_class"] == "agent_run_shape"]
    # control: the same interaction WITH a real declaration still fires, so the guard above
    # is not passing merely because the interaction half stopped working.
    real = "class BaseAgent:\n    r = client.chat.completions.create(m)\n"
    assert [h for h in scan_text("d.py", real) if h["marker_class"] == "agent_run_shape"]


def test_run_shape_hit_reports_a_real_line():
    # Pins the ACTUAL anchor line, not just "some line". `line_no >= 1` and a truthy `line`
    # hold for every non-blank source line in this fixture -- including line 1 (`x=1`), which
    # carries no marker at all -- so the weaker form passes against an implementation that
    # anchors on the wrong line entirely.
    hits = [h for h in scan_text("l.py", "x=1\nmax_iterations=5\nfor tc in m.tool_calls: pass\n")
            if h["marker_class"] == "agent_run_shape"]
    assert hits[0]["line_no"] == 2, hits[0]
    assert "max_iterations" in hits[0]["line"], hits[0]


def test_vocab_version_is_3():
    assert MARKER_VOCAB_VERSION == 3


def test_at_most_one_run_shape_hit_per_file():
    # The decision is per FILE, so a repeat-dense file must not flood `marker_hits` (which
    # is capped at 500 in git_fetch) with the same evidence over and over.
    body = "while True:\n    msg.tool_calls\n" * 20
    assert len([h for h in scan_text("a.py", body) if h["marker_class"] == "agent_run_shape"]) == 1


def test_rust_and_go_infinite_loops_are_loop_markers_only_in_co_occurrence():
    # Fix round 1: the rest of AGENT_LOOP_MARKERS is Python/JS-shaped, so a Rust or Go
    # agent's loop never counted. `loop {` / `for {` are LOOP markers, which means they
    # still only count alongside an LLM-interaction marker -- a bare infinite loop appears
    # in a great deal of ordinary Rust and Go and must not fire on its own.
    assert "loop {" in AGENT_LOOP_MARKERS and "for {" in AGENT_LOOP_MARKERS
    assert [h for h in scan_text("a.rs", "loop {\n    poll_queue();\n}\n")
            if h["marker_class"] == "agent_run_shape"] == []
    assert [h for h in scan_text("a.go", "for {\n    select {}\n}\n")
            if h["marker_class"] == "agent_run_shape"] == []
    assert [h for h in scan_text("a.rs", "loop {\n    let r = client.chat_completions(&msgs);\n}\n")
            if h["marker_class"] == "agent_run_shape"], "rust loop + interaction must fire"
    assert [h for h in scan_text("a.go", "for {\n    resp := c.ChatCompletion(ctx)\n}\n")
            if h["marker_class"] == "agent_run_shape"], "go loop + interaction must fire"


def test_identity_alone_is_not_run_shape():
    # A class named *Agent with no LLM interaction in the file is not evidence enough.
    assert [h for h in scan_text("a.py", "class UserAgent:\n    def parse(self): pass\n")
            if h["marker_class"] == "agent_run_shape"] == []
