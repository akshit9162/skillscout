"""Personas: named kinds of work, each with its own keywords and, optionally, a Claude Code agent whose
`skills:` list skillscout keeps current. Everything else in the agent file is yours and is never touched."""

import json
import re
from pathlib import Path

from .score import relevance
from .skill import parse_frontmatter

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def personas(profile: dict) -> dict[str, dict]:
    """name -> {"description", "keywords", "agent", "max_skills"} from the profile's [personas.*] tables."""
    out = {}
    for name, p in profile.get("personas", {}).items():
        out[name] = {"description": p.get("description", ""), "keywords": p.get("keywords", {}),
                     "agent": p.get("agent", name), "max_skills": p.get("max_skills", 5)}
    return out


def keyword_sets(profile: dict) -> dict[str, dict[str, float]]:
    """Every keyword set to score against: the main [keywords] (as "") and each persona's."""
    sets = {"": profile.get("keywords", {})} if profile.get("keywords") else {}
    return sets | {n: p["keywords"] for n, p in personas(profile).items() if p["keywords"]}


def add(profile_path: Path, name: str, description: str, keywords: dict[str, int], agent: str | None = None) -> str:
    """Append a [personas.<name>] table to the profile file. Refuses a name that's taken."""
    if not NAME.fullmatch(name):
        raise SystemExit(f"persona names are lowercase letters, digits and dashes: {name!r}")
    text = profile_path.read_text(encoding="utf-8")
    if re.search(rf"^\[personas\.{re.escape(name)}\]", text, re.M):
        raise SystemExit(f"{name} is already in {profile_path}; edit it there")
    if not keywords:
        raise SystemExit("no keywords: describe the work in more specific words, or pass --keywords")
    block = [f"\n[personas.{name}]", f"description = {json.dumps(description)}"]
    if agent and agent != name:
        block.append(f"agent = {json.dumps(agent)}")
    block += ["max_skills = 5", f"[personas.{name}.keywords]"] + [f"{json.dumps(k)} = {v}" for k, v in keywords.items()]
    profile_path.write_text(text.rstrip("\n") + "\n" + "\n".join(block) + "\n", encoding="utf-8")
    return "\n".join(block).strip()


def fitting(installed: dict[str, str], keywords: dict[str, float], k: int, at: float = 0.2) -> list[tuple[str, float]]:
    """The installed skills (name -> description) that best fit a keyword set, best first."""
    scored = []
    for name, desc in installed.items():
        rel, _ = relevance(f"{name.split(':')[-1].replace('-', ' ')} {desc}", keywords)
        if rel >= at:
            scored.append((name, rel))
    return sorted(scored, key=lambda x: (-x[1], x[0]))[:k]


def write_agent(path: Path, name: str, description: str, skills: list[str]) -> tuple[str, list[str], list[str]]:
    """Create the agent file, or replace only the `skills:` list in an existing one.
    Returns (what happened, skills added, skills dropped)."""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise SystemExit(f"{path} has no frontmatter; add `---` blocks or remove the file")
        end = text.index("\n---", 3)
        head, body = text[: end], text[end:]
        before = _skills(head)
        head = _strip_skills(head).rstrip("\n") + "\n" + _skills_block(skills)
        path.write_text(head.rstrip("\n") + body, encoding="utf-8")
        return "updated", [s for s in skills if s not in before], [s for s in before if s not in skills]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""---
name: {name}
description: {json.dumps(description or f"The {name} side of my work")}
{_skills_block(skills)}---

{description}

The skills listed above load when this agent starts; `skillscout persona build {name}` keeps that list
current with the best installed skills for this work. Everything else in this file is yours to edit:
role, tone, rules, tools, model.
""", encoding="utf-8")
    return "created", skills, []


def _skills(head: str) -> list[str]:
    m = re.search(r"^skills:[ \t]*\n((?:[ \t]+-.*\n?)*)", head + "\n", re.M)
    if m:
        return [line.strip()[1:].strip().strip("\"'") for line in m.group(1).splitlines() if line.strip()]
    inline = parse_frontmatter(head + "\n---").get("skills", "")
    return [s.strip().strip("\"'") for s in inline.strip("[]").split(",") if s.strip()]


def _strip_skills(head: str) -> str:
    head = re.sub(r"^skills:[ \t]*\n(?:[ \t]+-.*\n?)*", "", head + "\n", flags=re.M)
    return re.sub(r"^skills:.*\n?", "", head, flags=re.M)


def _skills_block(skills: list[str]) -> str:
    return "skills:\n" + "".join(f"  - {s}\n" for s in skills) if skills else ""
