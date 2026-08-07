"""SPDX detection via token-similarity (licensee/ScanCode approach).

Exact normalized-hash can't match MIT/BSD/Apache because the copyright line
varies per repo, so we strip variable regions then compare with Sørensen–Dice
over token bigrams. Unmatched → the GitHub-API license string at low confidence.
"""
from __future__ import annotations

import re

from .shapes import fact, unknown

_THRESHOLD = 0.9
_COPYRIGHT = re.compile(r"^\s*copyright\b.*$", re.I | re.M)
_PLACEHOLDER = re.compile(r"\[(?:yyyy|year|name|fullname|author)\]|<(?:year|holder|name)>", re.I)
_WS = re.compile(r"\s+")

# Canonical (normalized-at-load) template bodies for the common licenses.
_TEMPLATES_RAW = {
    "MIT": """Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in the
Software without restriction, including without limitation the rights to use, copy,
modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so, subject to the
following conditions: The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software. THE SOFTWARE IS
PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.""",
    "ISC": """Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice and
this permission notice appear in all copies. THE SOFTWARE IS PROVIDED "AS IS" AND THE
AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE.""",
}


def _normalize(text: str) -> str:
    text = _COPYRIGHT.sub("", text)
    text = _PLACEHOLDER.sub("", text)
    return _WS.sub(" ", text.lower()).strip()


def _bigrams(s: str) -> set[str]:
    toks = s.split(" ")
    return {f"{toks[i]} {toks[i + 1]}" for i in range(len(toks) - 1)} if len(toks) > 1 else set(toks)


def _dice(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


_TEMPLATES = {spdx: _normalize(body) for spdx, body in _TEMPLATES_RAW.items()}


def _license_text(content: dict[str, str]) -> tuple[str, str] | None:
    for path in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE.rst"):
        if content.get(path):
            return path, content[path]
    return None


def detect(content: dict[str, str], fallback_license: str | None, *,
           dep_licenses: list[str] | None = None, repo_spdx: str | None = None) -> dict:
    caveat = None
    for lic in (dep_licenses or []):
        if any(k in lic.upper() for k in ("GPL", "AGPL", "LGPL")):
            caveat = "copyleft transitive dependency"
            break

    # The GitHub repo object's SPDX id is authoritative and covers licenses (Apache-2.0/BSD/…)
    # the template-similarity path can't self-verify — prefer it when present.
    if repo_spdx:
        return {"spdx": fact(repo_spdx, "repo object (github spdx_id)",
                             [{"path": "repo", "marker": f"spdx {repo_spdx}"}]), "caveat": caveat}

    found = _license_text(content)
    if found is not None:
        path, raw = found
        norm = _normalize(raw)
        best_id, best = None, 0.0
        for spdx, tmpl in _TEMPLATES.items():
            score = _dice(norm, tmpl)
            if score > best:
                best_id, best = spdx, score
        if best_id is not None and best >= _THRESHOLD:
            return {"spdx": fact(best_id, f"{path} (similarity {best:.2f})",
                                 [{"path": path, "marker": f"spdx {best_id}"}]), "caveat": caveat}

    if fallback_license:
        return {"spdx": fact(fallback_license, "fallback: GitHub API license string",
                             [{"path": "repo_metrics", "marker": "github license"}]), "caveat": caveat}
    return {"spdx": unknown("no license text matched and no GitHub license string"), "caveat": caveat}
