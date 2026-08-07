"""mcp_server composition: transport/auth (Fact) + tool-name static scan (Fact).

Tool NAMES are facts; descriptions/side-effects are added later (assessed) and are
NOT part of the fingerprint. The server is never executed.
"""
from __future__ import annotations

import re

from .shapes import fact, unknown

_TRANSPORT = [
    ("stdio", re.compile(r"StdioServerTransport|\bstdio\b", re.I)),
    ("streamable-http", re.compile(r"streamableHttp|StreamableHTTPServerTransport", re.I)),
    ("sse", re.compile(r"SSEServerTransport|\bsse\b", re.I)),
]
# TS: <obj>.tool("name" | .registerTool("name" | .addTool("name" — a word/`)`/`]` must precede so a
# bare " .tool(" inside a string literal isn't a registration. Comments are stripped first.
_TS_TOOL = re.compile(r"""[\w$)\]]\.(?:registerTool|addTool|tool)\(\s*["']([A-Za-z0-9_.\-]+)["']""")
# object-argument form (fastmcp-TS + repo-local wrappers): registerTool/addTool/defineTool
# ({name:"x", …}). Anchored to the registration verb + `({` so a free-floating {name:"x"} config
# literal is NOT captured; the name must appear before the first `}` (brace-safe). Verbs limited to
# MCP-distinctive ones (registerTool/addTool) + the one grounded repo-local wrapper (sentry's
# defineTool); generic factory verbs like createTool/makeTool are NOT included — they routinely name
# non-MCP objects (UI/build "tools") and would false-positive inside an mcp_server repo.
_TS_TOOL_OBJ = re.compile(
    r"""(?:registerTool|addTool|defineTool)\(\s*\{[^}]*?\bname\s*:\s*["']([A-Za-z0-9_.\-]+)["']""")
# repo-local class-static wrapper (e.g. mongodb's `static toolName = "x"`): optional `readonly`,
# optional `: string` type annotation before the `=`.
_TS_STATIC_TOOLNAME = re.compile(
    r'\bstatic\s+(?:readonly\s+)?toolName\s*(?::\s*string\s*)?=\s*["\']([A-Za-z0-9_.\-]+)["\']')
_PY_TOOL_DECO = re.compile(r"@\w+\.tool\b(?:\(([^)]*)\))?")   # parens optional (fastmcp bare @mcp.tool)
_PY_NAME_KW = re.compile(r"""name\s*=\s*["']([A-Za-z0-9_.\-]+)["']""")
_PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z0-9_]+)", re.M)
# Python low-level Server: Tool(name="x") constructors returned from a list_tools handler.
# `\bTool\(` (capital T) won't match ToolAnnotations()/mcp.Prompt()/the lowercase @x.tool().
_PY_TOOL_CTOR = re.compile(r"""\bTool\(\s*name\s*=\s*["']([A-Za-z0-9_.\-]+)["']""")
# programmatic mcp.add_tool(fn, name="x") — only when an explicit name= literal is present
# (a defaulted name=fn.__name__ stays a known_unknown).
_PY_ADD_TOOL = re.compile(r"""\.add_tool\([^)]*?\bname\s*=\s*["']([A-Za-z0-9_.\-]+)["']""")
# Go: official SDK struct literal `mcp.Tool{Name:"x"}` (name anchored INSIDE the mcp.Tool{…}
# struct so a bare Name: on mcp.Implementation{}/mcp.Prompt{} — the server/prompt name — is
# not captured); + mark3labs constructor `mcp.NewTool("x")`/`NewToolWithRawSchema("x")`.
_GO_TOOL_STRUCT = re.compile(r'(?s)&?mcp\.Tool\{[^}]*?\bName:\s*"([^"]+)"')
# repo-local wrapper constructors (e.g. grafana's mcpgrafana.MustTool("x", …)): any qualifier +
# a closed verb set (NewTool/MustTool/RegisterTool/AddTool), first string-literal arg. `\s*` spans
# newlines (Go call args commonly wrap), so this is compiled without re.S/re.M — `\s` already
# matches `\n`. The verb list is closed so NewToolResult(/etc. (a different token) can't match.
_GO_TOOL_CTOR = re.compile(
    r'\b(?:\w+\.)?(?:NewTool(?:WithRawSchema)?|MustTool|RegisterTool|AddTool)\(\s*"([^"]+)"')
