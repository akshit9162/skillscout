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

from . import github, index
from .scan import HIGH, RULES, scan, worst
from .score import combined, conflicts, freshness, popularity, relevance
from .skill import find_skills, parse_frontmatter

HOME = Path.home()
STATE = HOME / ".skillscout"
SKILLS_DIR = HOME / ".claude" / "skills"
PLUGIN_CACHE = HOME / ".claude" / "plugins" / "cache"


class Paths:
    def __init__(self, state: Path = STATE, skills: Path = SKILLS_DIR, plugins: Path = PLUGIN_CACHE):
        self.state, self.skills, self.plugins = state, skills, plugins
        self.cache = state / "repos"
        self.candidates = state / "candidates.json"
        self.crawl = state / "crawl.json"     # the user-independent part of the last crawl (what the index publishes)
        self.index = state / "index.json.gz"  # the last shared index downloaded
        self.repos = state / "repos.json"     # each repo's metadata, push time, checkout and commit at the last run
        self.lock = state / "skills.lock.json"
        self.reports = state / "reports"


def load_profile(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def _read(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def digest(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink()):
        h.update(str(f.relative_to(folder)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def installed_skills(paths: Paths) -> dict[str, str]:
    """name -> description for user skills and plugin skills (plugin ones as `plugin:name`)."""
    out = {}
    for root, prefix in ((paths.skills, ""), (paths.plugins, "plugin")):
        if not root.exists():
            continue
        for md in root.rglob("SKILL.md"):
            if ".git" in md.parts or "node_modules" in md.parts:
                continue
            meta = parse_frontmatter(md.read_text(errors="ignore"))
            name = meta.get("name") or md.parent.name
            out[f"{prefix}:{name}" if prefix else name] = meta.get("description", "")
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
        for sk in find_skills(root):
            cid = f"{r['full_name']}:{sk.name}"
            tree = tree_of.get(str(sk.path.relative_to(root)))
            key = f"{RULES}:{tree}:{int(licensed)}" if tree else None
            old = previous.get(cid)
            if key and old and old.get("scan_key") == key and not full:     # same files, same rules: the old result stands
                flags, reused = old["flags"], reused + 1
            else:
                flags = [asdict(f) for f in scan(sk.path, root)]
            records.append({
                "id": cid, "repo": r["full_name"], "name": sk.name, "description": sk.description,
                "path": str(sk.path.relative_to(root)), "commit": commit, "stars": r["stars"],
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
    previous = {c["id"] for c in _read(paths.candidates, [])}
    candidates = []
    for r in records:
        rel, hits = relevance(f"{r['name']} {r['description']}", profile["keywords"])
        candidates.append({
            **r, "trusted": r["repo"] in trusted, "relevance": round(rel, 3), "matched": hits,
            "score": combined(rel, popularity(r["stars"]), freshness(r["pushed_at"], now), r["repo"] in trusted),
            "conflicts": conflicts(r["name"], r["description"], installed, profile["scoring"]["overlap_at"]),
            "installed": r["id"] in locked, "new": r["id"] not in previous,
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    _write(paths.candidates, candidates)
    return candidates


def discover(profile: dict, paths: Paths, fetch=github, full: bool = False) -> list[dict]:
    """Crawl GitHub on this machine, then score: the `--local` path."""
    return score(crawl(profile["search"], paths, fetch, full), profile, paths)


def discover_from_index(profile: dict, paths: Paths, url: str = index.URL) -> list[dict]:
    """Score the shared weekly index instead of crawling: one download, no GitHub API calls."""
    idx = index.fetch(url, paths.index)
    print(f"index built {idx['built_at']}: {len(idx['skills'])} skills from {len({s['repo'] for s in idx['skills']})} repos")
    return score(idx["skills"], profile, paths)


def ensure_local(c: dict, paths: Paths, fetch=github) -> dict:
    """Fetch the skill and scan it again here, so nothing about it is taken from the index on trust."""
    root, commit = fetch.sync(c["repo"], paths.cache)
    src = root / c["path"]
    if not (src / "SKILL.md").exists():
        raise SystemExit(f"{c['id']} is no longer at {c['path']} upstream; run `skillscout discover` again")
    flags = [asdict(f) for f in scan(src, root)]
    if commit != c["commit"]:
        print(f"note: upstream moved from {c['commit'][:10]} to {commit[:10]} since the scan; showing the new commit")
    return {**c, "commit": commit, "flags": flags, "risk": worst(flags)}


def recommended(c: dict, profile: dict) -> bool:
    sc = profile["scoring"]
    return (c["score"] >= sc["recommend_at"] and c["relevance"] >= sc.get("min_relevance", 0.3) and c["risk"] != HIGH
            and not c["conflicts"] and not c["installed"])


def report(candidates: list[dict], profile: dict, paths: Paths, top: int = 25) -> tuple[Path, str]:
    rec = [c for c in candidates if recommended(c, profile)]
    flagged = [c for c in candidates if c["risk"] == HIGH and c["relevance"] > 0]
    new = [c for c in candidates if c["new"]]
    summary = f"{len(new)} new, {len(rec)} recommended, {len(flagged)} flagged high-risk"
    lines = [f"# Skill report {datetime.now():%Y-%m-%d}", "", summary, "", "## New since last run", ""]
    fresh = [c for c in rec if c["new"]]
    if new and len(new) == len(candidates):
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
    lines += ["", "Install one with `skillscout approve <id>` after reading it with `skillscout show <id>`.", "",
              "## Relevant but blocked", ""]
    for c in [c for c in candidates if c["relevance"] > 0 and not recommended(c, profile) and not c["installed"]][:top]:
        why = c["conflicts"] + [f"{f['level']}: {f['reason']} ({f['where']})" for f in c["flags"] if f["level"] == HIGH]
        if why:
            lines.append(f"- `{c['id']}`: {'; '.join(why[:3])}")
    paths.reports.mkdir(parents=True, exist_ok=True)
    out = paths.reports / f"{datetime.now():%Y-%m-%d}.md"
    out.write_text("\n".join(lines) + "\n")
    return out, summary


def find(candidates: list[dict], cid: str) -> dict:
    match = [c for c in candidates if c["id"] == cid] or [c for c in candidates if c["id"].endswith(":" + cid)]
    if len(match) != 1:
        raise SystemExit(f"{'no' if not match else 'ambiguous'} candidate matches {cid!r}; run `skillscout list`")
    return match[0]


def source_dir(c: dict, paths: Paths) -> Path:
    return paths.cache / c["repo"].replace("/", "__") / c["path"]


def approve(c: dict, paths: Paths, accept_risk: bool = False, replace: bool = False, fetch=None) -> Path:
    """Copy one skill into ~/.claude/skills and pin it in the lockfile. With `fetch`, the skill is fetched and
    re-scanned here first, and that scan decides."""
    if fetch is not None:
        c = ensure_local(c, paths, fetch)
    if c["risk"] == HIGH and not accept_risk:
        raise SystemExit(f"{c['id']} has high-risk flags; read it with `show`, then pass --accept-risk")
    src, dest = source_dir(c, paths), paths.skills / c["name"]
    locked = _read(paths.lock, {})
    if dest.exists():
        ours = locked.get(c["id"], {}).get("digest") == digest(dest)
        if not (replace and ours):
            raise SystemExit(f"{dest} already exists" + ("" if ours else " and wasn't installed by skillscout"))
        shutil.rmtree(dest)
    # Symlinks are dropped: following one could copy files from outside the skill (e.g. ~/.ssh)
    shutil.copytree(src, dest, ignore=lambda d, names: [n for n in names if n == ".git" or Path(d, n).is_symlink()])
    # Pin what is actually in the cache now (an `update` check may have moved it past discovery time)
    commit = github.git("rev-parse", "HEAD", cwd=paths.cache / c["repo"].replace("/", "__"))
    locked[c["id"]] = {"repo": c["repo"], "path": c["path"], "commit": commit, "digest": digest(dest),
                       "dest": str(dest), "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _write(paths.lock, locked)
    return dest


def remove(cid: str, paths: Paths) -> Path:
    locked = _read(paths.lock, {})
    if cid not in locked:
        raise SystemExit(f"{cid} isn't in the lockfile")
    dest = Path(locked.pop(cid)["dest"])
    if dest.exists():
        shutil.rmtree(dest)
    _write(paths.lock, locked)
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
            a = (old / n).read_text(errors="ignore").splitlines() if (old / n).exists() else []
            b = (new / n).read_text(errors="ignore").splitlines() if (new / n).exists() else []
            diff += difflib.unified_diff(a, b, f"installed/{n}", f"upstream/{n}", lineterm="")
        if diff:
            out.append((cid, commit, "\n".join(diff)))
    return out
