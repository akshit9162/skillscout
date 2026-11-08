"""The install boundary (install.py), the index download, and the command line end to end."""

import gzip
import json
import os
import subprocess
import tomllib
import urllib.error

import pytest

from skillscout import cli, core, index, install, store

from test_skillscout import PROFILE, can_symlink, env, make_repo, skill_md  # noqa: F401  (env is a fixture)


def commit_all(src, msg="u"):
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg], cwd=src, check=True)


def installed(env):
    paths, gh = env
    dest, status = install.approve(core.find(core.discover(PROFILE, paths, fetch=gh), "gst-eval"), paths)
    return paths, gh, dest, status


# --- install boundary -------------------------------------------------------------------------------

def test_updates_names_an_upstream_symlink_but_never_reads_it(env, tmp_path):
    paths, gh, dest, _ = installed(env)
    if not can_symlink(tmp_path):
        pytest.skip("no symlink rights")
    secret = tmp_path / "id_rsa"
    secret.write_text("-----BEGIN FAKE KEY-----\n", encoding="utf-8")
    src = gh.repos["me/good"][0]
    (src / "skills/gst-eval/notes.md").symlink_to(secret)
    commit_all(src)
    [(_, _, diff)] = install.updates(paths, fetch=gh)
    assert "BEGIN FAKE KEY" not in diff and "notes.md: symlink to" in diff


def test_approve_update_installs_only_the_commit_updates_showed(env):
    paths, gh, dest, _ = installed(env)
    src = gh.repos["me/good"][0]
    (src / "skills/gst-eval/SKILL.md").write_text(skill_md("gst-eval", "v2 of the GST RL environment eval"), encoding="utf-8")
    commit_all(src)
    [(cid, shown, _)] = install.updates(paths, fetch=gh)
    assert install.reviewed(paths)[cid] == shown
    (src / "skills/gst-eval/SKILL.md").write_text(skill_md("gst-eval", "v3, pushed after the review"), encoding="utf-8")
    commit_all(src)
    c = core.find(core._read(paths.candidates, []), "gst-eval")
    with pytest.raises(SystemExit, match="upstream moved"):
        install.approve(c, paths, replace=True, fetch=gh, expect=shown)
    assert "v2" not in (dest / "SKILL.md").read_text(encoding="utf-8")


def test_approve_again_without_changes_says_current_and_existing_points_to_update(env):
    paths, gh, dest, status = installed(env)
    assert status == "installed"
    c = core.find(core._read(paths.candidates, []), "gst-eval")
    with pytest.raises(SystemExit, match="--update"):
        install.approve(c, paths)
    assert install.approve(c, paths, replace=True) == (dest, "current")


def test_approve_elsewhere_refuses_instead_of_orphaning_the_first_copy(env, tmp_path):
    paths, gh, dest, _ = installed(env)
    c = core.find(core._read(paths.candidates, []), "gst-eval")
    paths.skills = tmp_path / "other-agent"
    with pytest.raises(SystemExit, match="already installed at"):
        install.approve(c, paths)
    assert dest.exists()


def test_remove_keeps_an_edited_copy_unless_forced(env):
    paths, gh, dest, _ = installed(env)
    (dest / "SKILL.md").write_text("my own edits", encoding="utf-8")
    with pytest.raises(SystemExit, match="edited since install"):
        install.remove("gst-eval", paths)                   # a bare name works, as in show/approve
    assert dest.exists()
    assert install.remove("gst-eval", paths, force=True) == ("me/good:gst-eval", dest) and not dest.exists()


def test_ambiguous_names_list_the_matches():
    with pytest.raises(SystemExit) as e:
        install.pick(["a/x:lint", "b/y:lint", "c/z:other"], "lint")
    assert "a/x:lint" in str(e.value) and "b/y:lint" in str(e.value)


# --- files on disk ----------------------------------------------------------------------------------