# Rust (rmcp): #[tool(name="x")] explicit | #[tool(…)] w/o name → the next `fn` name (verbatim) |
# low-level Tool::new("x") / Tool{name:"x"}. `\btool\b` rejects #[tool_router]/#[tool_handler].
_RS_TOOL_NAME = re.compile(r'#\[\s*(?:rmcp::)?tool\s*\([^\]]*?\bname\s*=\s*"([^"]+)"')
_RS_TOOL_ATTR = re.compile(r'#\[\s*(?:rmcp::)?tool\b(?![^\]]*\bname\s*=)[^\]]*\]')
_RS_FN = re.compile(r'(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)')
_RS_TOOL_LIT = re.compile(r'(?s)&?(?:rmcp::)?Tool\s*\{[^}]*?\bname:\s*"([^"]+)"|\bTool::new(?:_with_raw)?\s*\(\s*"([^"]+)"')
# C# (csharp-sdk): [McpServerTool(Name="x")] explicit | [McpServerTool] w/o Name → next method name,
# snake_cased (SDK default). `\bMcpServerTool\b` excludes the class marker [McpServerToolType].
_CS_TOOL_NAME = re.compile(r'\[McpServerTool\s*\(\s*Name\s*=\s*"([^"]+)"')
_CS_TOOL_ATTR = re.compile(r'\[McpServerTool\b(?![^\]]*\bName\s*=)[^\]]*\]')
_CS_METHOD = re.compile(
    r'(?:^|\n)\s*(?:\[[^\]]*\]\s*)*'
    r'(?:(?:public|internal|private|protected|static|async|virtual|override|sealed|partial|readonly|extern|unsafe)\s+)+'
    r'[^\n=;{}()]*?\b([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\(', re.M)
# Java/Kotlin (java-sdk + Spring AI): new McpSchema.Tool("x") | Tool.builder("x")/.name("x") |
# Kotlin .addTool("x"|name="x") | @McpTool(name="x"|→method). @Tool(name="x") only in an MCP-context
# file (bare @Tool is LangChain4j/Spring, not MCP-specific).
_JAVA_TOOL_LIT = re.compile(
    r'\bnew\s+(?:McpSchema\.)?Tool\s*\(\s*"([^"]+)"'
    r'|(?:McpSchema\.)?Tool\.builder\s*\(\s*"([^"]+)"'
    r'|\.addTool\s*\(\s*(?:name\s*=\s*)?"([^"]+)"')
_JAVA_NAME_SETTER = re.compile(r'\.name\s*\(\s*"([^"]+)"\s*\)')
_JAVA_MCPTOOL_NAME = re.compile(r'@McpTool\s*\([^)]*?\bname\s*=\s*"([^"]+)"')
_JAVA_MCPTOOL_ATTR = re.compile(r'@McpTool\b(?![^)\n]*\bname\s*=)')
_JAVA_TOOL_ANN_NAME = re.compile(r'@Tool\s*\([^)]*?\bname\s*=\s*"([^"]+)"')
_JAVA_MCP_CTX = re.compile(r'McpSchema|McpServerFeatures|McpToolUtils|McpSyncServer|McpAsyncServer|@McpTool|\.addTool\(')
_JAVA_METHOD = re.compile(
    r'(?:^|\n)\s*(?:@\w+(?:\([^)]*\))?\s*)*'
    r'(?:(?:public|protected|private|static|final|abstract|default|synchronized|native)\s+)+'
    r'[^\n=;{}()]*?\b([A-Za-z_]\w*)\s*\(', re.M)
