"""Static safety checks on a skill folder. Flags things to read before installing; it can't prove a skill safe."""

import re
from dataclasses import dataclass
from pathlib import Path

HIGH, MEDIUM, LOW = "high", "medium", "low"

# (level, reason, regex, trigger words). Regexes run on lowercased text and only when a trigger word
# appears, which keeps scanning fast (case-insensitive regex disables Python's literal-prefix search).
PATTERNS = [
    (HIGH, "pipes a download into a shell", r"(curl|wget)[^\n|]{0,200}\|\s*(sudo\s+)?(ba|z)?sh\b", ("curl", "wget")),
    (HIGH, "runs sudo", r"\bsudo\s+\w", ("sudo",)),
    (HIGH, "deletes recursively from a broad path", r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$home)(\s|$|/\*)", ("rm ",)),
    (HIGH, "reads SSH keys or the keychain", r"~/\.ssh|\.ssh/id_|security\s+find-(generic|internet)-password|git credential fill",
     (".ssh", "find-generic", "find-internet", "credential fill")),
    (HIGH, "decodes and executes", r"base64\s+(-d|--decode)[^\n]{0,200}\|\s*(ba|z)?sh", ("base64",)),
    (HIGH, "tells the agent to ignore its instructions",
     r"ignore (all |any )?(previous|prior|above) instructions|disregard (your|the) (system|previous)", ("ignore", "disregard")),
    (HIGH, "tells the agent to hide actions from the user",
     r"without (telling|informing|asking) the user|(do not|don't|never) (tell|inform|show) the user (about|that you|what you)"
     r"|(hide|conceal|keep) (this|it|these|that) (secret |hidden )?from the user", (" the user",)),
    (HIGH, "uploads local files", r"curl[^\n]{0,200}?\s(-f|--data(-binary)?|-d)\s+@|scp\s+\S+\s+\S+@", ("curl", "scp ")),
    (MEDIUM, "reads .env or API keys", r"cat\s+[^\n]{0,120}?\.env\b|printenv|env\s*\|\s*grep|(api|secret)_key\s*=\s*\$",
     (".env", "printenv", "env", "_key")),
    (MEDIUM, "pushes or force-pushes git", r"git\s+push\s+(-f|--force)", ("git push",)),
    (MEDIUM, "edits shell profiles or Claude settings", r"(>>|>)\s*~/\.(zshrc|bashrc|profile|claude/settings)",
     ("zshrc", "bashrc", "profile", "claude/settings")),
    (MEDIUM, "disables permission prompts", r"dangerously-?skip-?permissions|bypasspermissions", ("dangerously", "bypasspermissions")),
]
HIDDEN = re.compile("[\u200b\u200c\u2060\u202a-\u202e\u2066-\u2069]")   # zero-width + bidi controls; not ZWJ (emoji) or BOM
SCRIPT_EXT = {".sh", ".py", ".js", ".cjs", ".mjs", ".ts", ".rb", ".ps1", ".cmd", ".bat", ""}
MAX_SCAN = 512_000   # bytes; bigger text files (minified bundles, data) are flagged unscanned
NETWORK = re.compile(r"\b(requests\.|urllib|fetch\(|https?://|socket\.|XMLHttpRequest|axios)")


@dataclass
class Flag:
    level: str
    reason: str
    where: str


COMPILED = [(level, reason, re.compile(pat), words) for level, reason, pat, words in PATTERNS]
MAX_FILES = 400       # per skill; a skill at a repo's root can otherwise mean scanning the whole repo
_file_cache: dict[tuple[str, float], list[tuple[str, str, int | None]]] = {}


def _scan_file(f: Path) -> list[tuple[str, str, int | None]]:
    """(level, reason, line) findings for one file, memoised per run so nested skills don't rescan."""
    key = (str(f), f.stat().st_mtime)
    if key in _file_cache:
        return _file_cache[key]
    out: list[tuple[str, str, int | None]] = []
    if f.stat().st_size > MAX_SCAN:
        out.append((MEDIUM, "file too large to scan", None))
    else:
        try:
            text = f.read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            out.append((MEDIUM, "bundled binary file", None))
        else:
            low = text.lower()
            for level, reason, rx, words in COMPILED:
                if any(w in low for w in words) and (m := rx.search(low)):
                    out.append((level, reason, low.count("\n", 0, m.start()) + 1))
            if HIDDEN.search(text):
                out.append((HIGH, "contains hidden or direction-changing Unicode", None))
            if f.suffix in SCRIPT_EXT and f.name != "SKILL.md" and (f.suffix or text.startswith("#!")):
                net = bool(NETWORK.search(text))
                out.append((MEDIUM if net else LOW, "bundled script that makes network calls" if net else "bundled script", None))
    if f.name in {"hooks.json", "settings.json"}:
        out.append((MEDIUM, "defines hooks or settings", None))
    _file_cache[key] = out
    return out


def scan(skill_dir: Path, repo_root: Path | None = None) -> list[Flag]:
    flags, files = [], 0
    for f in sorted(skill_dir.rglob("*")):
        if ".git" in f.relative_to(skill_dir).parts:
            continue
        rel = str(f.relative_to(skill_dir))
        if f.is_symlink():
            flags.append(Flag(MEDIUM, "symlink (could point outside the skill)", rel))
            continue
        if not f.is_file():
            continue
        files += 1
        if files > MAX_FILES:
            flags.append(Flag(MEDIUM, f"more than {MAX_FILES} files; the rest weren't scanned", "."))
            break
        flags += [Flag(level, reason, f"{rel}:{line}" if line else rel) for level, reason, line in _scan_file(f)]
    if repo_root and not any((repo_root / n).exists() for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")):
        flags.append(Flag(LOW, "repo has no licence file", "repo"))
    return flags


def worst(flags: list[Flag]) -> str | None:
    for level in (HIGH, MEDIUM, LOW):
        if any(f.level == level for f in flags):
            return level
    return None
