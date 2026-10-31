from skillscout import core

from test_skillscout import PROFILE, FakeGitHub, link, make_repo, skill_md


def test_include_keeps_a_repo_below_min_stars_without_trust(tmp_path):
    paths = core.Paths(tmp_path / "state", tmp_path / "skills", tmp_path / "plugins")
    paths.skills.mkdir()
    new = tmp_path / "src" / "new"
    make_repo(new, {"LICENSE": "MIT", "skills/think/SKILL.md": skill_md("think", "Mental models for a GST eval decision")})
    gh = FakeGitHub({"me/new": (new, 0)})
    paths.cache.mkdir(parents=True)
    link(paths.cache / "me__new", new)

    assert not core.crawl(PROFILE["search"], paths, fetch=gh, full=True)

    s = {**PROFILE["search"], "include": ["me/new"]}
    records = core.crawl(s, paths, fetch=gh, full=True)
    assert [r["name"] for r in records] == ["think"]
    cands = core.discover({**PROFILE, "search": s}, paths, fetch=gh)
    assert cands and not any(c["trusted"] for c in cands)