_LINE_COMMENT = re.compile(r"//[^\n]*|(?<!['\"])#(?![\[!])[^\n]*")   # `#(?![\[!])`: keep Rust #[…]/#! attrs
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)   # Rust/C#/Java use /* */ heavily; strip before scan
_AUTH_KEYS = re.compile(r"apiKey|api_key|token|bearer|Authorization", re.I)
# Tier 3 honesty flags (spec §5): a genuine zero-tools repo can be inherent (not a scan gap) when
# the entrypoint is a runtime proxy to a remote MCP server, or when tool defs live in an external
# package this repo re-exports. Conservative: both signals of a pair must be present; no signal
# falls through to the existing generic "no tool registrations statically found" reason. These scan
# the RAW (un-comment-stripped) `joined` source, so in principle a comment carrying both signals of
# a pair could set the specific reason — bounded and acceptable: it fires only at zero tools, needs
# BOTH signals, and changes only the reason STRING (never tools_complete), so it can't overclaim.
_REMOTE_PROXY = re.compile(r'\.getTools\(\)|list_tools\(\)')
_REMOTE_URL = re.compile(r'https://mcp\.[\w.\-]+|MCP_SERVER_URL|MCP_REMOTE|mcpServerUrl', re.I)

# Part B (spec §4, DP1): "the entrypoint delegates its server to an EXTERNAL package" —
# language-agnostic, multi-shape replacement for the old playwright-only _EXT_RELOCATE/
# _EXT_REEXPORT token pair. Fires only at zero statically-found tools (compose()'s
# `elif not tools:` branch), so a false negative here just keeps the existing honest generic
# reason — it can never make an already-honest result LESS honest, only more specific.
_MCP_FRAMEWORK_DENY = frozenset({"modelcontextprotocol", "mcp", "fastmcp", "rmcp", "mcp-go"})

# Naming discipline (DP1 review — FP fix): a package is only MATCHED at all (not merely named) when
# the repo genuinely delegates its SERVER to it. Two closed sets used across the shapes below:
# - a JS/TS object-literal export only counts as "the entire module export IS the bare import" when
#   it is wired through a recognized server entry-point KEY — an unrelated key (`{logger: pino}`)
#   is a property bag referencing a dependency, not delegation. Narrowed (DP1 re-review — FP fix)
#   to the two DISTINCTIVE MCP-server entrypoint names: `server`/`connection`/`default` were
#   dropped because they routinely collide with ordinary config objects (`{connection: dbConn,
#   retries: 3}`) and TS/Babel default-export interop (`{default: winston, __esModule: true}`) —
#   neither is server delegation. A genuine delegation under one of those generic keys now falls
#   through to the honest generic reason instead (fail-safe, precision-first).
_JS_SERVER_KEYS = ("createConnection", "createServer")
# - a generic WSGI/ASGI launcher can never itself BE the tool source — it always fronts something
#   else (usually local source the scanner already sees, or nothing statically visible at all).
_GENERIC_LAUNCHERS = frozenset({"gunicorn", "uvicorn", "hypercorn", "waitress", "celery",
                                 "flask", "fastapi"})
# - a Go blank-import path is only plausibly a server/tool registration when a path token names one;
#   a driver/codec (lib/pq, image/png) is not (spec §4 shape 3).
_GO_PLAUSIBLE_TOKENS = ("mcp", "tool", "server")

# JS/TS shape 1 (source re-export/passthrough): a symbol bound from a BARE import becomes the
# module's export. `(?!\.{1,2}/|/)` excludes relative/absolute specifiers — a repo re-exporting
# its OWN local tools/ dir is not relocation (same guard the old _EXT_RELOCATE had).
_JS_BARE_REQUIRE = re.compile(r'''require\(\s*["'](?!\.{1,2}/|/)([^"']+)["']\s*\)''')
# An object-export value counts as genuine delegation only when it IS the bare import itself, or a
# plain member-access chain off a symbol bound from one (e.g. `tools.createConnection`) — never a
# call-wrapped/transformed value (`dbConn(require('pg'))`, `wrap(tools).createConnection`), which
# means something merely CONSUMED the import rather than exporting it verbatim (DP1 re-review).
_JS_PLAIN_REF = re.compile(r'''^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$''')
_JS_CONST_REQUIRE = re.compile(
    r'''\b(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*["'](?!\.{1,2}/|/)([^"']+)["']\s*\)''')
