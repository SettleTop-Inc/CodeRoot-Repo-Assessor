import json

from assessor.assessment.compose_mcp import _tool_names, compose


def _pkg(deps=None):
    return json.dumps({"dependencies": deps or {}})


def test_go_musttool_wrapper():
    src = 'var ListTeams = mcpgrafana.MustTool(\n    "list_teams",\n    "desc",\n)'
    names = [t["name"] for t in _tool_names({"tools/admin.go": src})]
    assert "list_teams" in names


def test_ts_definetool_wrapper():
    src = 'export default defineTool({\n  name: "whoami",\n  inputSchema: {},\n});'
    names = [t["name"] for t in _tool_names({"src/tools/whoami.ts": src})]
    assert "whoami" in names


def test_ts_static_toolname_field():
    src = 'class FindTool {\n  static toolName = "find";\n}'
    names = [t["name"] for t in _tool_names({"src/tools/find.ts": src})]
    assert "find" in names


def test_go_wrapper_fp_guard():
    # a non-verb Tool token must NOT match
    assert _tool_names({"x.go": 'r := mcp.NewToolResult("nope")'}) == []


def test_existing_sdk_forms_still_extract():
    assert [t["name"] for t in _tool_names({"src/i.ts": 'srv.registerTool("search", {})'})] == ["search"]
    assert [t["name"] for t in _tool_names({"s.go": 'mcp.NewTool("x")'})] == ["x"]


def test_remote_proxy_reason():
    src = 'const t = this._mcpClient.getTools();\nserver.tool(remoteTool.name, "d");\nconst URL="https://mcp.stripe.com";'
    out = compose({"src/toolkit.ts": src})
    assert out["tools"] == []
    assert "remote-proxy" in out["tools_incomplete_reason"]


def test_external_relocation_reason():
    src = "const { tools } = require('playwright-core/lib/coreBundle');\nmodule.exports = { createConnection: tools.createConnection };"
    out = compose({"index.js": src})
    assert out["tools"] == []
    assert "external package" in out["tools_incomplete_reason"]


def test_plain_zero_tools_keeps_generic_reason():
    out = compose({"src/app.ts": "const x = 1;"})
    assert out["tools"] == [] and out["tools_incomplete_reason"] == "no tool registrations statically found"


def test_local_tools_mcp_import_is_not_external_relocation():
    # A repo importing its OWN tools from a LOCAL ./tools/mcp dir (relative specifier) must NOT be
    # labeled "relocated to an external package" — that would overclaim. Generic reason instead.
    src = "const { tools } = require('./tools/mcp');\nmodule.exports = { createConnection: tools.createConnection };"
    out = compose({"index.js": src})
    assert out["tools"] == []
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


def test_capped_still_wins_over_flags():
    src = 'this._mcpClient.getTools(); const URL="https://mcp.stripe.com";'
    out = compose({"src/toolkit.ts": src}, source_coverage_capped=True)
    assert "capped" in out["tools_incomplete_reason"]


# -- generalized external-delegation detector (spec §4 / DP1) ----------------------------------

def test_js_module_exports_require_names_external_pkg():
    src = "module.exports = require('some-mcp-core');"
    out = compose({"index.js": src, "package.json": _pkg({"some-mcp-core": "^1"})})
    assert out["tools"] == [] and "some-mcp-core" in out["tools_incomplete_reason"]


def test_reexport_of_own_sdk_is_not_relocation():
    src = "export { Server } from '@modelcontextprotocol/sdk/server/index.js';"
    out = compose({"index.ts": src, "package.json": _pkg({"@modelcontextprotocol/sdk": "^1"})})
    assert out["tools"] == []
    assert "@modelcontextprotocol/sdk" not in out["tools_incomplete_reason"]   # denylisted
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"  # generic


def test_python_console_script_delegates_to_external():
    py = '[project]\ndependencies = ["upstream-mcp"]\n[project.scripts]\nfoo = "upstream_mcp.__main__:main"\n'
    out = compose({"pyproject.toml": py, "README.md": "an mcp server"})
    assert out["tools"] == [] and "upstream" in out["tools_incomplete_reason"].lower()


def test_local_reexport_is_not_relocation():
    out = compose({"index.js": "module.exports = require('./server');"})
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


def test_playwright_characterization_still_named():
    src = ("const { tools } = require('playwright-core/lib/coreBundle');\n"
           "module.exports = { createConnection: tools.createConnection };")
    out = compose({"index.js": src, "package.json": _pkg({"playwright-core": "^1"})})
    assert out["tools"] == [] and "playwright-core" in out["tools_incomplete_reason"]


def test_reexport_of_unnamed_pkg_is_unnamed_reason():
    # not mcp/server/tool-named and NOT a declared dep -> matched but un-named (spec §4 naming
    # discipline "else" branch), distinct wording from the named reason.
    out = compose({"index.js": "module.exports = require('acme-widgets');"})
    assert out["tools"] == []
    assert out["tools_incomplete_reason"] == ("tools re-exported from an external package — "
                                               "not present in this repo")


