"""Discovery, reporting, approval and update checks."""

import difflib
import hashlib
import json
import shutil
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import os

from . import github, index, persona
from .scan import HIGH, RULES, scan, worst
from .score import combined, conflicts, freshness, popularity, relevance
from .skill import find_skills, parse_frontmatter, safe_name

HOME = Path.home()
STATE = HOME / ".skillscout"
SKILLS_DIR = HOME / ".claude" / "skills"
PLUGIN_CACHE = HOME / ".claude" / "plugins" / "cache"


class Paths:
    def __init__(self, state: Path = STATE, skills: Path = SKILLS_DIR, plugins: Path = PLUGIN_CACHE):
        self.state, self.skills, self.plugins = state, skills, plugins
        claude = plugins.parent.parent                    # ~/.claude
        self.agents = claude / "agents"
        self.settings = claude / "settings.json"
        self.cache = state / "repos"
        self.candidates = state / "candidates.json"
        self.crawl = state / "crawl.json"     # the user-independent part of the last crawl (what the index publishes)
        self.index = state / "index.json.gz"  # the last shared index downloaded
        self.repos = state / "repos.json"     # each repo's metadata, push time, checkout and commit at the last run
        self.lock = state / "skills.lock.json"
        self.backups = state / "backups"     # the copy each install replaced, for `rollback`
        self.reports = state / "reports"