_JS_CONST_DESTRUCTURE_REQUIRE = re.compile(
    r'''\b(?:const|let|var)\s*\{\s*([^}]+?)\s*\}\s*=\s*require\(\s*["'](?!\.{1,2}/|/)([^"']+)["']\s*\)''')
_JS_IMPORT_DEFAULT = re.compile(r'''\bimport\s+(\w+)\s+from\s+["'](?!\.{1,2}/|/)([^"']+)["']''')
_JS_IMPORT_NAMED = re.compile(
    r'''\bimport\s*\{\s*([^}]+?)\s*\}\s*from\s+["'](?!\.{1,2}/|/)([^"']+)["']''')
_JS_IMPORT_STAR = re.compile(r'''\bimport\s*\*\s*as\s+(\w+)\s+from\s+["'](?!\.{1,2}/|/)([^"']+)["']''')
_JS_EXPORT_FROM_BARE = re.compile(
    r'''export\s*(?:\*|\{[^}]*\})\s*from\s*["'](?!\.{1,2}/|/)([^"']+)["']''')
# module.exports = <expr> / export default <expr>: captures either a `{...}` object literal
# (non-greedy to its first close-brace — bounded, won't run away across a large file) or a plain
# expression up to the statement end.
_JS_EXPORT_ASSIGN = re.compile(r'''(?:module\.exports\s*=|export\s+default)\s*(\{[\s\S]*?\}|[^;\n]+)''')
_JS_EXPORT_NAMED = re.compile(r'''export\s*\{\s*([^}]+?)\s*\}(?!\s*from)''')

# Shape 2 (manifest/command delegation): the run target points at an external package with no
# local source implementing it.
_DOCKER_CMD_PY_MODULE = re.compile(r'''CMD\s*\[[^\]]*"python3?"\s*,\s*"-m"\s*,\s*"([\w.\-]+)"''')
_DOCKER_CMD_NPX = re.compile(r'''CMD\s*\[[^\]]*"npx"\s*,\s*(?:"-y"\s*,\s*)?"([@\w][\w@/.\-]*)"''')
_SMITHERY_NPX = re.compile(r'''npx\s+(?:-y\s+)?([@\w][\w@/.\-]*)''')

# Shape 3 (Go): blank import registers side effects in the imported package's init(). Matched at
# line-level (not anchored to the literal `import` keyword) so it fires both for a single-line
# `import _ "pkg"` and for a blank-import line inside a grouped `import (...)` block.
_GO_BLANK_IMPORT = re.compile(r'^[ \t]*_\s+"([^"]+)"', re.M)


def _js_bound_symbols(src: str) -> dict[str, str]:
    """symbol -> bare package it was bound from (require/import), across CJS + ESM forms."""
    binds: dict[str, str] = {}
    for name, pkg in _JS_CONST_REQUIRE.findall(src):
        binds[name] = pkg
    for names, pkg in _JS_CONST_DESTRUCTURE_REQUIRE.findall(src):
        for n in names.split(","):
            n = n.strip().split(":")[0].strip()
            if n:
                binds[n] = pkg
    for name, pkg in _JS_IMPORT_DEFAULT.findall(src):
        binds[name] = pkg
    for names, pkg in _JS_IMPORT_NAMED.findall(src):
        for n in names.split(","):
            n = n.strip().split(" as ")[-1].strip()
            if n:
                binds[n] = pkg
    for name, pkg in _JS_IMPORT_STAR.findall(src):
        binds[name] = pkg
    return binds


