"""Relevance to your profile, popularity, freshness, and overlap with skills you already have."""

import functools
import math
import re
from datetime import datetime, timezone

WORD = re.compile(r"[a-z0-9]+")
STOP = set("a an and the of to for in on with when use used using or is are be this that it its your you any as by from".split())


def tokens(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 2}


def stem(word: str) -> str:
    """Plural to singular, so `agents` matches `agent`; applied to both sides. Only plurals: stripping `-ing`
    would make `accounting` match `account`."""
    if word.endswith("ies") and len(word) > 5:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def _words(text: str) -> str:
    return " ".join(stem(w) for w in WORD.findall(text.lower()))


@functools.lru_cache(maxsize=1024)
def _phrase(keyword: str) -> str:
    return _words(keyword)


# A word after one of these, in the same clause, is something the skill says it does not do.
NEGATION = re.compile(r"\b(?:not|cannot|can't|can’t|doesn't|doesn’t|don't|don’t|won't|won’t|isn't|isn’t|never|no longer)\b")
CLAUSE = re.compile(r"[,;:()]|\bbut\b")
SENTENCE = re.compile(r"[.!?\n]")
# A cue with nothing after it ("Not for:", "Don't use for:") negates the list that follows.
BARE = re.compile(r"^\W*(?:(?:to\W+)?(?:be\W+)?(?:use|used)\W+)?(?:for|on|with)?\W*$")


def _affirmed(text: str) -> str:
    """The text without what it says it does not do: each clause is cut at its first negation cue,
    so "Deploys to Kubernetes, but does not handle Helm" keeps Kubernetes and drops Helm. A bare cue
    ("Not for: A, B") drops the rest of its sentence."""
    kept = []
    for sentence in SENTENCE.split(text.lower()):
        for clause in CLAUSE.split(sentence):
            cue = NEGATION.search(clause)
            if not cue:
                kept.append(clause)
                continue
            kept.append(clause[:cue.start()])
            if BARE.match(clause[cue.end():]):
                break
    return " ".join(kept)


def relevance(text: str, keywords: dict[str, float]) -> tuple[float, list[str]]:
    """Share of the best achievable keyword weight this text hits, capped so 3 strong matches reach 1.
    A keyword phrase matches when its words appear in order, in any word form, outside what the text
    says it does not do."""
    low = f" {_words(_affirmed(text))} "
    hits = [k for k in keywords if f" {_phrase(k)} " in low]
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


def combined(rel: float, pop: float, fresh: float, trusted: bool, effectiveness: float | None = None) -> float:
    """Relevance plus a quality prior. Stars measure attention, not whether a skill works, so once a skill has
    a measured effectiveness (the same task with and without it) that takes most of the stars' weight."""
    quality = 0.2 * pop if effectiveness is None else 0.05 * pop + 0.15 * effectiveness
    return round(min(0.65 * rel + quality + 0.15 * fresh + (0.1 if trusted else 0), 1.0), 3)


def feedback(text: str, kept: list[set[str]], rejected: list[set[str]], overlap_at: float) -> float:
    """Your own decisions, the strongest signal there is: nearness to a skill you kept raises a score, nearness
    to one you rejected lowers it more. Nearness is measured against overlap_at, the similarity that already
    counts as a duplicate, so a near-copy gets the full amount."""
    mine = tokens(text)
    near = lambda pool: max((min(similarity(mine, t) / overlap_at, 1.0) for t in pool), default=0.0)
    return round(0.1 * near(kept) - 0.3 * near(rejected), 3)


def diversify(items: list[dict], top: int = 25, weight: float = 0.3) -> list[dict]:
    """Maximal marginal relevance for the first `top`: each pick trades its score against how much it repeats
    one already picked, so five copies of one idea don't fill the list. The rest keep their order.
    weight 0 is plain score order."""
    if weight <= 0 or len(items) < 3:
        return list(items)
    pool = items[: top * 4]
    toks = {id(c): tokens(f"{c['name']} {c['description']}") for c in pool}
    picked, left = [], list(pool)
    while left and len(picked) < top:
        best = max(left, key=lambda c: (1 - weight) * c["score"]
                   - weight * max((similarity(toks[id(c)], toks[id(p)]) for p in picked), default=0.0))
        picked.append(best)
        left.remove(best)
    return picked + left + items[top * 4:]


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
