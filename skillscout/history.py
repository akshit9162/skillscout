"""Your Claude Code history, read on this machine only and only when you ask: a digest of what you've worked
on, for you (or the agent in your session) to draft personas from, and keywords from a one-line description.

Word counting can't group short chat messages into kinds of work reliably, so grouping is left to a reader
who understands them; this module only gathers and condenses."""

import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .score import STOP, WORD, stem

HISTORY = Path.home() / ".claude" / "projects"
GAP = 45 * 60                 # seconds of silence that start a new episode
URL = re.compile(r"https?://\S+|www\.\S+")
PASTE = re.compile(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", re.S)
# Words that say nothing about the kind of work. Common topic words (`page`, `code`) aren't here: alone they're
# too frequent to survive vocabulary(), but they must stay available for phrases like `landing page`.
CHATTER = set("""make give can could would should also now just want need like please yes okay thanks thank
    then there here what which why how does doing done did lets let get got put see look check run try use
    using used new add added change changed fix fixed update updated same other more most much very really
    still again first last next back way thing things something everything anything all every each one two
    three good best better bad right wrong sure think know mean means said say tell told keep kept stop start
    working show wrote
    take didn don isn doesn wasn will been being have has had into onto over under about after before above
    below between only well even ever never always too our ours their them they him her his she its
    hello hey yeah dont doesnt cant wont whatever able
    goes made makes gave given took actually maybe already help helps helping based specific various
    http https www com org html url link links we my me up do so if at no go us am he ok oh hi""".split())
CLAUSE = re.compile(r"[,.;:!?()\[\]/|]+|\s(?:and|or|for|with|in|on|of|to|the)\s")


def episodes(root: Path = HISTORY, days: int | None = None) -> list[dict]:
    """Your typed messages, grouped into episodes (same folder, no gap over 45 minutes), newest first:
    {"folder", "start", "end", "messages", "title"}. Tool output, system text and pasted blocks are left out."""
    since = datetime.now().timestamp() - days * 86400 if days else None
    out = []
    for f in sorted(root.glob("*/*.jsonl")):
        title, mine, current = None, [], None
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "ai-title":
                title = title or d.get("aiTitle")
            if d.get("type") != "user" or d.get("isMeta"):
                continue
            text = typed(d.get("message", {}).get("content"))
            when = _time(d.get("timestamp"))
            if not text or (since and when and when < since):
                continue
            folder = d.get("cwd") or ""
            if not current or current["folder"] != folder or (when and current["end"] and when - current["end"] > GAP):
                current = {"folder": folder, "start": when, "end": when, "messages": [], "title": None}
                mine.append(current)
            current["messages"].append(text)
            current["end"] = when or current["end"]
        if mine and title:
            mine[0]["title"] = title
        out += mine
    return sorted(out, key=lambda e: -(e["start"] or 0))


def typed(content) -> str:
    """The part of a user record you typed: not tool results, command wrappers, system notes or pastes."""
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    if not isinstance(content, str):
        return ""
    text = " ".join(PASTE.sub(" ", URL.sub(" ", content)).split())
    if not text or text.startswith(("<", "[Request interrupted", "Caveat:", "This session is being continued")):
        return ""
    return text[:300]


def _time(stamp) -> float | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def digest(eps: list[dict], per_episode: int = 4, limit: int = 150) -> str:
    """A compact plain-text summary to read or hand to the agent: one block per episode, short messages only."""
    lines = []
    for e in eps[:limit]:
        when = datetime.fromtimestamp(e["start"]).strftime("%Y-%m-%d") if e["start"] else "?"
        folder = Path(e["folder"]).name or "~"
        head = f"## {when} · {folder}" + (f" · {e['title']}" if e["title"] else "") + f" · {len(e['messages'])} messages"
        msgs = [m[:140] for m in e["messages"] if len(m.split()) >= 3][:per_episode]
        if msgs:
            lines += [head] + [f"- {m}" for m in msgs] + [""]
    return "\n".join(lines)


def vocabulary(records: list[dict], low: int = 3, high: float = 0.04) -> dict[str, int]:
    """Terms (single words and two-word phrases) that skills use in their names and descriptions, with how
    many skills use each. Kept: at least `low` skills (so a keyword can match something) and at most `high`
    of them (`build`, `create` and the like match everything and so say nothing)."""
    df = Counter()
    for r in records:
        df.update(set(terms(f"{r['name'].replace('-', ' ')} {r['description']}")))
    cap = max(low, high * len(records))
    return {t: n for t, n in df.items() if low <= n <= cap}


def terms(text: str, surface: dict[str, str] | None = None) -> list[str]:
    """Content words (plural folded to singular) and adjacent pairs within a clause. `surface`, if given,
    collects each term's first spelling as written (`postgres`, not the folded `postgre`)."""
    out = []
    for clause in CLAUSE.split(f" {text.lower()} "):
        words = []
        for w in WORD.findall(clause):
            if w in CHATTER or w in STOP or w.isdigit() or len(w) < 2:
                continue
            s = stem(w)
            if s in CHATTER:
                continue
            words.append(s)
            if surface is not None:
                surface.setdefault(s, w)
        out += words
        for a, b in zip(words, words[1:]):
            out.append(f"{a} {b}")
            if surface is not None:
                surface.setdefault(f"{a} {b}", f"{surface[a]} {surface[b]}")
    return out


def keywords_from(about: str, vocab: dict[str, int], k: int = 10) -> dict[str, int]:
    """Profile keywords from a sentence about the work: its terms that skills also use, rarer ones weighted
    higher (`meta ads` says more than `marketing`), a phrase preferred over its own words. Weights 1 to 4."""
    surface: dict[str, str] = {}
    found = [t for t in dict.fromkeys(terms(about, surface)) if t in vocab]
    total = max(vocab.values(), default=1) / 0.04
    scored = sorted(((t, math.log(total / vocab[t]) * (1.5 if " " in t else 1)) for t in found), key=lambda x: -x[1])
    out: dict[str, float] = {}
    for t, w in scored:
        if any(t in o.split() for o in out):          # its phrase is already in
            continue
        out = {o: v for o, v in out.items() if o not in t.split()} | {t: w}
        if len(out) == k:
            break
    hi = max(out.values(), default=1)
    return {surface[t]: max(1, round(4 * w / hi)) for t, w in out.items()}