def _js_object_export_pkg(expr: str, binds: dict[str, str]) -> str | None:
    """Object-literal export form of shape 1: only a DISTINCTIVE server-entry-point key
    (`_JS_SERVER_KEYS` — `createConnection`/`createServer`) wired to a value that IS the bare
    import (verbatim, or a plain member-access chain off a symbol bound from one) counts as "the
    entire module export IS the bare import" (spec §4 naming discipline). A property bag exporting
    an unrelated symbol under a non-server key (`{logger: pino}`, `{db: x}`, `{utils: y}`) does NOT
    qualify — that's a dependency reference, not server delegation, so it must not even MATCH
    (never mind naming). Nor does a call-wrapped/transformed value under a recognized key
    (`{createConnection: wrap(require('pg'))}`) — genuine delegation re-exports the import
    verbatim, it doesn't pass it through another function first (DP1 re-review — FP fix)."""
    for key in _JS_SERVER_KEYS:
        m = re.search(rf'''["']?\b{re.escape(key)}\b["']?\s*:\s*([^,}}]+)''', expr)
        if not m:
            continue
        value = m.group(1).strip()
        req = _JS_BARE_REQUIRE.fullmatch(value)
        if req:
            return req.group(1)
        if _JS_PLAIN_REF.match(value):
            head = value.split(".", 1)[0]
            if head in binds:
                return binds[head]
    return None


def _js_source_reexport(src: str) -> str | None:
    """Shape 1 (JS/TS): the ENTIRE module export IS a bare-import symbol — directly via
    `module.exports = require('pkg')` / `export … from 'pkg'`, via a bare identifier bound from a
    bare import (`const x = require('pkg'); module.exports = x;`), or via an object literal wired
    through a recognized server-entry-point key (e.g. playwright's
    `{createConnection: tools.createConnection}` off a `coreBundle` import — retained as ONE
    recognized wiring, not a special-cased path: `_js_object_export_pkg` finds `tools` referenced
    under the `createConnection` key, so playwright needs no bespoke token). A property bag under
    an unrelated key does NOT qualify (see `_js_object_export_pkg`) — that is a dependency
    reference (e.g. a logger), not delegation. One-hop local-barrel indirection
    (`export … from './x'`) is not special-cased either — `joined` already merges every fetched
    entrypoint file, so a barrel and the file it points at land in the same search space for
    free."""
    m = _JS_EXPORT_FROM_BARE.search(src)
    if m:
        return m.group(1)
    binds = _js_bound_symbols(src)
    for m in _JS_EXPORT_ASSIGN.finditer(src):
        expr = m.group(1).strip()
        if expr.startswith("{"):
            pkg = _js_object_export_pkg(expr, binds)
            if pkg:
                return pkg
            continue
        bare = expr.rstrip(";").strip()
        req = _JS_BARE_REQUIRE.fullmatch(bare)
        if req:
            return req.group(1)
        if bare in binds:
            return binds[bare]
    for m in _JS_EXPORT_NAMED.finditer(src):
        for n in m.group(1).split(","):
            n = n.strip()
            if not n:
                continue
            parts = n.split(" as ")
            local, exported = parts[0].strip(), parts[-1].strip()
            if exported in _JS_SERVER_KEYS and local in binds:
                return binds[local]
    return None


def _pyproject_script_pkg(py: str) -> str | None:
    """`[project.scripts]`/`[project.gui-scripts]` target `pkg.__main__[:func]` -> pkg."""
    in_scripts = False
    for line in (py or "").splitlines():
        s = line.strip()
        if s.startswith("["):
            in_scripts = s in ("[project.scripts]", "[project.gui-scripts]")
            continue
        if in_scripts and "=" in s:
            target = s.split("=", 1)[1].strip().strip("\"'")
            mod = target.split(":", 1)[0]
            if mod.endswith(".__main__"):
                return mod[: -len(".__main__")]
    return None


def _norm_dep(s: str) -> str:
    return s.lower().replace("_", "-")


def _has_local_source(content: dict, pkg: str) -> bool:
    """True when the repo itself appears to contain pkg's source — i.e. this is normal packaging
    (an own package delegating to its own __main__/CMD), NOT a relocation to elsewhere."""
    stems = {pkg.lower(), pkg.lower().replace("-", "_"), pkg.lower().replace("_", "-"),
             pkg.lower().split("/")[-1].lstrip("@")}
    for p in content:
        base = p.split("/", 1)[0].lower()
        if base in stems or p.lower() in {f"{s}.py" for s in stems} | {f"{s}.js" for s in stems}:
            return True
    return False


