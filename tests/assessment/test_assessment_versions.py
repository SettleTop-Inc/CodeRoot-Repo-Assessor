from assessor.assessment import versions


def test_latest_release_and_count_from_collected():
    rels = [{"tag": "v1.0", "name": "1.0", "published_at": "2024-01-01T00:00:00Z", "is_prerelease": False},
            {"tag": "v1.1", "name": "1.1", "published_at": "2024-06-01T00:00:00Z", "is_prerelease": False}]
    v = versions.build(rels, "sha1")
    assert v["latest_release"]["value"]["tag"] == "v1.1"    # newest by published_at
    assert v["release_count"]["value"] == 2
    assert v["assessed_commit"]["value"] == "sha1"


def test_no_releases_is_an_explicit_gap():
    v = versions.build([], "sha1")
    assert v["latest_release"]["known_unknown"] and v["release_count"]["value"] == 0
