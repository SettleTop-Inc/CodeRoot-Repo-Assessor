"""Export CodeRoot assessment inputs + expected outputs for parity testing.

WHERE TO RUN THIS: from the CodeRoot OSS service's own repository/environment
(the one with `sqlalchemy` and the `coderoot_oss` package installed, and a
live `DATABASE_URL` pointed at its Postgres). It does NOT run inside
CodeRoot-Repo-Assessor's virtualenv — this repo deliberately does not depend
on sqlalchemy or coderoot_oss, so importing this file here will fail. That
failure is expected, not a bug to chase: this script is a developer tool that
ships in this repo only so the corpus shape it writes stays version-controlled
next to the harness (tests/test_parity.py) that reads it.

Usage (from the CodeRoot OSS service directory, with its env loaded):
    python export_corpus.py <output_dir>

Each file holds {"subject", "snapshot", "metrics", "expected"} where "expected"
is the record CodeRoot's own pipeline produced for that repo. The key names in
both the "subject"/"snapshot" objects and the assessor.ports.source.Subject /
Snapshot TypedDicts must match exactly — see tests/test_parity.py, which reads
this exact shape.
"""
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
import os

_SQL = text("""
SELECT r.id, r.host, r.owner, r.name, a.commit_sha, a.description, a.homepage,
       a.topics, a.license_spdx, a.source_coverage_capped, a.tree_paths,
       a.tree_capped, a.marker_hits, ra.assessment, ra.asset_types,
       ra.content_fingerprint, ra.subdir
  FROM coderoot.repo_assessment ra
  JOIN coderoot.repositories r ON r.id = ra.repo_id
  JOIN coderoot.repo_acquisition a ON a.repo_id = ra.repo_id
 WHERE ra.subdir = ''
""")


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    engine = create_engine(os.environ["DATABASE_URL"])
    from coderoot_oss.artifacts import get_store
    from sqlalchemy.orm import Session
    store = get_store()
    with Session(engine) as session:
        for row in session.execute(_SQL).mappings():
            files = {}
            for cr in session.execute(text(
                "SELECT path, blob_uri FROM coderoot.repo_content "
                "WHERE repo_id=:r AND commit_sha=:s"),
                    {"r": row["id"], "s": row["commit_sha"]}).mappings():
                files[cr["path"]] = store.get_bytes(cr["blob_uri"]).decode("utf-8")
            metrics = session.execute(text(
                "SELECT license, releases FROM coderoot.repo_metrics WHERE repo_id=:r"),
                {"r": row["id"]}).mappings().one_or_none()
            payload = {
                "subject": {
                    "repo_url": f"https://{row['host']}/{row['owner']}/{row['name']}",
                    "subject_key": str(row["id"]),
                    "commit_sha": row["commit_sha"], "subdir": row["subdir"]},
                "snapshot": {
                    "commit_sha": row["commit_sha"],
                    "metadata": {"description": row["description"],
                                 "homepage": row["homepage"],
                                 "topics": row["topics"] or [],
                                 "license_spdx": row["license_spdx"]},
                    "tree_paths": list(row["tree_paths"] or []),
                    "tree_capped": bool(row["tree_capped"]),
                    "marker_hits": list(row["marker_hits"] or []),
                    "files": files,
                    "source_coverage_capped": bool(row["source_coverage_capped"]),
                    "allowlist_version": 7},
                "metrics": ({"license": metrics["license"],
                             "releases": metrics["releases"]} if metrics else None),
                "expected": {"asset_types": list(row["asset_types"] or []),
                             "content_fingerprint": row["content_fingerprint"]},
            }
            (out / f"{row['owner']}__{row['name']}.json").write_text(
                json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