def _is_generic_launcher(pkg: str) -> bool:
    """A generic WSGI/ASGI launcher/runner (gunicorn/uvicorn/hypercorn/waitress/celery/flask/
    fastapi) is NEVER the tool source, even when it's a declared dependency with no vendored local
    source under its own name — it always fronts a LOCAL app (`gunicorn app:app`, `python -m
    gunicorn` next to an `app.py`). Total command delegation (spec §4 shape 2 naming discipline)
    excludes these unconditionally."""
    leaf = pkg.lower().split("/")[-1].split(".")[0]
    return _norm_dep(leaf) in _GENERIC_LAUNCHERS


def _manifest_command_delegation(content: dict, deps: set[str]) -> str | None:
    """Shape 2: the run target (console-script / Docker CMD / smithery startCommand) points at
    an external package with no local source implementing it — and isn't a generic launcher
    fronting local source (`_is_generic_launcher`)."""
    pkg = _pyproject_script_pkg(content.get("pyproject.toml", ""))
    if (pkg and not _is_generic_launcher(pkg) and _norm_dep(pkg) in deps
            and not _has_local_source(content, pkg)):
        return pkg
    docker = content.get("Dockerfile", "")
    for rx in (_DOCKER_CMD_PY_MODULE, _DOCKER_CMD_NPX):
        m = rx.search(docker)
        if m and not _is_generic_launcher(m.group(1)) and not _has_local_source(content, m.group(1)):
            return m.group(1)
    smithery = content.get("smithery.yaml", "")
    if "startCommand" in smithery:
        m = _SMITHERY_NPX.search(smithery)
        if m and not _is_generic_launcher(m.group(1)) and not _has_local_source(content, m.group(1)):
            return m.group(1)
    return None


def _go_blank_import(content: dict, joined: str) -> str | None:
    """Shape 3: `import _ "module/path"` (side-effect registration) with a thin local main — a
    substantial local main.go (>40 non-blank lines across .go entrypoints) suggests real logic
    our scanner just missed, not a genuine relocation, so we stay silent (generic reason) rather
    than overclaim. Naming discipline (spec §4 rule 4): only fire when the imported path is
    plausibly server/tool-related — a driver/codec (`lib/pq`, `image/png`) is not, so a blank
    import of one must not be read as server delegation at all."""
    m = _GO_BLANK_IMPORT.search(joined)
    if not m:
        return None
    path = m.group(1)
    tokens = re.split(r"[/\-_.]", path.lower())
    if not any(k in t for t in tokens for k in _GO_PLAUSIBLE_TOKENS):
        return None
    go_lines = [ln for p, src in content.items() if p.endswith(".go")
                for ln in src.splitlines() if ln.strip()]
    if len(go_lines) > 40:
        return None
    return path


def _pkg_name(spec: str) -> str:
    """Normalize an import/require specifier to its package identity: npm scoped (@scope/name)
    keeps 2 segments; npm subpath/py dotted takes the leading token; a domain-qualified Go module
    path (a '.' in the first '/'-segment) is kept whole since its identity spans segments."""
    spec = spec.strip()
    if spec.startswith("@"):
        parts = spec.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else spec
    first = spec.split("/")[0]
    if "." in first and "/" in spec:
        return spec
    return first.split(".")[0]


def _is_denylisted(pkg: str) -> bool:
    """Denylist the framework you're built ON (spec §4): re-exporting your own MCP SDK/transport
    is NOT relocation. Matched per path segment so both npm scopes (@modelcontextprotocol/sdk)
    and Go module paths (.../mark3labs/mcp-go/server) are caught."""
    segs = [s.lstrip("@").lower() for s in pkg.split("/") if s]
    return any(s in _MCP_FRAMEWORK_DENY for s in segs)