def test_writing_through_a_symlinked_settings_file_keeps_the_link(tmp_path):
    if not can_symlink(tmp_path):
        pytest.skip("no symlink rights")
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text("{}", encoding="utf-8")
    real.chmod(0o600)
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    store.write_json(link, {"b": 1, "a": 2}, sort_keys=False)
    assert link.is_symlink() and json.loads(real.read_text(encoding="utf-8")) == {"b": 1, "a": 2}
    if os.name != "nt":                                     # Windows has no Unix permission bits
        assert real.stat().st_mode & 0o777 == 0o600


def test_persona_and_init_text_with_emoji_and_newlines_stay_valid_toml(tmp_path, monkeypatch):
    from skillscout import persona
    prof = tmp_path / "profile.toml"
    prof.write_text('[keywords]\n"x" = 1\n', encoding="utf-8")
    persona.add(prof, "launch", "ship the launch 🚀 fast\nreally", {"launch 🚀": 3, 'say "hi"': 2}, "agent\u007f")
    p = tomllib.loads(prof.read_text(encoding="utf-8"))["personas"]["launch"]
    assert p["description"] == "ship the launch 🚀 fast\nreally" and p["keywords"]["launch 🚀"] == 3
    with pytest.raises(SystemExit, match="valid TOML"):
        store.write_toml(prof, "[broken")
    assert tomllib.loads(prof.read_text(encoding="utf-8"))        # the good file is untouched


# --- index download ---------------------------------------------------------------------------------

def gz(path, built="2026-11-08T00:00:00+00:00"):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"version": index.VERSION, "built_at": built, "rules": "r", "skills": []}, f)
    return path.read_bytes()


