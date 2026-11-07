"""Second opinions from skills.sh: the Gen, Socket and Snyk audits Vercel's `skills` CLI shows before an install.

Fetched once a week by the index job and stored in the signed index, so your machine never calls skills.sh.
"""

import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "https://www.skills.sh/tele/audit"
PARTNERS = {"ath": "Gen", "socket": "Socket", "snyk": "Snyk"}
SEVERE = {"high", "critical"}
CHUNK = 40          # skill names per request, to keep the URL short


def fetch(repo: str, names: list[str], timeout: float = 15) -> dict:
    """{skill name: {partner: {risk, alerts?, analyzedAt}}} for one repo; {} on any failure."""
    if not names or repo.count("/") != 1:
        return {}
    q = urllib.parse.urlencode({"source": repo, "skills": ",".join(names)})
    try:
        req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": "skillscout-index"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def attach(records: list[dict], fetcher=fetch, workers: int = 8) -> int:
    """Set each record's `audits` (empty when skills.sh has none). Returns how many records got one."""
    jobs = []
    by_repo: dict[str, list[dict]] = {}
    for r in records:
        by_repo.setdefault(r["repo"], []).append(r)
    for repo, rs in by_repo.items():
        names = sorted({r["name"] for r in rs})
        jobs += [(repo, names[i:i + CHUNK]) for i in range(0, len(names), CHUNK)]
    found: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (repo, _), data in zip(jobs, pool.map(lambda j: fetcher(*j), jobs)):
            for name, audit in data.items():
                if isinstance(audit, dict) and audit:
                    found[f"{repo}\0{name}"] = audit
    for r in records:
        r["audits"] = found.get(f"{r['repo']}\0{r['name']}", {})
    return sum(1 for r in records if r["audits"])


def severe(audit: dict | None) -> list[str]:
    """The partners that rate it high or critical."""
    return [PARTNERS.get(p, p) for p, a in (audit or {}).items() if isinstance(a, dict) and a.get("risk") in SEVERE]


def blocked(audit: dict | None) -> bool:
    """Two or more auditors agree it's high or critical. One alone is a note: they disagree often."""
    return len(severe(audit)) >= 2


def summary(audit: dict | None) -> str:
    parts = []
    for p, a in (audit or {}).items():
        if isinstance(a, dict) and a.get("risk"):
            alerts = a.get("alerts")
            parts.append(f"{PARTNERS.get(p, p)} {a['risk']}" + (f" ({alerts} alert{'s' if alerts != 1 else ''})" if alerts else ""))
    return ", ".join(parts) or "not audited"
