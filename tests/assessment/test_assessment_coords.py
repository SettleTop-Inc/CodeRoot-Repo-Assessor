import json

from assessor.assessment.coords import extract


def test_npm_docker_and_git():
    content = {
        "package.json": json.dumps({"name": "my-mcp", "version": "1.2.3"}),
        "Dockerfile": "FROM node:20\n",
    }
    coords = extract(content, "https://github.com/o/my-mcp")
    kinds = {c["kind"]: c["ref"] for c in coords}
    assert "npx my-mcp" in kinds["npm"]
    assert kinds["docker"]
    assert kinds["git"] == "https://github.com/o/my-mcp"


def test_pip_from_pyproject():
    content = {"pyproject.toml": '[project]\nname = "mcp-thing"\n'}
    kinds = {c["kind"]: c["ref"] for c in extract(content, "https://github.com/o/mcp-thing")}
    assert "mcp-thing" in kinds["pip"]


def test_absent_coords_omitted():
    kinds = {c["kind"] for c in extract({}, "https://github.com/o/n")}
    assert kinds == {"git"}  # only git when nothing else declared


def test_endpoint_picks_mcp_url_not_first_docs_url():
    content = {"server.json": '{"documentation": "https://docs.example.com", "url": "https://api.example.com/mcp"}'}
    kinds = {c["kind"]: c["ref"] for c in extract(content, "https://github.com/o/n")}
    assert kinds["endpoint"] == "https://api.example.com/mcp"   # not the docs URL that appears first


def test_malformed_package_json_does_not_crash():
    # valid JSON but wrong shape (top-level array / deps as a list) must not raise (best-effort)
    from assessor.assessment.classify_mcp import classify
    extract({"package.json": '["x"]'}, "https://github.com/o/n")
    assert classify({"package.json": '{"dependencies": ["x"]}'}) is None
    assert classify({"package.json": '["array top level"]'}) is None