def load_profile(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _read(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data) -> None:
    """Write whole or not at all: a crash mid-write leaves the previous file, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def digest(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink()):
        h.update(str(f.relative_to(folder)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def installed_skills(paths: Paths) -> dict[str, str]:
    """name -> description for your skills and for skills of enabled plugins, which are named the way Claude
    Code names them: `<plugin>:<skill>`. The plugin cache is laid out <marketplace>/<plugin>/<version>/..."""
    try:
        enabled = {k.split("@")[0] for k, on in json.loads(paths.settings.read_text(encoding="utf-8"))
                   .get("enabledPlugins", {}).items() if on}
    except (OSError, ValueError):
        enabled = None                                    # no settings file: count every cached plugin
    out = {}
    for root, is_plugin in ((paths.skills, False), (paths.plugins, True)):
        if not root.exists():
            continue
        for md in root.rglob("SKILL.md"):
            if ".git" in md.parts or "node_modules" in md.parts:
                continue
            plugin = md.relative_to(root).parts[1] if is_plugin and len(md.relative_to(root).parts) > 2 else None
            if is_plugin and (plugin is None or (enabled is not None and plugin not in enabled)):
                continue
            meta = parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
            name = meta.get("name") or md.parent.name
            out[f"{plugin}:{name}" if plugin else name] = meta.get("description", "")
    return out


def follow_lists(repos: dict, s: dict, fetch=github, paths: "Paths | None" = None, seen: dict | None = None) -> dict:
    """Repos linked from curated lists: the profile's `lists` plus any found repo named awesome-*.
    Each link costs one API lookup, so at most `max_list_repos` are looked up per run: links never looked
    up go first, then the longest unchecked, taking turns across lists so each gets a share. Repos found
    through a list on an earlier run stay in, from the cache, until their turn comes round again.
    The same filters (stars, freshness, size) then apply as to search results."""
    if not s.get("follow_lists", True) or not hasattr(fetch, "readme"):
        return {}
    lists = set(s.get("lists", [])) | {n for n in repos if "awesome" in n.split("/")[-1].lower()}
    known = {n.lower() for n in repos}
    per_list, via = [], {}
    for lst in sorted(lists):
        try:
            text = fetch.readme(lst)
        except Exception as e:
            print(f"list {lst}: {e}")
            continue
        links = [n for n in github.linked_repos(text) if n.lower() not in known and n.lower() != lst.lower()]
        per_list.append([n for n in links if n not in via])
        for n in links:
            via.setdefault(n, lst)
    names = [n for group in _interleave(per_list) for n in group]
    state = paths.state / "lists.json" if paths else None
    checked = _read(state, {}) if state else {}
    names.sort(key=lambda n: checked.get(n, ""))       # never looked up ("") first, then the oldest
    cap = s.get("max_list_repos", 1500 if github.token() else 40)
    if fetch is github:                                 # never spend more lookups than the hour has left
        try:
            cap = min(cap, max(0, github._get("/rate_limit")["resources"]["core"]["remaining"] - 50))
        except Exception:
            pass
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def look(n):
        try:
            return {**fetch.repo(n), "via": via[n]}
        except Exception:                           # renamed, deleted or private: skip quietly
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [r for r in pool.map(look, names[:cap]) if r]
    if state:
        _write(state, {**checked, **{n: now for n in names[:cap]}})
    out = {r["full_name"]: r for r in found if r["full_name"].lower() not in known}
    carried = 0
    for name, e in (seen or {}).items():                # earlier list finds whose turn hasn't come
        if e["meta"].get("via") and name not in out and name.lower() not in known:
            out[name] = e["meta"]
            carried += 1
    if lists:
        left = max(0, len(names) - cap)
        print(f"followed {len(lists)} lists: {len(names)} linked repos, looked up {min(len(names), cap)}, "
              f"kept {carried} from earlier runs" + (f"; {left} wait for later runs" if left else ""))
    return out


def _interleave(groups: list[list[str]]) -> list[list[str]]:
    """Round-robin: one name from each list in turn, so a long list can't use up the lookups."""
    out, i = [], 0
    while any(i < len(g) for g in groups):
        out.append([g[i] for g in groups if i < len(g)])
        i += 1
    return out


def crawl(s: dict, paths: Paths, fetch=github, full: bool = False) -> list[dict]:
    """Search, clone and scan: the part that is the same for every user. Returns one record per skill and
    keeps them in crawl.json. Repos with no new pushes aren't fetched and unchanged skills aren't
    re-scanned, unless `full`."""
    seen = {} if full else _read(paths.repos, {})
    repos, failed = {}, False
    for q in s["queries"]:
        try:
            for r in fetch.search(q, s.get("per_query", 30)):
                repos.setdefault(r["full_name"], r)
        except Exception as e:                      # a failed search falls back to last run's repos below
            print(f"search {q!r} failed: {e}")
            failed = True
    for name in s.get("trusted", []):
        try:
            repos[name] = repos.get(name) or fetch.repo(name)
        except Exception as e:
            print(f"lookup {name} failed: {e}")
            failed = True
    repos.update(follow_lists(repos, s, fetch, paths, seen))
    if failed:
        for name, e in seen.items():
            repos.setdefault(name, e["meta"])

    now = datetime.now(timezone.utc)
    trusted = set(s.get("trusted", []))
    keep = [r for r in repos.values() if r["full_name"] in trusted or (
        not r["archived"] and r["stars"] >= s.get("min_stars", 0)
        and r.get("size_kb", 0) <= s.get("max_repo_mb", 300) * 1024
        and freshness(r["pushed_at"], now) > 1 - s.get("max_age_days", 365) / 365)]

    previous = {c["id"]: c for c in _read(paths.crawl, None) or _read(paths.candidates, [])}
    stale = set()           # repos whose refresh failed: don't record their push time, so the next run retries
    if not github.token():
        print("tip: set GITHUB_TOKEN or run `gh auth login` for higher GitHub API limits (5,000 requests an hour instead of 60)")

    def pull(r):
        last = seen.get(r["full_name"])
        if last and last["meta"]["pushed_at"] == r["pushed_at"] and (Path(last["root"]) / ".git").exists():
            return r, Path(last["root"]), last["commit"]      # nothing pushed since the last run: no fetch
        try:
            return r, *fetch.sync(r["full_name"], paths.cache)
        except github.Stale as e:
            print(f"{r['full_name']}: {e}")
            stale.add(r["full_name"])
            return r, e.root, e.commit
        except Exception as e:                      # one bad repo shouldn't stop the run
            print(f"skip {r['full_name']}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=s.get("workers", 16)) as pool:
        synced = [x for x in pool.map(pull, keep) if x]
    _write(paths.repos, {**seen, **{r["full_name"]: {"meta": r, "root": str(root), "commit": commit}
                                    for r, root, commit in synced if r["full_name"] not in stale}})
    print(f"fetched {sum(1 for r, *_ in synced if seen.get(r['full_name'], {}).get('meta', {}).get('pushed_at') != r['pushed_at'])} "
          f"of {len(synced)} repos; the rest had no new pushes")

    records, reused = [], 0
    for r, root, commit in synced:
        try:
            tree_of = github.trees(root)
        except Exception:
            tree_of = {}
        licensed = any((root / n).exists() for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"))
        names = set()
        for sk in find_skills(root):
            rel = sk.path.relative_to(root).as_posix()
            # owner/repo:name, or owner/repo:path/to/skill for a second skill with the same name
            cid = f"{r['full_name']}:{rel if sk.name in names else sk.name}"
            names.add(sk.name)
            tree = tree_of.get(rel)
            key = f"{RULES}:{tree}:{int(licensed)}" if tree else None
            old = previous.get(cid)
            if key and old and old.get("scan_key") == key and not full:     # same files, same rules: the old result stands
                flags, reused = old["flags"], reused + 1
            else:
                flags = [asdict(f) for f in scan(sk.path, root)]
            records.append({
                "id": cid, "repo": r["full_name"], "repo_id": r.get("id"), "name": sk.name, "description": sk.description,
                "path": rel, "commit": commit, "stars": r["stars"],
                "license": r["license"], "pushed_at": r["pushed_at"],
                "flags": flags, "risk": worst(flags), "scan_key": key,
            })
    print(f"scanned {len(records) - reused} skills, reused {reused} unchanged")
    _write(paths.crawl, records)
    return records


def score(records: list[dict], profile: dict, paths: Paths) -> list[dict]:
    """The personal part: relevance to this profile's keywords, conflicts with what's installed, what's new.
    Writes candidates.json and returns the candidates, best first."""
    now = datetime.now(timezone.utc)
    trusted = set(profile.get("search", {}).get("trusted", []))
    installed = installed_skills(paths)
    locked = _read(paths.lock, {})
    # A renamed repo keeps its GitHub id, so a skill installed under the old name still counts as installed
    pinned = {(e["repo_id"], e["path"]) for e in locked.values() if e.get("repo_id")}
    previous = {c["id"] for c in _read(paths.candidates, [])}
    candidates = []
    sets = persona.keyword_sets(profile)
    for r in records:
        text = f"{r['name']} {r['description']}"
        fit = {name: relevance(text, kw) for name, kw in sets.items()}
        rel = max((f[0] for f in fit.values()), default=0.0)
        hits = sorted({h for f in fit.values() for h in f[1]})
        candidates.append({
            **r, "trusted": r["repo"] in trusted, "relevance": round(rel, 3), "matched": hits,
            "personas": {name: round(f[0], 3) for name, f in fit.items() if name and f[0] > 0},
            "score": combined(rel, popularity(r["stars"]), freshness(r["pushed_at"], now), r["repo"] in trusted),
            "conflicts": conflicts(r["name"], r["description"], installed, profile["scoring"]["overlap_at"]),
            "installed": r["id"] in locked or (r.get("repo_id"), r["path"]) in pinned, "new": r["id"] not in previous,
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    _write(paths.candidates, candidates)
    return candidates


def discover(profile: dict, paths: Paths, fetch=github, full: bool = False) -> list[dict]:
    """Crawl GitHub on this machine, then score: the `--local` path."""
    return score(crawl(profile["search"], paths, fetch, full), profile, paths)


def discover_from_index(profile: dict, paths: Paths, url: str = index.URL, check: bool = False) -> list[dict]:
    """Score the shared weekly index instead of crawling: one download, no GitHub API calls."""
    idx = index.fetch(url, paths.index, check)
    print(f"index built {idx['built_at']}: {len(idx['skills'])} skills from {len({s['repo'] for s in idx['skills']})} repos")
    return score(idx["skills"], profile, paths)


def ensure_local(c: dict, paths: Paths, fetch=github) -> dict:
    """Fetch the skill and scan it again here, so nothing about it is taken from the index on trust."""
    root, commit = fetch.sync(c["repo"], paths.cache)
    src = inside(root, c["path"])
    if not (src / "SKILL.md").is_file() or (src / "SKILL.md").is_symlink():
        raise SystemExit(f"{c['id']} is no longer at {c['path']} upstream; run `skillscout discover` again")
    flags = [asdict(f) for f in scan(src, root)]
    if commit != c["commit"]:
        print(f"note: upstream moved from {c['commit'][:10]} to {commit[:10]} since the scan; showing the new commit")
    # The install folder is named from the SKILL.md fetched here, not from the index
    name = parse_frontmatter((src / "SKILL.md").read_text(encoding="utf-8", errors="ignore")).get("name") or src.name
    name = name if safe_name(name) else src.name
    return {**c, "name": name, "commit": commit, "flags": flags, "risk": worst(flags)}


def inside(root: Path, rel: str) -> Path:
    """root/rel, refusing any rel that leads out of root (`../`, an absolute path, a symlinked folder)."""
    path = (root / rel).resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise SystemExit(f"refusing {rel!r}: it points outside the repository")
    return root / rel


def recommended(c: dict, profile: dict) -> bool:
    sc = profile["scoring"]
    return (c["score"] >= sc["recommend_at"] and c["relevance"] >= sc.get("min_relevance", 0.3) and c["risk"] != HIGH
            and not c["conflicts"] and not c["installed"])


def report(candidates: list[dict], profile: dict, paths: Paths, top: int = 25) -> tuple[Path, str]:
    rec = [c for c in candidates if recommended(c, profile)]
    flagged = [c for c in candidates if c["risk"] == HIGH and c["relevance"] > 0]
    new = [c for c in candidates if c["new"]]
    fresh = [c for c in rec if c["new"]]
    first = bool(new) and len(new) == len(candidates)
    summary = (f"{len(rec)} recommended for you" + ("" if first else f" ({len(fresh)} new this run)")
               + f", {len(flagged)} relevant but flagged high-risk; {len(candidates):,} skills checked")
    lines = [f"# Skill report {datetime.now():%Y-%m-%d}", "", summary, "", "## New since last run", ""]
    if first:
        lines.append("First run: everything is new. From the next run, this section lists only what appeared since.")
    elif fresh:
        lines += [f"- **`{c['id']}`** ({c['stars']}★, score {c['score']:.2f}): {c['description'][:140]}" for c in fresh[:top]]
    else:
        lines.append("Nothing new that fits your profile.")
    lines += ["", "Run `skillscout updates` to see what changed in the skills you already have.", "",
              "## Recommended", "", "| Score | Skill | Stars | Risk | Matched | Why |", "|---|---|---|---|---|---|"]
    for c in rec[:top]:
        lines.append(f"| {c['score']:.2f} | `{c['id']}`{' 🆕' if c['new'] else ''} | {c['stars']} | {c['risk'] or 'none'} "
                     f"| {', '.join(c['matched'][:4])} | {c['description'][:110]} |")
    for name in persona.personas(profile):
        mine = sorted((c for c in rec if c["personas"].get(name, 0) >= profile["scoring"].get("min_relevance", 0.3)),
                      key=lambda c: (-c["new"], -c["personas"][name], -c["score"]))[:8]
        lines += ["", f"## For your {name} work", ""]
        lines += [f"- `{c['id']}`{' 🆕' if c['new'] else ''} ({c['stars']}★): {c['description'][:120]}" for c in mine] \
            or ["Nothing recommended yet; widen its keywords in your profile."]
    lines += ["", "Install one with `skillscout approve <id>` after reading it with `skillscout show <id>`.", "",
              "## Relevant but blocked", ""]
    for c in [c for c in candidates if c["relevance"] > 0 and not recommended(c, profile) and not c["installed"]][:top]:
        why = c["conflicts"] + [f"{f['level']}: {f['reason']} ({f['where']})" for f in c["flags"] if f["level"] == HIGH]
        if why:
            lines.append(f"- `{c['id']}`: {'; '.join(why[:3])}")
    paths.reports.mkdir(parents=True, exist_ok=True)
    out = paths.reports / f"{datetime.now():%Y-%m-%d}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out, summary


def find(candidates: list[dict], cid: str) -> dict:
    match = [c for c in candidates if c["id"] == cid] or [c for c in candidates if c["id"].endswith(":" + cid)]
    if len(match) != 1:
        raise SystemExit(f"{'no' if not match else 'ambiguous'} candidate matches {cid!r}; run `skillscout list`")
    return match[0]


def source_dir(c: dict, paths: Paths) -> Path:
    return inside(paths.cache / c["repo"].replace("/", "__"), c["path"])


def approve(c: dict, paths: Paths, accept_risk: bool = False, replace: bool = False, fetch=None) -> Path:
    """Copy one skill into ~/.claude/skills and pin it in the lockfile. With `fetch`, the skill is fetched and
    re-scanned here first, and that scan decides."""
    if fetch is not None:
        c = ensure_local(c, paths, fetch)
    if c["risk"] == HIGH and not accept_risk:
        raise SystemExit(f"{c['id']} has high-risk flags; read it with `show`, then pass --accept-risk")
    if not safe_name(c["name"]):
        raise SystemExit(f"refusing to install {c['id']}: {c['name']!r} isn't a plain folder name")
    repo_dir = paths.cache / c["repo"].replace("/", "__")
    src, dest = inside(repo_dir, c["path"]), paths.skills / c["name"]
    locked = _read(paths.lock, {})
    if dest.exists():
        ours = locked.get(c["id"], {}).get("digest") == digest(dest)
        if not (replace and ours):
            raise SystemExit(f"{dest} already exists" + ("" if ours else " and wasn't installed by skillscout"))
    # Pin what is actually in the cache now (an `update` check may have moved it past discovery time)
    commit = github.git("rev-parse", "HEAD", cwd=repo_dir)
    # Build the new copy beside the old one, then swap: a failure at any step leaves the old copy in place
    stage = paths.skills / f".{c['name']}.skillscout-new"
    shutil.rmtree(stage, ignore_errors=True)
    paths.skills.mkdir(parents=True, exist_ok=True)
    # Symlinks are dropped: following one could copy files from outside the skill (e.g. ~/.ssh)
    shutil.copytree(src, stage, ignore=lambda d, names: [n for n in names if n == ".git" or Path(d, n).is_symlink()])
    entry = {"repo": c["repo"], "repo_id": c.get("repo_id"), "path": c["path"], "commit": commit, "digest": digest(stage),
             "dest": str(dest), "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    backup = None
    if dest.exists():
        backup = paths.backups / _slug(c["id"])
        shutil.rmtree(backup, ignore_errors=True)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(backup))
        entry["previous"] = {k: v for k, v in locked[c["id"]].items() if k != "previous"} | {"backup": str(backup)}
    swapped = False
    try:
        stage.rename(dest)
        swapped = True
        _write(paths.lock, {**locked, c["id"]: entry})
    except BaseException:
        if swapped:
            shutil.rmtree(dest)
        if backup:
            shutil.move(str(backup), str(dest))
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return dest


def _slug(cid: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in cid)


def rollback(cid: str, paths: Paths) -> Path:
    """Put back the copy the last `approve --update` replaced, and its pin."""
    locked = _read(paths.lock, {})
    prev = locked.get(cid, {}).get("previous")
    if not prev or not Path(prev["backup"]).exists():
        raise SystemExit(f"no earlier version of {cid} to roll back to")
    dest, backup = Path(locked[cid]["dest"]), Path(prev.pop("backup"))
    if dest.exists() and digest(dest) != locked[cid]["digest"]:
        raise SystemExit(f"{dest} was edited since install; move your changes out first")
    stage = dest.with_name(f".{dest.name}.skillscout-old")
    shutil.rmtree(stage, ignore_errors=True)
    if dest.exists():
        dest.rename(stage)
    try:
        shutil.move(str(backup), str(dest))
        _write(paths.lock, {**locked, cid: prev})
    except BaseException:
        if dest.exists() and stage.exists():
            shutil.move(str(dest), str(backup))
        if stage.exists():
            stage.rename(dest)
        raise
    shutil.rmtree(stage, ignore_errors=True)
    return dest


def remove(cid: str, paths: Paths) -> Path:
    locked = _read(paths.lock, {})
    if cid not in locked:
        raise SystemExit(f"{cid} isn't in the lockfile")
    dest = Path(locked.pop(cid)["dest"])
    _write(paths.lock, locked)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.rmtree(paths.backups / _slug(cid), ignore_errors=True)
    return dest


def updates(paths: Paths, fetch=github) -> list[tuple[str, str, str]]:
    """(id, new commit, unified diff) for each pinned skill whose source changed. Applies nothing."""
    out = []
    for cid, entry in _read(paths.lock, {}).items():
        root, commit = fetch.sync(entry["repo"], paths.cache)
        if commit == entry["commit"]:
            continue
        new, old = root / entry["path"], Path(entry["dest"])
        diff = []
        names = sorted({str(p.relative_to(d)) for d in (old, new) if d.exists() for p in d.rglob("*") if p.is_file()})
        for n in names:
            a = (old / n).read_text(encoding="utf-8", errors="ignore").splitlines() if (old / n).exists() else []
            b = (new / n).read_text(encoding="utf-8", errors="ignore").splitlines() if (new / n).exists() else []
            diff += difflib.unified_diff(a, b, f"installed/{n}", f"upstream/{n}", lineterm="")
        if diff:
            out.append((cid, commit, "\n".join(diff)))
    return out