def _plausible_name(pkg: str, deps: set[str]) -> str | None:
    """Naming discipline (spec §4): only name pkg when it's plausibly the tool source — its name
    contains mcp/server/tool, or it's a declared dependency (pkg is already denylist-cleared by
    the caller). Otherwise the caller emits the un-named reason (matched, but nothing named)."""
    leaf = pkg.lower().split("/")[-1]
    if any(k in leaf for k in ("mcp", "server", "tool")):
        return pkg
    if _norm_dep(leaf) in deps or _norm_dep(pkg.lstrip("@")) in deps or pkg.lower() in deps:
        return pkg
    return None


def _external_delegation(content: dict, joined: str, deps: set[str]) -> tuple[bool, str | None]:
    """Part B (spec §4): does the entrypoint delegate its server to an EXTERNAL package (0 local
    tools, not capped)? Tries all three shapes (any one match wins). Returns (matched, pkg) —
    pkg is None when matched but naming discipline can't plausibly attribute a tool source."""
    pkg = (_js_source_reexport(joined)
           or _manifest_command_delegation(content, deps)
           or _go_blank_import(content, joined))
    if not pkg:
        return False, None
    pkg = _pkg_name(pkg)
    if _is_denylisted(pkg):
        return False, None
    return True, _plausible_name(pkg, deps)


def _cs_wire(method: str) -> str:
    """SDK-default method→tool-name transform: strip a trailing Async, then SnakeCaseLower."""
    if method.endswith("Async"):
        method = method[:-5]
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', method)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s)
    return s.lower()


def _findall_names(rx, src):
    """Yield the non-empty capture group per match (handles multi-alternative patterns)."""
    for m in rx.finditer(src):
        g = next((x for x in m.groups() if x), None)
        if g:
            yield g


def _next_def_names(src, attr_rx, def_rx, transform=None):
    """For each attribute/annotation lacking an explicit name=, capture the next fn/method name."""
    for m in attr_rx.finditer(src):
        d = def_rx.search(src, m.end())
        if d:
            yield transform(d.group(1)) if transform else d.group(1)


def _entrypoints(content):
    return {p: t for p, t in content.items()
            if p.endswith((".ts", ".js", ".mjs", ".py", ".go", ".rs", ".cs", ".java", ".kt"))}


def _tool_names(entry: dict[str, str]) -> list[dict]:
    tools: list[dict] = []
    seen: set[str] = set()

    def add(name, path):
        if name and name not in seen:
            seen.add(name)
            tools.append({"name": name, "evidence": {"path": path, "marker": "tool registration"}})

    for path, raw in entry.items():
        src = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", raw))  # strip /*…*/ then //,# comments
        if path.endswith(".go"):
            for rx in (_GO_TOOL_STRUCT, _GO_TOOL_CTOR):
                for name in rx.findall(src):
                    add(name, path)
        elif path.endswith(".rs"):
            for name in _RS_TOOL_NAME.findall(src):                          # #[tool(name="x")]
                add(name, path)
            for name in _next_def_names(src, _RS_TOOL_ATTR, _RS_FN):         # #[tool(…)] → next fn
                add(name, path)
            for name in _findall_names(_RS_TOOL_LIT, src):                   # Tool::new("x") / Tool{name:"x"}
                add(name, path)
        elif path.endswith(".cs"):
            for name in _CS_TOOL_NAME.findall(src):                          # [McpServerTool(Name="x")]
                add(name, path)
            for name in _next_def_names(src, _CS_TOOL_ATTR, _CS_METHOD, transform=_cs_wire):  # → method, snake_cased
                add(name, path)
        elif path.endswith((".java", ".kt")):
            mcp = bool(_JAVA_MCP_CTX.search(src))
            for name in _findall_names(_JAVA_TOOL_LIT, src):                 # Tool("x")/builder("x")/addTool("x")
                add(name, path)
            if "Tool.builder" in src:
                for name in _JAVA_NAME_SETTER.findall(src):                  # .name("x") — only with a Tool builder
                    add(name, path)
            for name in _JAVA_MCPTOOL_NAME.findall(src):                     # @McpTool(name="x")
                add(name, path)
            for name in _next_def_names(src, _JAVA_MCPTOOL_ATTR, _JAVA_METHOD):  # @McpTool → next method
                add(name, path)
            if mcp:
                for name in _JAVA_TOOL_ANN_NAME.findall(src):               # @Tool(name="x") — MCP-context only
                    add(name, path)
        elif path.endswith(".py"):
            for m in _PY_TOOL_DECO.finditer(src):
                kw = _PY_NAME_KW.search(m.group(1) or "")        # prefer the registered name=…
                if kw:
                    add(kw.group(1), path)
                else:
                    d = _PY_DEF.search(src, m.end())             # else the next def (skip stacked decorators)
                    add(d.group(1) if d else None, path)
            for rx in (_PY_TOOL_CTOR, _PY_ADD_TOOL):             # low-level Tool(name=…) + add_tool(name=…)
                for name in rx.findall(src):
                    add(name, path)
        else:
            for rx in (_TS_TOOL, _TS_TOOL_OBJ, _TS_STATIC_TOOLNAME):  # string-first + object-arg + static field
                for name in rx.findall(src):
                    add(name, path)
    return sorted(tools, key=lambda t: t["name"])