class Response:
    def __init__(self, body, etag=None):
        self.body, self.headers = body, {"ETag": etag} if etag else {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_blocked_release_download_falls_back_to_the_mirror(tmp_path, monkeypatch):
    body, tried = gz(tmp_path / "src.gz"), []

    def urlopen(req, timeout):
        tried.append(req.full_url)
        if "releases/download" in req.full_url:
            raise urllib.error.URLError(ConnectionResetError(54, "Connection reset by peer"))
        return Response(body)

    monkeypatch.setattr(index.urllib.request, "urlopen", urlopen)
    data = index.fetch(index.URL, tmp_path / "index.json.gz")
    assert data["built_at"].startswith("2026-11-08") and tried == [index.URL, index.MIRRORS[0]]
    monkeypatch.setattr(index.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(urllib.error.URLError("down")))
    with pytest.raises(index.Unavailable, match="couldn't download"):
        index.fetch(index.URL, tmp_path / "missing.json.gz")


def test_turning_on_verify_checks_a_copy_cached_before(tmp_path, monkeypatch):
    cache = tmp_path / "index.json.gz"
    gz(cache)
    cache.with_suffix(".etag").write_text('"abc"', encoding="utf-8")
    not_modified = urllib.error.HTTPError(index.URL, 304, "Not Modified", {}, None)
    monkeypatch.setattr(index.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(not_modified))
    checked = []
    monkeypatch.setattr(index, "verify", lambda path, url: checked.append(path))
    index.fetch(index.URL, cache, check=False)
    assert checked == []
    index.fetch(index.URL, cache, check=True)
    index.fetch(index.URL, cache, check=True)              # verified once; the mark covers these bytes
    assert checked == [cache]


# --- command line -----------------------------------------------------------------------------------

@pytest.fixture
def home(env, tmp_path, monkeypatch):
    paths, gh = env
    monkeypatch.setattr(cli, "USER_PROFILE", tmp_path / "profile.toml")
    monkeypatch.setattr(core, "Paths", lambda: paths)
    return paths, gh, tmp_path / "profile.toml"


def run(capsys, *argv):
    try:
        cli.main(list(argv))
    except SystemExit as e:
        if e.code not in (None, 0):
            out = capsys.readouterr()
            return 1, out.out + out.err + (e.code if isinstance(e.code, str) else "")
    out = capsys.readouterr()
    return 0, out.out + out.err


def test_cli_without_a_profile_says_to_init_instead_of_scoring_the_example(home, capsys):
    for argv in ([], ["discover"], ["list"], ["show", "x"]):
        code, out = run(capsys, *argv)
        assert code == 1 and "skillscout init --about" in out, argv
    code, out = run(capsys, "--version")
    assert code == 0 and out.startswith("skillscout ")
    code, out = run(capsys, "installed")
    assert code == 0 and "nothing pinned yet" in out


def test_cli_init_survives_an_unreachable_index_and_a_broken_profile_is_one_line(home, capsys, monkeypatch):
    paths, gh, prof = home
    monkeypatch.setattr(index.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(urllib.error.URLError("reset")))
    code, out = run(capsys, "init", "--about", "backend in Python, RL evals\nand Postgres 🐘")
    assert code == 0 and "your own words" in out
    kw = tomllib.loads(prof.read_text(encoding="utf-8"))["keywords"]
    assert {"backend", "python", "rl evals", "postgres 🐘"} <= set(kw)
    code, out = run(capsys, "init")
    assert code == 1 and "persona add" in out
    prof.write_text("[keywords\n", encoding="utf-8")
    code, out = run(capsys, "list")
    assert code == 1 and str(prof) in out and "Traceback" not in out


def test_cli_list_dedupes_copies_and_approve_survives_a_deleted_active_persona(home, capsys, tmp_path, monkeypatch):
    paths, gh, prof = home
    fork = tmp_path / "src" / "fork"
    make_repo(fork, {"LICENSE": "MIT", "s/SKILL.md": skill_md("gst-eval", "Build an RL environment eval for GST filing in Python")})
    gh.repos["them/fork"] = (fork, 50)
    if not can_symlink(tmp_path):
        pytest.skip("no symlink rights")
    (paths.cache / "them__fork").symlink_to(fork)
    prof.write_text(json_profile(), encoding="utf-8")
    core.discover(tomllib.loads(json_profile()), paths, fetch=gh)
    capsys.readouterr()
    code, out = run(capsys, "list")
    assert code == 0 and out.splitlines()[0].startswith("score") and "(+1 copy)" in out
    assert sum("gst-eval" in line for line in out.splitlines()[1:]) == 1
    (paths.state / "switch.json").write_text(json.dumps({"active": ["ghost"], "skills": {}, "plugins": {}}), encoding="utf-8")
    monkeypatch.setattr(cli, "github", gh)
    code, out = run(capsys, "approve", "me/good:gst-eval")
    assert code == 0 and out.startswith("installed me/good:gst-eval")
    code, out = run(capsys, "persona", "build", "ghost")
    assert code == 1 and "no personas yet" in out


def json_profile() -> str:
    """PROFILE as TOML text, for the commands that read a profile file."""
    lines = ["[search]", 'queries = ["topic:claude-skills"]', "per_query = 5", "min_stars = 10", "max_age_days = 365",
             "trusted = []", "[keywords]"] + [f"{store.toml_str(k)} = {v}" for k, v in PROFILE["keywords"].items()]
    return "\n".join(lines + ["[scoring]", "recommend_at = 0.3", "overlap_at = 0.35", "[index]", 'url = ""']) + "\n"


def test_persona_build_names_an_unknown_persona(home, capsys):
    paths, gh, prof = home
    prof.write_text(json_profile() + '[personas.brand]\ndescription = "b"\n[personas.brand.keywords]\n"ad copy" = 3\n',
                    encoding="utf-8")
    code, out = run(capsys, "persona", "build", "ghost")
    assert code == 1 and "unknown persona 'ghost'" in out and "brand" in out


def test_dedupe_counts_copies_once():
    rows = core.dedupe([{"id": "a:x", "name": "x", "description": "Same  thing"},
                        {"id": "b:x", "name": "X", "description": "same thing"},
                        {"id": "c:y", "name": "y", "description": "other"}])
    assert [(c["id"], n) for c, n in rows] == [("a:x", 1), ("c:y", 0)]
