"""Switching personas: Claude Code lists only the skills and plugins for the work at hand.

Every installed skill's description loads into every session, so a big collection crowds the context.
`persona use brand` hides your own skills that belong to other personas (`skillOverrides: "off"` in
~/.claude/settings.json) and turns off plugins pinned only to other personas (`enabledPlugins`; plugins
switch whole). Nothing moves on disk.

skillscout only changes back what it changed: it remembers each entry's value before it first touched it,
restores that when the entry is no longer needed, and leaves alone anything you edited in between. Your own
skillOverrides entries are never overwritten. `persona use --clear` puts everything back."""

import json
from pathlib import Path

from .persona import fitting, personas
from .skill import parse_frontmatter

SELF = "skillscout"          # its own skill and plugin are never hidden
ALL = 10**6


def plugin_skills(paths) -> dict[str, dict[str, str]]:
    """plugin name -> {skill: description} for every plugin in the cache, enabled or not."""
    out: dict[str, dict[str, str]] = {}
    if not paths.plugins.exists():
        return out
    for md in paths.plugins.rglob("SKILL.md"):
        rel = md.relative_to(paths.plugins).parts
        if len(rel) < 3 or ".git" in rel or "node_modules" in rel:
            continue
        meta = parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
        out.setdefault(rel[1], {})[meta.get("name") or md.parent.name] = meta.get("description", "")
    return out


def draft_plugins(paths, keywords: dict, at: float = 0.25) -> list[str]:
    """Plugins most of whose skills fit a persona's keywords (at least two, and a quarter of them)."""
    out = []
    for name, skills in sorted(plugin_skills(paths).items()):
        if name == SELF:
            continue
        fit = len(fitting(skills, keywords, ALL))
        if fit >= 2 and fit >= at * len(skills):
            out.append(name)
    return out


def own_skills(installed: dict[str, str]) -> dict[str, str]:
    return {n: d for n, d in installed.items() if ":" not in n}


def claims(profile: dict, own: dict[str, str]) -> dict[str, set[str]]:
    """persona -> the user's own skills it claims: those fitting its keywords, plus `skills`, minus `exclude`."""
    out = {}
    for name, p in personas(profile).items():
        fit = {n for n, _ in fitting(own, p["keywords"], ALL)} if p["keywords"] else set()
        out[name] = (fit | set(p["skills"])) - set(p["exclude"])
    return out


def plugin_keys(pins: list[str], enabled: dict) -> set[str]:
    """Resolve `marketing` or `marketing@market` to the keys used in enabledPlugins."""
    keys = set()
    for pin in pins:
        keys |= {k for k in enabled if k == pin or k.split("@")[0] == pin}
    return keys


def plan(profile: dict, own: dict[str, str], enabled: dict, active: list[str]) -> tuple[set[str], dict[str, bool]]:
    """(own skills to hide, plugin key -> on/off) for this set of active personas."""
    known = personas(profile)
    cfg = profile.get("switch", {})
    by = claims(profile, own)
    active = [a for a in active if a in known]
    keep = set(cfg.get("core", [])) | {SELF} | set().union(*(by[a] for a in active))
    pool = set(own) if cfg.get("others", "on") == "off" else set().union(*by.values())
    hide = {s for s in pool - keep if s in own}
    pinned = {n: plugin_keys(p["plugins"], enabled) for n, p in known.items()}
    on = set().union(*(pinned[a] for a in active))
    plugins = {k: k in on for k in set().union(*pinned.values()) if k.split("@")[0] != SELF}
    return hide, plugins


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path: Path, data) -> None:
    """Atomic, and keeps the file's key order (settings.json is yours, not ours to reshuffle)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".skillscout.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def apply(paths, active: list[str], hide: set[str], plugins: dict[str, bool]) -> dict:
    """Make settings.json match the plan and report what changed. State (~/.skillscout/switch.json) keeps,
    for each entry skillscout set, the value it had before; an entry you changed since is yours again."""
    state_path = paths.state / "switch.json"
    state = _load(state_path, {"active": [], "skills": {}, "plugins": {}})
    settings = _load(paths.settings, {})
    over = dict(settings.get("skillOverrides", {}))
    ep = dict(settings.get("enabledPlugins", {}))
    done = {"hidden": [], "shown": [], "plugins_on": [], "plugins_off": [], "kept_yours": []}

    for name, before in sorted(state["skills"].items()):
        if over.get(name) != "off":                            # you changed it since: yours now
            del state["skills"][name]
        elif name not in hide:                                 # ours and no longer needed: restore
            over.pop(name) if before is None else over.__setitem__(name, before)
            del state["skills"][name]
            done["shown"].append(name)
    for name in sorted(hide - set(state["skills"])):
        if name in over:                                       # your own entry: never overwritten
            done["kept_yours"].append(name)
            continue
        state["skills"][name] = None
        over[name] = "off"
        done["hidden"].append(name)

    for key, (before, was_set) in sorted(state["plugins"].items()):
        if ep.get(key) != was_set:
            del state["plugins"][key]
        elif key not in plugins:
            ep[key] = before
            del state["plugins"][key]
            done["plugins_on" if before else "plugins_off"].append(key)
    for key, want in sorted(plugins.items()):
        before = state["plugins"][key][0] if key in state["plugins"] else ep.get(key, False)
        if ep.get(key, False) != want:
            ep[key] = want
            done["plugins_on" if want else "plugins_off"].append(key)
        if want == before:
            state["plugins"].pop(key, None)                    # back where it started: nothing to undo
        else:
            state["plugins"][key] = [before, want]

    if over != settings.get("skillOverrides", {}) or ep != settings.get("enabledPlugins", {}):
        if over:
            settings["skillOverrides"] = over
        else:
            settings.pop("skillOverrides", None)
        if ep or "enabledPlugins" in settings:
            settings["enabledPlugins"] = ep
        _save(paths.settings, settings)
    state["active"] = active
    _save(state_path, state)
    return done


def active(paths) -> list[str]:
    return _load(paths.state / "switch.json", {}).get("active", [])


CORE = "(core only)"


def use(profile: dict, paths, names: list[str], installed: dict[str, str], core_only: bool = False) -> dict:
    """Activate these personas; none and not core_only means clear. core_only hides every persona's skills
    and pinned plugins, leaving `[switch] core` and whatever no persona claims."""
    known = personas(profile)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SystemExit(f"unknown persona {', '.join(unknown)}; you have {', '.join(known) or 'none'}")
    if not names and not core_only:
        return apply(paths, [], set(), {})
    enabled = _load(paths.settings, {}).get("enabledPlugins", {})
    hide, plugins = plan(profile, own_skills(installed), enabled, names)
    return apply(paths, names or [CORE], hide, plugins)
