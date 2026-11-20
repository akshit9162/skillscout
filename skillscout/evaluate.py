"""Offline evaluation of the ranking against a hand-graded pool (tests/eval/pool.json), Cranfield style:
a ranking change counts as better only if these numbers go up."""

import json
import math
import tempfile
from datetime import datetime
from pathlib import Path

from . import core
from .score import diversify

POOL = Path(__file__).resolve().parent.parent / "tests" / "eval" / "pool.json"


def ndcg(grades: list[int], all_grades: list[int], k: int) -> float:
    dcg = lambda gs: sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gs[:k]))
    ideal = dcg(sorted(all_grades, reverse=True))
    return dcg(grades) / ideal if ideal else 0.0


def ranked(pool: dict, diversity: float = 0.3) -> list[dict]:
    """The pool in the order the report would show it, scored as of the pool's date on an empty machine."""
    graded = {s["id"]: s["grade"] for s in pool["skills"]}
    records = [{**s, "repo": s["id"].split(":")[0], "path": s["id"].split(":", 1)[1], "flags": [], "risk": None}
               for s in pool["skills"]]
    profile = {"keywords": pool["profile"], "search": {"trusted": []},
               "scoring": {"recommend_at": 0, "overlap_at": 0.35, "diversity": diversity}}
    with tempfile.TemporaryDirectory() as d:
        paths = core.Paths(Path(d) / "state", Path(d) / "skills", Path(d) / "p" / "c" / "plugins")
        paths.skills.mkdir(parents=True)
        cands = core.score(records, profile, paths, datetime.fromisoformat(pool["now"].replace("Z", "+00:00")))
    return [{**c, "grade": graded[c["id"]]} for c in diversify(cands, 10, diversity)]


def evaluate(pool: dict | None = None, k: int = 10, diversity: float = 0.3) -> dict:
    pool = pool or json.loads(POOL.read_text(encoding="utf-8"))
    order = ranked(pool, diversity)
    grades = [c["grade"] for c in order]
    return {"ndcg": ndcg(grades, grades, k), "precision": sum(g > 0 for g in grades[:k]) / k,
            "junk_in_top": [c["id"] for c in order[:k] if c["grade"] == 0], "order": order}


if __name__ == "__main__":
    r = evaluate()
    print(f"nDCG@10 {r['ndcg'] * 100:.1f}  precision@10 {r['precision'] * 100:.0f}")
    for c in r["order"][:10]:
        print(f"  {c['grade']}  {c['score']:.3f}  {c['id']}")
