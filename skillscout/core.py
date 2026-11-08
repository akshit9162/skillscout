"""Discovery, reporting, approval and update checks."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import audits, github, index, persona
from .install import pick
from .scan import HIGH, RULES, scan, worst
from .score import combined, conflicts, freshness, popularity, relevance
from .skill import VENDORED, find_skills, licensed, parse_frontmatter, plugin_skills
from .store import load_toml, read_json as _read, write_json as _write

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
    return load_toml(path)


def installed_skills(paths: Paths) -> dict[str, str]:
    """name -> description for your skills and for skills of enabled plugins, which are named the way Claude
    Code names them: `<plugin>:<skill>`. The plugin cache is laid out <marketplace>/<plugin>/<version>/..."""
    try:
        enabled = {k.split("@")[0] for k, on in json.loads(paths.settings.read_text(encoding="utf-8"))
                   .get("enabledPlugins", {}).items() if on}
    except (OSError, ValueError):
        enabled = None                                    # no settings file: count every cached plugin
    out = {}
    if paths.skills.exists():
        found = set(paths.skills.rglob("SKILL.md"))
        # rglob doesn't follow folder links (Python 3.13+); skills are often linked in
        found |= {d / "SKILL.md" for d in paths.skills.iterdir()
                  if (d.is_symlink() or getattr(d, "is_junction", bool)()) and (d / "SKILL.md").is_file()}
        for md in sorted(found):
            if VENDORED & set(md.relative_to(paths.skills).parts):
                continue
            meta = parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
            out[meta.get("name") or md.parent.name] = meta.get("description", "")
    for plugin, skills in plugin_skills(paths.plugins).items():
        if enabled is None or plugin in enabled:
            out |= {f"{plugin}:{name}": desc for name, desc in skills.items()}
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
    for name in [*s.get("trusted", []), *s.get("include", [])]:
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
    always = set(s.get("trusted", [])) | set(s.get("include", []))     # kept whatever their stars or age
    keep = [r for r in repos.values() if r["full_name"] in always or (
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
        has_licence = licensed(root)
        names = set()
        for sk in find_skills(root):
            rel = sk.path.relative_to(root).as_posix()
            # owner/repo:name, or owner/repo:path/to/skill for a second skill with the same name
            cid = f"{r['full_name']}:{rel if sk.name in names else sk.name}"
            names.add(sk.name)
            tree = tree_of.get(rel)
            key = f"{RULES}:{tree}:{int(has_licence)}" if tree else None
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


def risk_word(c: dict) -> str:
    return c["risk"] or "none"


def dedupe(candidates: list[dict]) -> list[tuple[dict, int]]:
    """One row per skill, in order: a skill at two paths in one repo, or copied into forks, shows once (its
    best-scored copy) with the number of other copies."""
    first, out = {}, []
    for c in candidates:
        key = (c["name"].lower(), " ".join(c["description"].lower().split()))
        if key in first:
            out[first[key]][1] += 1
        else:
            first[key] = len(out)
            out.append([c, 0])
    return [(c, n) for c, n in out]


def copies_note(n: int) -> str:
    return f" (+{n} {'copy' if n == 1 else 'copies'})" if n else ""


def recommended(c: dict, profile: dict) -> bool:
    sc = profile["scoring"]
    return (c["score"] >= sc["recommend_at"] and c["relevance"] >= sc.get("min_relevance", 0.3) and c["risk"] != HIGH
            and not audits.blocked(c.get("audits")) and not c["conflicts"] and not c["installed"])


def report(candidates: list[dict], profile: dict, paths: Paths, top: int = 25) -> tuple[Path, str]:
    rows = dedupe([c for c in candidates if recommended(c, profile)])     # copies of one skill count once
    rec, copies = [c for c, _ in rows], {c["id"]: n for c, n in rows}
    more = lambda c: copies_note(copies.get(c["id"], 0))
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
        lines += [f"- **`{c['id']}`**{more(c)} ({c['stars']}★, score {c['score']:.2f}): {c['description'][:140]}" for c in fresh[:top]]
    else:
        lines.append("Nothing new that fits your profile.")
    lines += ["", "Run `skillscout updates` to see what changed in the skills you already have.", "",
              "## Recommended", "", "| Score | Skill | Stars | Risk | skills.sh audits | Matched | Why |", "|---|---|---|---|---|---|---|"]
    for c in rec[:top]:
        lines.append(f"| {c['score']:.2f} | `{c['id']}`{' 🆕' if c['new'] else ''}{more(c)} | {c['stars']} | {risk_word(c)} "
                     f"| {audits.summary(c.get('audits'))} | {', '.join(c['matched'][:4])} | {c['description'][:110]} |")
    for name in persona.personas(profile):
        mine = sorted((c for c in rec if c["personas"].get(name, 0) >= profile["scoring"].get("min_relevance", 0.3)),
                      key=lambda c: (-c["new"], -c["personas"][name], -c["score"]))[:8]
        lines += ["", f"## For your {name} work", ""]
        lines += [f"- `{c['id']}`{' 🆕' if c['new'] else ''}{more(c)} ({c['stars']}★): {c['description'][:120]}" for c in mine] \
            or ["Nothing recommended yet; widen its keywords in your profile."]
    lines += ["", "Install one with `skillscout approve <id>` after reading it with `skillscout show <id>`.", "",
              "## Relevant but blocked", ""]
    for c in [c for c in candidates if c["relevance"] > 0 and not recommended(c, profile) and not c["installed"]][:top]:
        why = c["conflicts"] + [f"{f['level']}: {f['reason']} ({f['where']})" for f in c["flags"] if f["level"] == HIGH]
        if audits.blocked(c.get("audits")):
            why.append(f"skills.sh: {audits.summary(c['audits'])}")
        if why:
            lines.append(f"- `{c['id']}`: {'; '.join(why[:3])}")
    paths.reports.mkdir(parents=True, exist_ok=True)
    out = paths.reports / f"{datetime.now():%Y-%m-%d}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out, summary


def find(candidates: list[dict], cid: str) -> dict:
    by_id = {c["id"]: c for c in candidates}
    return by_id[pick(by_id, cid)]
