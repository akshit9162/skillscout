"""skillscout command line."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __doc__ as ABOUT, core, schedule

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
    sub.add_parser("init", help="copy the example profile to ~/.skillscout/profile.toml")
    d = sub.add_parser("discover", help="search GitHub, scan and score skills, write a report")
    d.add_argument("--full", action="store_true", help="ignore caches: fetch every repo and re-scan every skill")
    d.add_argument("--notify", action="store_true", help="desktop notification with the summary (macOS, or Linux with notify-send)")
    ls = sub.add_parser("list", help="show scored candidates")
    ls.add_argument("--all", action="store_true", help="include zero-relevance skills")
    ls.add_argument("-n", type=int, default=30)
    sh = sub.add_parser("show", help="print a candidate's flags and full SKILL.md")
    sh.add_argument("id")
    ap = sub.add_parser("approve", help="install one skill into ~/.claude/skills and pin it")
    ap.add_argument("id")
    ap.add_argument("--accept-risk", action="store_true", help="install despite high-risk flags")
    ap.add_argument("--update", action="store_true", help="replace a copy skillscout installed earlier")
    rm = sub.add_parser("remove", help="uninstall a skill skillscout installed")
    rm.add_argument("id")
    sub.add_parser("installed", help="show pinned skills")
    sub.add_parser("updates", help="diff pinned skills against upstream; applies nothing")
    sub.add_parser("schedule", help="set up weekly discovery for this OS (you turn it on)")
    a = p.parse_args(argv)

    paths = core.Paths()
    if a.cmd == "init":
        if USER_PROFILE.exists():
            raise SystemExit(f"{USER_PROFILE} already exists")
        USER_PROFILE.parent.mkdir(parents=True, exist_ok=True)
        USER_PROFILE.write_text(EXAMPLE.read_text())
        print(f"wrote {USER_PROFILE}; edit [keywords] to match your work, then run `skillscout discover`")
        return
    profile = core.load_profile(a.profile or (USER_PROFILE if USER_PROFILE.exists() else EXAMPLE))
    cands = core._read(paths.candidates, [])

    if a.cmd == "discover":
        cands = core.discover(profile, paths, full=a.full)
        out, summary = core.report(cands, profile, paths)
        print(f"{summary}\nreport: {out}")
        if a.notify:
            notify(summary)
    elif a.cmd == "list":
        rows = [c for c in cands if a.all or c["relevance"] > 0][: a.n]
        for c in rows:
            tag = "installed" if c["installed"] else ("REC" if core.recommended(c, profile) else "")
            print(f"{c['score']:.2f}  {c['risk'] or '-':6}  {tag:9}  {c['id']}")
    elif a.cmd == "show":
        c = core.find(cands, a.id)
        print(json.dumps({k: c[k] for k in ("id", "score", "stars", "license", "commit", "risk", "matched", "conflicts")}, indent=2))
        for f in c["flags"]:
            print(f"  [{f['level']}] {f['reason']} — {f['where']}")
        src = core.source_dir(c, paths)
        print(f"\nfiles: {', '.join(sorted(str(x.relative_to(src)) for x in src.rglob('*') if x.is_file()))}\n")
        print((src / "SKILL.md").read_text(errors="ignore"))
    elif a.cmd == "approve":
        c = core.find(cands, a.id)
        print(f"installed {c['id']} -> {core.approve(c, paths, a.accept_risk, a.update)}")
    elif a.cmd == "remove":
        print(f"removed {core.remove(a.id, paths)}")
    elif a.cmd == "installed":
        for cid, e in core._read(paths.lock, {}).items():
            print(f"{cid}  @{e['commit'][:10]}  {e['installed_at']}")
    elif a.cmd == "updates":
        found = core.updates(paths)
        for cid, commit, diff in found:
            print(f"\n=== {cid} -> {commit[:10]}\n{diff}")
        print(f"\n{len(found)} pinned skill(s) changed upstream." + (" Re-run `approve <id> --update` after reading the diff." if found else ""))
    elif a.cmd == "schedule":
        files, message = schedule.here(paths.state)
        schedule.write(files)
        print(message)


if __name__ == "__main__":
    main()