def test_local_barrel_one_hop_resolves_via_merged_entrypoints():
    out = compose({"index.js": "export * from './server';",
                    "server.js": "module.exports = require('upstream-mcp-core');"})
    assert out["tools"] == [] and "upstream-mcp-core" in out["tools_incomplete_reason"]


def test_dockerfile_cmd_python_module_names_external_pkg():
    dockerfile = 'FROM python:3.12\nCMD ["python", "-m", "upstream_mcp"]\n'
    out = compose({"Dockerfile": dockerfile, "README.md": "an mcp server"})
    assert out["tools"] == [] and "upstream" in out["tools_incomplete_reason"].lower()


def test_dockerfile_cmd_own_local_module_is_not_delegation():
    dockerfile = 'FROM python:3.12\nCMD ["python", "-m", "mytool"]\n'
    out = compose({"Dockerfile": dockerfile, "mytool/__main__.py": "print('x')",
                    "mytool/server.py": "pass"})
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


def test_python_console_script_to_own_package_is_not_delegation():
    # the overwhelmingly common real shape: a package's OWN [project.scripts] entry pointing at
    # its OWN __main__ — must never be mislabeled as external relocation.
    py = '[project]\nname = "mytool"\ndependencies = []\n[project.scripts]\nmytool = "mytool.__main__:main"\n'
    out = compose({"pyproject.toml": py, "mytool/__main__.py": "print('hi')"})
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


def test_smithery_start_command_npx_names_external_pkg():
    smithery = "startCommand:\n  type: stdio\n  commandFunction: |\n    npx -y some-mcp-server\n"
    out = compose({"smithery.yaml": smithery, "README.md": "an mcp server"})
    assert out["tools"] == [] and "some-mcp-server" in out["tools_incomplete_reason"]


def test_go_blank_import_names_external_pkg():
    src = 'package main\n\nimport (\n\t_ "github.com/acme/my-mcp-tools"\n)\n\nfunc main() {}\n'
    out = compose({"main.go": src})
    assert out["tools"] == [] and "my-mcp-tools" in out["tools_incomplete_reason"]


def test_go_blank_import_of_own_sdk_is_denylisted():
    src = 'package main\n\nimport (\n\t_ "github.com/mark3labs/mcp-go/server"\n)\n\nfunc main() {}\n'
    out = compose({"main.go": src})
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


# -- FP fixes (DP1 review): naming discipline gates MATCHING, not just naming -------------------

def test_js_property_bag_non_server_key_is_not_delegation():
    # `pino` is a LOGGER: `{logger: pino}` is a property bag exporting an unrelated symbol under a
    # non-server key. Must NOT be treated as server delegation at all (generic reason), even though
    # pino is a declared dependency bound from a bare require().
    out = compose({"index.js": "const pino=require('pino');\nmodule.exports={logger:pino};",
                    "package.json": _pkg({"pino": "^8"})})
    assert out["tools"] == []
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


def test_docker_generic_launcher_fronting_local_app_is_not_delegation():
    # `gunicorn` is a generic WSGI launcher fronting a LOCAL flask app — never a server-delegation
    # match, regardless of whether gunicorn is a declared dependency.
    out = compose({"Dockerfile": 'FROM python:3.12\nCMD ["python","-m","gunicorn"]',
                    "requirements.txt": "gunicorn==21.2.0\nflask==3.0.0",
                    "app.py": "from flask import Flask\napp=Flask(__name__)"})
    assert out["tools"] == []
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


def test_go_blank_import_of_driver_is_not_delegation():
    # `lib/pq` is a DB driver, not a server/tool source — a blank import of a non-mcp/tool/server
    # path must not fire, even with a thin local main.
    src = 'package main\n\nimport (\n\t_ "github.com/lib/pq"\n)\n\nfunc main() {}\n'
    out = compose({"main.go": src})
    assert out["tools"] == []
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


# -- FP fix (DP1 re-review): object-export key narrowed to distinctive entrypoints only ---------

def test_js_object_export_connection_key_is_not_delegation():
    # `connection` is a generic config key (DB connection pools, etc.), not a distinctive MCP
    # server entrypoint. A `pg` driver wired under it must not be treated as server delegation,
    # even though `pg` is a declared dependency bound from a bare require().
    out = compose({"index.js": "module.exports = { connection: dbConn(require('pg')), retries: 3 };",
                    "package.json": _pkg({"pg": "^8"})})
    assert out["tools"] == []
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"


def test_js_object_export_default_key_is_not_delegation():
    # `default` is TS/Babel default-export interop (`__esModule` marker) — extremely common in
    # bundled entrypoints, not a distinctive MCP server entrypoint. A `winston` logger wired under
    # it must not be treated as server delegation.
    out = compose({"index.js": "module.exports = { default: winston(require('winston')), __esModule: true };",
                    "package.json": _pkg({"winston": "^3"})})
    assert out["tools"] == []
    assert out["tools_incomplete_reason"] == "no tool registrations statically found"
