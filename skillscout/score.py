"""Relevance to your profile, popularity, freshness, and overlap with skills you already have."""

import math
import re
from datetime import datetime, timezone

WORD = re.compile(r"[a-z0-9]+")
STOP = set("a an and the of to for in on with when use used using or is are be this that it its your you any as by from".split())


def tokens(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 2}


def relevance(text: str, keywords: dict[str, float]) -> tuple[float, list[str]]:
    """Share of the best achievable keyword weight this text hits, capped so 3 strong matches reach 1."""
    low = " " + " ".join(WORD.findall(text.lower())) + " "
    hits = [k for k in keywords if f" {' '.join(WORD.findall(k.lower()))} " in low]
    top = sorted(keywords.values(), reverse=True)[:3]
    return min(sum(keywords[k] for k in hits) / max(sum(top), 1e-9), 1.0), hits


def popularity(stars: int) -> float:
    return min(math.log10(max(stars, 1)) / 4, 1.0)          # 10k stars = 1.0


def freshness(pushed_at: str | None, now: datetime | None = None) -> float:
    if not pushed_at:
        return 0.0
    now = now or datetime.now(timezone.utc)
    days = (now - datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))).days
    return max(0.0, 1 - days / 365)


def combined(rel: float, pop: float, fresh: float, trusted: bool) -> float:
    return round(min(0.65 * rel + 0.2 * pop + 0.15 * fresh + (0.1 if trusted else 0), 1.0), 3)


def similarity(a: str | set[str], b: str | set[str]) -> float:
    ta = a if isinstance(a, set) else tokens(a)
    tb = b if isinstance(b, set) else tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


_token_cache: dict[str, set[str]] = {}


def conflicts(name: str, description: str, installed: dict[str, str], overlap_at: float) -> list[str]:
    """Installed skills with the same name or a description that would trigger on the same prompts."""
    out, mine = [], tokens(description)
    for other, desc in installed.items():
        short = other.split(":")[-1]
        if short == name:
            out.append(f"same name as installed `{other}`")
            continue
        theirs = _token_cache.get(desc)
        if theirs is None:
            theirs = _token_cache[desc] = tokens(desc)
        if (s := similarity(mine, theirs)) >= overlap_at:
            out.append(f"overlaps `{other}` ({s:.0%} similar)")
    return out
