from assessor.assessment.compose_mcp import compose

TS = '''
const server = new McpServer({name: "x"});
server.registerTool("search", {description: "find"}, async () => {});
server.tool("write_file", async () => {});
const t = new StdioServerTransport();
'''


def test_transport_and_tool_names_are_facts():
    r = compose({"src/server.ts": TS})
    assert r["transport"]["value"] == "stdio"
    names = {t["name"] for t in r["tools"]}
    assert names == {"search", "write_file"}


def test_python_fastmcp_decorator_tools():
    py = "app = FastMCP('x')\n@app.tool()\ndef add(a, b):\n    return a+b\n"
    r = compose({"server.py": py})
    assert {t["name"] for t in r["tools"]} == {"add"}


def test_unknown_transport_when_absent():
    r = compose({"README.md": "an mcp server"})
    assert "known_unknown" in r["transport"]


def test_py_prefers_name_kwarg_and_allows_stacked_decorators():
    py = ('app = FastMCP("x")\n'
          '@app.tool(name="delete_record")\ndef handler(): ...\n'
          '@app.tool()\n@requires_auth\ndef go(): ...\n')
    names = {t["name"] for t in compose({"server.py": py})["tools"]}
    assert names == {"delete_record", "go"}   # registered name preferred; stacked decorator not dropped


def test_ts_ignores_comment_and_string_false_matches():
    ts = ('const s = new McpServer({});\n'
          's.tool("real_tool", ()=>{});\n'
          '// s.tool("commented_out")\n'
          'const help = "call .tool(\\"debug_exec\\") to run";\n')
    names = {t["name"] for t in compose({"src/server.ts": ts})["tools"]}
    assert names == {"real_tool"}   # comment + in-string mentions are not registrations


def test_tools_complete_false_when_listtools_handler_present():
    ts = ('s.tool("a", ()=>{});\n'
          's.setRequestHandler(ListToolsRequestSchema, async () => ({tools: [{name: "b"}]}));\n')
    r = compose({"src/server.ts": ts})
    assert r["tools_complete"] is False   # dynamic handler may hold more than the scanned [a]
    assert r["tools_incomplete_reason"] == "dynamic tool list handler present"


def test_tools_complete_false_when_no_tools_found():
    # a server with transport but zero statically-scannable tool registrations
    r = compose({"README.md": "an mcp server", "src/server.ts": "new StdioServerTransport();"})
    assert r["tools"] == [] and r["tools_complete"] is False
    assert r["tools_incomplete_reason"] == "no tool registrations statically found"


def test_tools_complete_true_when_tools_found_and_no_dynamic_handler():
    r = compose({"src/server.ts": TS})
    assert {t["name"] for t in r["tools"]} == {"search", "write_file"}
    assert r["tools_complete"] is True and r["tools_incomplete_reason"] is None


GO_STRUCT = '''
package github
func IssueReadTool() inventory.ServerTool {
	return NewTool(ToolsetMetadataIssues, mcp.Tool{
		Name:        "issue_read",
		Description: t("DESC", "read an issue"),
	}, scopes, handler)
}
func ActionsListTool() inventory.ServerTool {
	tool := NewTool(ToolsetMetadataActions, mcp.Tool{Name: "actions_list"}, scopes, handler)
	return tool
}
'''

GO_MARK3LABS = '''
package main
func main() {
	s.AddTool(mcp.NewTool("hello_world", mcp.WithDescription("hi")), handler)
	s.AddTool(mcp.NewToolWithRawSchema("raw_tool", "d", schema), handler)
}
'''


def test_python_low_level_tool_constructor():
    # official low-level servers register via @server.list_tools() returning Tool(name="x")
    py = ('from mcp.types import Tool\n'
          'TOOLS = [Tool(name="fetch", description="d"),\n'
          '         Tool(\n             name="search_books",\n             description="d")]\n')
    assert {t["name"] for t in compose({"server.py": py})["tools"]} == {"fetch", "search_books"}


def test_python_tool_ctor_ignores_non_tool_classes():
    py = 'x = ToolAnnotations(name="ann")\np = mcp.Prompt(name="a_prompt")\nt = Tool(name="real")\n'
    assert {t["name"] for t in compose({"server.py": py})["tools"]} == {"real"}


def test_python_no_paren_decorator_and_add_tool():
    py = ('@mcp.tool\ndef greet():\n    ...\n'
          'mcp.add_tool(some_fn, name="explicit_add")\n')
    assert {t["name"] for t in compose({"server.py": py})["tools"]} == {"greet", "explicit_add"}


def test_ts_object_arg_and_addtool_forms():
    ts = ('s.registerTool({name: "obj_tool", description: "d"}, h);\n'
          's.addTool("str_tool", async()=>{});\n')
    assert {t["name"] for t in compose({"src/server.ts": ts})["tools"]} == {"obj_tool", "str_tool"}