def compose(content: dict[str, str], *, source_coverage_capped: bool = False,
            paths=(), meta=None) -> dict:
    entry = _entrypoints(content)
    joined = "\n".join(entry.values())

    transport = unknown("no transport declared")
    for name, rx in _TRANSPORT:
        if rx.search(joined) or rx.search(content.get("smithery.yaml", "")):
            transport = fact(name, "entrypoint/manifest", [{"path": "entrypoint", "marker": name}])
            break

    auth = unknown("no auth mode declared")
    if "configSchema" in content.get("smithery.yaml", "") and _AUTH_KEYS.search(content.get("smithery.yaml", "")):
        auth = fact("api-key", "smithery configSchema",
                    [{"path": "smithery.yaml", "marker": "configSchema apiKey"}])
    elif _AUTH_KEYS.search(joined) or _AUTH_KEYS.search(content.get("Dockerfile", "")):
        auth = fact("api-key", "env/config", [{"path": "entrypoint", "marker": "auth token reference"}])

    tools = _tool_names(entry)
    # complete=True ONLY when we actually recovered tools AND saw no dynamic handler.
    # A dynamic ListTools/list_tools handler may return more than we statically scanned;
    # zero statically-found tools is never "complete". (Phase C adds source-coverage-cap honesty.)
    has_dynamic = "ListToolsRequestSchema" in joined or "list_tools" in joined
    if source_coverage_capped:
        complete, reason = False, "source coverage capped/truncated — tool list not fully scanned"
    elif not tools:
        if _REMOTE_PROXY.search(joined) and _REMOTE_URL.search(joined):
            complete, reason = False, ("dynamic remote-proxy — tools defined on a remote MCP "
                                       "server, not statically enumerable")
        else:
            # deferred import: classify_agent imports FROM compose_mcp (_BLOCK_COMMENT etc.) —
            # a top-level import here would be circular.
            from .classify_agent import _all_deps
            matched, pkg = _external_delegation(content, joined, _all_deps(content))
            if matched and pkg:
                complete, reason = False, (f"tool source defined in external package '{pkg}' "
                                            "— not present in this repo")
            elif matched:
                complete, reason = False, ("tools re-exported from an external package — "
                                           "not present in this repo")
            else:
                complete, reason = False, "no tool registrations statically found"
    elif has_dynamic:
        complete, reason = False, "dynamic tool list handler present"
    else:
        complete, reason = True, None
    return {"transport": transport, "auth": auth, "tools": tools,
            "tools_complete": complete, "tools_incomplete_reason": reason}


def fingerprint_facts(match: dict, composition: dict) -> dict:
    """Deterministic per-type fingerprint subset (R4: sorted leaves + marker_tier)."""
    return {"marker_tier": match["marker_tier"],
            "transport": composition["transport"].get("value"),
            "auth": composition["auth"].get("value"),
            "tool_names": sorted(t["name"] for t in composition["tools"])}
