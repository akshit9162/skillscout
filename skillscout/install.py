"""Installing skills: fetch and re-scan here, copy, pin, roll back, remove, and diff against upstream.

This is the trust boundary. Nothing here takes a file from the index or an upstream checkout on trust:
every install is re-scanned locally, symlinks are never followed or copied, and every path is checked to
stay inside its repository."""

import difflib
import hashlib
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import audits, github
from .scan import HIGH, scan, worst
from .skill import parse_frontmatter, safe_name, skill_files
from .store import read_json, write_json


def digest(folder: Path) -> str:
    h = hashlib.sha256()
    for f in skill_files(folder):
        h.update(str(f.relative_to(folder)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def inside(root: Path, rel: str) -> Path:
    """root/rel, refusing any rel that leads out of root (`../`, an absolute path, a symlinked folder)."""
    path = (root / rel).resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise SystemExit(f"refusing {rel!r}: it points outside the repository")
    return root / rel


def pick(ids, query: str, what: str = "candidate") -> str:
    """The one id equal to `query`, or ending in `:query` (a bare skill name). Ambiguity lists the matches."""
    ids = list(ids)
    if query in ids:
        return query
    match = [i for i in ids if i.endswith(":" + query)]
    if len(match) == 1:
        return match[0]
    if not match:
        hint = "run `skillscout list`" if what == "candidate" else "run `skillscout installed`"
        raise SystemExit(f"no {what} matches {query!r}; {hint}")
    raise SystemExit(f"{query!r} matches {len(match)} {what}s; use the full id:\n  " + "\n  ".join(match[:12]))


def source_dir(c: dict, paths) -> Path:
    return inside(github.cache_dir(paths.cache, c["repo"]), c["path"])


def ensure_local(c: dict, paths, fetch=github) -> dict:
    """Fetch the skill and scan it again here, so nothing about it is taken from the index on trust."""
    root, commit = fetch.sync(c["repo"], paths.cache)
    src = inside(root, c["path"])
    if not (src / "SKILL.md").is_file() or (src / "SKILL.md").is_symlink():
        raise SystemExit(f"{c['id']} is no longer at {c['path']} upstream; run `skillscout discover` again")
    flags = [asdict(f) for f in scan(src, root)]
    if commit != c["commit"]:
        print(f"note: upstream is at {commit[:10]} now, not {c['commit'][:10]} as in the index; this is the new commit")
    # The install folder is named from the SKILL.md fetched here, not from the index
    name = parse_frontmatter((src / "SKILL.md").read_text(encoding="utf-8", errors="ignore")).get("name") or src.name
    name = name if safe_name(name) else src.name
    return {**c, "name": name, "commit": commit, "flags": flags, "risk": worst(flags), "root": str(root)}


def reviewed(paths) -> dict[str, str]:
    """id -> the upstream commit `updates` last showed a diff for."""
    return read_json(paths.state / "reviewed.json", {})


def approve(c: dict, paths, accept_risk: bool = False, replace: bool = False, fetch=None,
            expect: str | None = None) -> tuple[Path, str]:
    """Copy one skill into the skills folder and pin it. With `fetch`, the skill is fetched and re-scanned here
    first, and that scan decides. `expect` is the commit the user reviewed: anything else is refused.
    Returns (folder, "installed" | "updated" | "current")."""
    if fetch is not None:
        c = ensure_local(c, paths, fetch)
        if expect and not c["commit"].startswith(expect):
            raise SystemExit(f"upstream moved to {c['commit'][:10]} after you reviewed {expect[:10]}; "
                             "run `skillscout updates` and read the new diff first")
    if c["risk"] == HIGH and not accept_risk:
        raise SystemExit(f"{c['id']} has high-risk flags; read it with `show`, then pass --accept-risk")
    if audits.blocked(c.get("audits")) and not accept_risk:
        raise SystemExit(f"{c['id']}: skills.sh auditors rate it high risk ({audits.summary(c['audits'])}); "
                         "read it with `show`, then pass --accept-risk")
    if not safe_name(c["name"]):
        raise SystemExit(f"refusing to install {c['id']}: {c['name']!r} isn't a plain folder name")
    repo_dir = Path(c["root"]) if c.get("root") else github.cache_dir(paths.cache, c["repo"])
    src, dest = inside(repo_dir, c["path"]), paths.skills / c["name"]
    locked = read_json(paths.lock, {})
    old = locked.get(c["id"])
    if old and Path(old["dest"]) != dest:
        raise SystemExit(f"{c['id']} is already installed at {old['dest']}; "
                         f"`skillscout remove {c['id']}` first to install it in {paths.skills}")
    commit = github.git("rev-parse", "HEAD", cwd=repo_dir)    # pin what is actually checked out now
    if dest.exists():
        ours = bool(old) and old.get("digest") == digest(dest)
        if not (replace and ours):
            raise SystemExit(f"{dest} already exists" + (f"; run `skillscout approve {c['id']} --update` to replace it"
                                                         if ours else " and wasn't installed by skillscout"))
        if old["commit"] == commit:
            return dest, "current"
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
        entry["previous"] = {k: v for k, v in old.items() if k != "previous"} | {"backup": str(backup)}
    swapped = False
    try:
        stage.rename(dest)
        swapped = True
        write_json(paths.lock, {**locked, c["id"]: entry})
    except BaseException:
        if swapped:
            shutil.rmtree(dest)
        if backup:
            shutil.move(str(backup), str(dest))
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return dest, "updated" if backup else "installed"


def _slug(cid: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in cid)


def rollback(cid: str, paths) -> tuple[str, Path]:
    """Put back the copy the last `approve --update` replaced, and its pin."""
    locked = read_json(paths.lock, {})
    cid = pick(locked, cid, "installed skill")
    prev = locked[cid].get("previous")
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
        write_json(paths.lock, {**locked, cid: prev})
    except BaseException:
        if dest.exists() and stage.exists():
            shutil.move(str(dest), str(backup))
        if stage.exists():
            stage.rename(dest)
        raise
    shutil.rmtree(stage, ignore_errors=True)
    return cid, dest


def remove(cid: str, paths, force: bool = False) -> tuple[str, Path]:
    locked = read_json(paths.lock, {})
    cid = pick(locked, cid, "installed skill")
    dest = Path(locked[cid]["dest"])
    if dest.exists() and digest(dest) != locked[cid]["digest"] and not force:
        raise SystemExit(f"{dest} was edited since install; move your changes out, or pass --force to delete them")
    del locked[cid]
    write_json(paths.lock, locked)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.rmtree(paths.backups / _slug(cid), ignore_errors=True)
    return cid, dest


def updates(paths, fetch=github) -> list[tuple[str, str, str]]:
    """(id, new commit, unified diff) for each pinned skill whose source changed. Applies nothing, and
    remembers each commit shown so `approve --update` installs exactly that one."""
    out, seen = [], reviewed(paths)
    for cid, entry in read_json(paths.lock, {}).items():
        root, commit = fetch.sync(entry["repo"], paths.cache)
        if commit == entry["commit"]:
            continue
        new, old = inside(root, entry["path"]), Path(entry["dest"])
        files = {d: {str(p.relative_to(d)) for p in skill_files(d)} if d.exists() else set() for d in (old, new)}
        diff = []
        for n in sorted(files[old] | files[new]):
            a = (old / n).read_text(encoding="utf-8", errors="ignore").splitlines() if n in files[old] else []
            b = (new / n).read_text(encoding="utf-8", errors="ignore").splitlines() if n in files[new] else []
            diff += difflib.unified_diff(a, b, f"installed/{n}", f"upstream/{n}", lineterm="")
        if new.exists():        # a symlink is named, never read: it could point at ~/.ssh
            diff += [f"upstream/{p.relative_to(new)}: symlink to {os.readlink(p)} (not read; approve never copies it)"
                     for p in sorted(new.rglob("*")) if p.is_symlink()]
        if diff:
            out.append((cid, commit, "\n".join(diff)))
            seen[cid] = commit
    write_json(paths.state / "reviewed.json", seen)
    return out
