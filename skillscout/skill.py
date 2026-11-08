"""Reading SKILL.md files."""

import re
from dataclasses import dataclass, field
from pathlib import Path

# A name becomes a folder under the skills directory, so it can't carry a path: no slashes, no leading dot
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,99}")
VENDORED = {".git", "node_modules", "dist", "build"}        # never part of a skill
LICENCE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")


def skill_files(folder: Path) -> list[Path]:
    """A skill's own files, sorted: no symlinks (one could lead to ~/.ssh) and nothing under .git.
    Every reader of a skill's files goes through here: scan, digest, show, updates."""
    return sorted(p for p in folder.rglob("*") if ".git" not in p.relative_to(folder).parts
                  and not p.is_symlink() and p.is_file())


def licensed(root: Path) -> bool:
    return any((root / n).exists() for n in LICENCE_FILES)


def safe_name(name: str) -> bool:
    return bool(SAFE_NAME.fullmatch(name)) and ".." not in name and name == name.strip()


@dataclass
class Skill:
    name: str
    description: str
    path: Path                      # directory holding SKILL.md
    files: list[str] = field(default_factory=list)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML frontmatter reader: `key: value`, quoted values and folded (`>`, `|`) blocks."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta, key, block = {}, None, []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if key and (line.startswith((" ", "\t")) or not line.strip()):
            block.append(line.strip())
            continue
        if key:
            meta[key], key, block = " ".join(b for b in block if b), None, []
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            v = v.strip()
            if v in (">", ">-", "|", "|-"):
                key = k.strip()
            else:
                meta[k.strip()] = v.strip("\"'")
    if key:
        meta[key] = " ".join(b for b in block if b)
    return meta


def find_skills(root: Path) -> list[Skill]:
    """Every SKILL.md under root, skipping vendored and VCS folders. A name that isn't a plain folder name
    (`../x`, `a/b`) falls back to the folder's own name. Duplicate names are kept; the caller tells them apart."""
    skills = []
    for md in sorted(root.rglob("SKILL.md")):
        if VENDORED & set(md.relative_to(root).parts) or md.is_symlink() or not md.is_file():
            continue
        meta = parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
        name = meta.get("name") or md.parent.name
        if not safe_name(name):
            name = md.parent.name
            if not safe_name(name):
                continue
        files = [str(p.relative_to(md.parent)) for p in skill_files(md.parent)]
        skills.append(Skill(name, meta.get("description", ""), md.parent, files))
    return skills


def plugin_skills(cache: Path) -> dict[str, dict[str, str]]:
    """plugin name -> {skill: description} for every plugin in Claude Code's plugin cache, enabled or not.
    The cache is laid out <marketplace>/<plugin>/<version>/..."""
    out: dict[str, dict[str, str]] = {}
    if not cache.exists():
        return out
    for md in sorted(cache.rglob("SKILL.md")):
        rel = md.relative_to(cache).parts
        if len(rel) < 3 or VENDORED & set(rel):
            continue
        meta = parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
        out.setdefault(rel[1], {})[meta.get("name") or md.parent.name] = meta.get("description", "")
    return out
