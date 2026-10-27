"""Reading SKILL.md files."""

from dataclasses import dataclass, field
from pathlib import Path


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
    """Every SKILL.md under root, skipping vendored and VCS folders and duplicate names."""
    skills, seen = [], set()
    for md in sorted(root.rglob("SKILL.md")):
        if any(part in {".git", "node_modules", "dist", "build"} for part in md.parts) or md.is_symlink() or not md.is_file():
            continue
        meta = parse_frontmatter(md.read_text(errors="ignore"))
        name = meta.get("name") or md.parent.name
        if name in seen:
            continue
        seen.add(name)
        files = sorted(str(p.relative_to(md.parent)) for p in md.parent.rglob("*") if p.is_file() and not p.is_symlink())
        skills.append(Skill(name, meta.get("description", ""), md.parent, files))
    return skills
