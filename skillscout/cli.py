"""skillscout command line."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __doc__ as ABOUT, core, github, history, index, persona, schedule, switch

EXAMPLE = Path(__file__).resolve().parent / "profile.example.toml"
USER_PROFILE = Path.home() / ".skillscout" / "profile.toml"


def notify(text: str) -> None:
    if sys.platform == "darwin" and shutil.which("osascript"):
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "skillscout"'], check=False)
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", "skillscout", text], check=False)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="skillscout", description=ABOUT)
    p.add_argument("--profile", type=Path, default=None, help="default: ~/.skillscout/profile.toml, else the example")
    sub = p.add_subparsers(dest="cmd", required=True)
    it = sub.add_parser("init", help="write ~/.skillscout/profile.toml")
    it.add_argument("--about", help="one line about your work; becomes your keywords (e.g. \"backend in Python, RL evals, Postgres\")")
    d = sub.add_parser("discover", help="score this week's shared index against your profile and write a report")
    d.add_argument("--local", action="store_true", help="crawl and scan GitHub on this machine instead of using the index")
    d.add_argument("--full", action="store_true", help="with --local: ignore caches, fetch every repo and re-scan every skill")
    d.add_argument("--notify", action="store_true", help="desktop notification with the summary (macOS, or Linux with notify-send)")
    ls = sub.add_parser("list", help="show scored candidates")
    ls.add_argument("--all", action="store_true", help="include zero-relevance skills")
    ls.add_argument("-n", type=int, default=30)
    ix = sub.add_parser("index", help="crawl and scan the ecosystem and write the shared index (what the weekly job runs)")
    ix.add_argument("--out", type=Path, default=Path("index.json.gz"))
    ix.add_argument("--full", action="store_true", help="ignore caches: fetch every repo and re-scan every skill")
    sh = sub.add_parser("show", help="fetch a candidate, scan it here, print its flags and full SKILL.md")
    sh.add_argument("id")
    ap = sub.add_parser("approve", help="install one skill (into ~/.claude/skills unless [install] dir says otherwise) and pin it")
    ap.add_argument("id")
    ap.add_argument("--to", type=Path, help="install into this skills folder instead, e.g. another agent's")
    ap.add_argument("--accept-risk", action="store_true", help="install despite high-risk flags")
    ap.add_argument("--update", action="store_true", help="replace a copy skillscout installed earlier")
    rm = sub.add_parser("remove", help="uninstall a skill skillscout installed")
    rm.add_argument("id")
    rb = sub.add_parser("rollback", help="restore the copy the last `approve --update` replaced")
    rb.add_argument("id")
    sub.add_parser("installed", help="show pinned skills")
    sub.add_parser("updates", help="diff pinned skills against upstream; applies nothing")
    sub.add_parser("schedule", help="set up weekly discovery for this OS (you turn it on)")
    hi = sub.add_parser("history", help="summarise your recent Claude Code sessions, read locally, to draft personas from")
    hi.add_argument("--days", type=int, default=60)
    pe = sub.add_parser("persona", help="kinds of work, each with its own keywords and an agent kept current")
    psub = pe.add_subparsers(dest="action", required=True)
    pa = psub.add_parser("add", help="add a persona from one line about that work")
    pa.add_argument("name")
    pa.add_argument("--about", required=True, help="what this side of your work is, in your words")
    pa.add_argument("--keywords", help="instead of drafting them: \"cold email=4,investor update=3\"")
    pa.add_argument("--agent", help="agent file name, if not the persona's name")
    psub.add_parser("list", help="each persona, its keywords, and the installed skills that fit it")
    pb = psub.add_parser("build", help="write or update the persona's agent in ~/.claude/agents (only its skills: list)")
    pb.add_argument("name", nargs="?")
    pb.add_argument("--all", action="store_true")
    pb.add_argument("--dry-run", action="store_true")
    pu = psub.add_parser("use", help="list only these personas' skills and plugins in Claude Code (no names: show what's active)")
    pu.add_argument("names", nargs="*")
    pu.add_argument("--clear", action="store_true", help="undo every change skillscout made: all skills and plugins as before")
    pu.add_argument("--core", action="store_true", help="no persona: hide every persona's skills and plugins, keep [switch] core")
    ps = psub.add_parser("start", help="`persona use` these personas, then start Claude Code")
    ps.add_argument("names", nargs="+")
    a = p.parse_args(argv)

    paths = core.Paths()
    if a.cmd == "init":
        if USER_PROFILE.exists():
            raise SystemExit(f"{USER_PROFILE} already exists")
        text = EXAMPLE.read_text(encoding="utf-8")
        if a.about:
            kw = history.keywords_from(a.about, vocabulary(core.load_profile(EXAMPLE), paths))
            if not kw:
                raise SystemExit("none of those words appear in skill descriptions; try more specific ones")
            head, rest = text.split("[keywords]\n", 1)
            lines = rest.splitlines(keepends=True)
            keep = next(i for i, line in enumerate(lines) if not line.startswith('"') and line.strip())
            tail = "".join(lines[keep:])
            text = head + "[keywords]\n" + f"# drafted from: {a.about}\n" + "".join(f"{json.dumps(k)} = {v}\n" for k, v in kw.items()) + "\n" + tail
        USER_PROFILE.parent.mkdir(parents=True, exist_ok=True)
        USER_PROFILE.write_text(text, encoding="utf-8")
        print(f"wrote {USER_PROFILE}; " + ("check the drafted [keywords]" if a.about else "edit [keywords] to match your work")
              + ", then run `skillscout discover`")
        return
    profile = core.load_profile(a.profile or (USER_PROFILE if USER_PROFILE.exists() else EXAMPLE))
    if folder := getattr(a, "to", None) or profile.get("install", {}).get("dir"):
        paths.skills = Path(folder).expanduser()
    cands = core._read(paths.candidates, [])

    if a.cmd == "discover":
        url = profile.get("index", {}).get("url", index.URL)
        if a.local or not url:
            cands = core.discover(profile, paths, full=a.full)
        else:
            try:
                cands = core.discover_from_index(profile, paths, url, profile.get("index", {}).get("verify", False))
            except index.Unavailable as e:
                raise SystemExit(str(e))
        out, summary = core.report(cands, profile, paths)
        print(f"{summary}\nreport: {out}")
        if a.notify:
            notify(summary)
    elif a.cmd == "index":
        records = core.crawl(profile["search"], paths, full=a.full)
        data = index.build(records, a.out, core.RULES)
        print(f"wrote {a.out}: {len(data['skills'])} skills from {len({r['repo'] for r in records})} repos")
    elif a.cmd == "list":
        rows = [c for c in cands if a.all or c["relevance"] > 0][: a.n]
        for c in rows:
            tag = "installed" if c["installed"] else ("REC" if core.recommended(c, profile) else "")
            print(f"{c['score']:.2f}  {c['risk'] or '-':6}  {tag:9}  {c['id']}")
    elif a.cmd == "show":
        c = core.ensure_local(core.find(cands, a.id), paths)
        print(json.dumps({k: c[k] for k in ("id", "score", "stars", "license", "commit", "risk", "matched", "conflicts")}, indent=2))
        for f in c["flags"]:
            print(f"  [{f['level']}] {f['reason']} — {f['where']}")
        src = core.source_dir(c, paths)
        print(f"\nfiles: {', '.join(sorted(str(x.relative_to(src)) for x in src.rglob('*') if x.is_file()))}\n")
        print((src / "SKILL.md").read_text(encoding="utf-8", errors="ignore"))
    elif a.cmd == "approve":
        c = core.find(cands, a.id)
        print(f"installed {c['id']} -> {core.approve(c, paths, a.accept_risk, a.update, fetch=github)}")
        if on := switch.active(paths):                 # a new skill for an inactive persona stays out of view
            names = [n for n in on if n != switch.CORE]
            say_switch(switch.use(profile, paths, names, core.installed_skills(paths), not names), on, quiet=True)
    elif a.cmd == "remove":
        print(f"removed {core.remove(a.id, paths)}")
    elif a.cmd == "rollback":
        print(f"restored the previous {a.id} -> {core.rollback(a.id, paths)}")
    elif a.cmd == "installed":
        for cid, e in core._read(paths.lock, {}).items():
            print(f"{cid}  @{e['commit'][:10]}  {e['installed_at']}")
    elif a.cmd == "updates":
        found = core.updates(paths)
        for cid, commit, diff in found:
            print(f"\n=== {cid} -> {commit[:10]}\n{diff}")
        print(f"\n{len(found)} pinned skill(s) changed upstream." + (" Re-run `approve <id> --update` after reading the diff." if found else ""))
    elif a.cmd == "history":
        text = history.digest(history.episodes(days=a.days))
        print(text or "no Claude Code history found in ~/.claude/projects")
        print(f"(read from ~/.claude/projects on this machine, last {a.days} days; nothing is sent anywhere)")
    elif a.cmd == "persona":
        run_persona(a, profile, paths, cands)
    elif a.cmd == "schedule":
        files, message = schedule.here(paths.state)
        schedule.write(files)
        print(message)


def say_switch(done: dict, names: list[str], quiet: bool = False) -> None:
    parts = [f"{label} {len(done[k])}: {', '.join(done[k][:8])}{' ...' if len(done[k]) > 8 else ''}"
             for k, label in (("hidden", "hid"), ("shown", "showed"), ("plugins_off", "plugins off"), ("plugins_on", "plugins on"))
             if done[k]]
    if done["kept_yours"]:
        parts.append(f"left your own skillOverrides alone for {', '.join(done['kept_yours'])}")
    if quiet and not parts:
        return
    print((f"active: {', '.join(names)}" if names else "cleared: every skill and plugin as before skillscout switched them"))
    for line in parts:
        print(f"  {line}")
    if parts:
        print("A new Claude Code session picks this up; in an open one, run /reload-skills and /reload-plugins.")


def start_claude(names: list[str], known: dict, paths: core.Paths) -> None:
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("claude isn't on PATH; the switch is done, start Claude Code yourself")
    cmd = [claude]
    agent = known[names[0]]["agent"] if len(names) == 1 else None
    if agent and (paths.agents / f"{agent}.md").exists():
        cmd += ["--agent", agent]
    sys.exit(subprocess.run(cmd, check=False).returncode)


def vocabulary(profile: dict, paths: core.Paths) -> dict[str, int]:
    """Terms skills use, from the cached index (downloaded first if there isn't one)."""
    try:
        if not paths.index.exists():
            index.fetch(profile.get("index", {}).get("url") or index.URL, paths.index)
        return history.vocabulary(index.load(paths.index)["skills"])
    except index.Unavailable as e:
        raise SystemExit(f"{e}\n(drafting keywords needs the skill index; or write them by hand)")


def run_persona(a, profile: dict, paths: core.Paths, cands: list[dict]) -> None:
    known = persona.personas(profile)
    if a.action == "add":
        if a.keywords:
            kw = {k.strip(): int(v) for k, v in (pair.rsplit("=", 1) for pair in a.keywords.split(","))}
        else:
            kw = history.keywords_from(a.about, vocabulary(profile, paths))
        path = a.profile or USER_PROFILE
        if not path.exists():
            raise SystemExit(f"no profile at {path}; run `skillscout init` first")
        print(persona.add(path, a.name, a.about, kw, a.agent, switch.draft_plugins(paths, kw)))
        print(f"\nadded to {path}. Next: `skillscout discover`, then `skillscout persona build {a.name}`")
        return
    installed = core.installed_skills(paths)
    if a.action in ("use", "start"):
        if a.action == "use" and not a.names and not a.clear and not a.core:
            on = switch.active(paths)
            print(f"active: {', '.join(on)}" if on else "no persona active: Claude Code lists every skill")
            return
        names = [] if a.action == "use" and a.clear else a.names
        core_only = a.action == "use" and a.core and not a.clear
        say_switch(switch.use(profile, paths, names, installed, core_only), names or ([switch.CORE] if core_only else []))
        if a.action == "start":
            start_claude(names, known, paths)
        return
    if not known:
        raise SystemExit("no personas yet: add one with `skillscout persona add <name> --about \"...\"`")
    if a.action == "list":
        on = switch.active(paths)
        by = switch.claims(profile, switch.own_skills(installed))
        for name, p in known.items():
            fits = persona.fitting(installed, p["keywords"], p["max_skills"])
            agent = paths.agents / f"{p['agent']}.md"
            print(f"{name}: {p['description']}\n  keywords: {', '.join(p['keywords'])}\n"
                  f"  agent: {agent}{'' if agent.exists() else ' (not built yet)'}\n"
                  f"  best installed skills: {', '.join(n for n, _ in fits) or 'none fit yet'}\n"
                  f"  switches: {len(by[name])} of your skills, plugins: {', '.join(p['plugins']) or 'none'}"
                  + ("  (active)" if name in on else ""))
            pending = [c for c in cands if core.recommended(c, profile) and c.get("personas", {}).get(name, 0) >= 0.3][:3]
            if pending:
                print(f"  worth installing: {', '.join(c['id'] for c in pending)}")
        return
    names = list(known) if a.all else [a.name] if a.name in known else None
    if not names:
        raise SystemExit(f"name a persona ({', '.join(known)}) or pass --all")
    for name in names:
        p = known[name]
        skills = [n for n, _ in persona.fitting(installed, p["keywords"], p["max_skills"])]
        path = paths.agents / f"{p['agent']}.md"
        if a.dry_run:
            print(f"{path}: skills would be {', '.join(skills) or '(none fit yet)'}")
            continue
        what, added, dropped = persona.write_agent(path, p["agent"], p["description"], skills)
        print(f"{what} {path}" + (f"; added {', '.join(added)}" if added else "") + (f"; dropped {', '.join(dropped)}" if dropped else "")
              + ("" if added or dropped or what == "created" else "; skills unchanged"))
    print("Start one with `claude --agent <name>`, or ask for it by name in any session.")


if __name__ == "__main__":
    main()
