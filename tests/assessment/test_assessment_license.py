from assessor.assessment.license import detect, _normalize

MIT = """MIT License

Copyright (c) 2021 Some Person

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def test_mit_matches_despite_copyright_line():
    r = detect({"LICENSE": MIT}, fallback_license="MIT")
    assert r["spdx"]["value"] == "MIT" and r["spdx"]["source"].startswith("LICENSE")


def test_unmatched_falls_back_to_github_string():
    r = detect({"LICENSE": "totally custom terms blah blah blah"}, fallback_license="Apache-2.0")
    assert r["spdx"]["value"] == "Apache-2.0" and "fallback" in r["spdx"]["source"]


def test_normalize_strips_copyright():
    assert "some person" not in _normalize(MIT)


def test_detect_prefers_repo_spdx_over_templates():
    r = detect({}, None, repo_spdx="Apache-2.0")                  # no LICENSE text, no fallback
    assert r["spdx"]["value"] == "Apache-2.0" and "repo object" in r["spdx"]["source"]