def test_ts_bare_object_name_not_captured():
    # a {name:"x"} NOT anchored to registerTool/addTool must not be captured
    ts = 'const config = {name: "not_a_tool"};\ns.registerTool("real_tool", {}, h);\n'
    assert {t["name"] for t in compose({"src/server.ts": ts})["tools"]} == {"real_tool"}


def test_go_official_sdk_struct_literal_tools():
    r = compose({"pkg/github/issues.go": GO_STRUCT})
    assert {t["name"] for t in r["tools"]} == {"issue_read", "actions_list"}


def test_go_mark3labs_constructor_tools():
    r = compose({"main.go": GO_MARK3LABS})
    assert {t["name"] for t in r["tools"]} == {"hello_world", "raw_tool"}


def test_go_does_not_capture_server_or_prompt_names():
    # bare Name: on non-Tool structs (server impl / prompt) must NOT be captured
    go = ('package main\n'
          's := mcp.NewServer(&mcp.Implementation{Name: "greeter"}, nil)\n'
          's.AddPrompt(&mcp.Prompt{Name: "code_review"}, h)\n'
          's.AddTool(&mcp.Tool{Name: "real_tool"}, h)\n')
    r = compose({"server.go": go})
    assert {t["name"] for t in r["tools"]} == {"real_tool"}   # not greeter, not code_review


RUST = '''
#[tool_router]
impl Counter {
    /// doc comment
    #[tool(description = "Increment the counter")]
    async fn increment(&self) -> Result<()> { }

    #[rmcp::tool(name = "get-weather", description = "d")]
    pub async fn get_weather(&self) -> String { }
}
fn helper_not_a_tool() {}
'''


def test_rust_tool_macro_method_name_and_explicit():
    r = compose({"src/lib.rs": RUST})
    assert {t["name"] for t in r["tools"]} == {"increment", "get-weather"}   # not tool_router, not helper


CSHARP = '''
[McpServerToolType]
public class WeatherTools {
    [McpServerTool, Description("Get forecast")]
    public static async Task<string> GetForecast(string city) { return ""; }

    [McpServerTool(Name = "get_alerts_v2")]
    public static string GetAlertsAsync() { return ""; }
}
'''


def test_csharp_attribute_method_snakecase_and_explicit():
    r = compose({"Tools/WeatherTools.cs": CSHARP})
    # GetForecast -> snake_case wire name; explicit Name verbatim; class marker not captured
    assert {t["name"] for t in r["tools"]} == {"get_forecast", "get_alerts_v2"}


JAVA = '''
import io.modelcontextprotocol.spec.McpSchema;
class Tools {
    Object logTool() {
        return new McpServerFeatures.SyncToolSpecification(
            new McpSchema.Tool("logPrompt", "t", "d", schema), h);
    }
    @McpTool(name = "start_task", description = "d")
    public String startTask() { return ""; }
    @McpTool
    public String stopTask() { return ""; }
}
'''


def test_java_ctor_and_mcptool_annotations():
    r = compose({"src/Tools.java": JAVA})
    assert {t["name"] for t in r["tools"]} == {"logPrompt", "start_task", "stopTask"}


def test_java_bare_tool_annotation_needs_mcp_context():
    no_ctx = '@Tool(name = "lc_tool", description = "d")\npublic String foo() { return ""; }\n'
    assert compose({"A.java": no_ctx})["tools"] == []                       # LangChain4j-style @Tool ignored
    with_ctx = ('import io.modelcontextprotocol.server.McpSyncServer;\n'
                '@Tool(name = "mcp_tool", description = "d")\npublic String bar() { return ""; }\n')
    assert {t["name"] for t in compose({"B.java": with_ctx})["tools"]} == {"mcp_tool"}


def test_kotlin_add_tool_named_and_positional():
    kt = 'server.addTool(name = "get-people", description = "d") { }\nserver.addTool("plain-name") { }\n'
    assert {t["name"] for t in compose({"Main.kt": kt})["tools"]} == {"get-people", "plain-name"}


def test_block_commented_tool_not_captured():
    go = '/* s.AddTool(&mcp.Tool{Name: "commented"}, h) */\ns.AddTool(&mcp.Tool{Name: "real"}, h)\n'
    assert {t["name"] for t in compose({"x.go": go})["tools"]} == {"real"}


def test_tools_complete_false_when_source_coverage_capped():
    r = compose({"src/server.ts": TS}, source_coverage_capped=True)
    assert {t["name"] for t in r["tools"]} == {"search", "write_file"}   # tools found...
    assert r["tools_complete"] is False                                  # ...but coverage was capped
    assert r["tools_incomplete_reason"] == "source coverage capped/truncated — tool list not fully scanned"
