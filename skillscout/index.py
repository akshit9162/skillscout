"""The shared weekly index: one crawl of the ecosystem, published for everyone to score locally."""

import gzip
import hashlib
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://github.com/akshit9162/skillscout/releases/download/index/index.json.gz"
MIRRORS = ("https://raw.githubusercontent.com/akshit9162/skillscout/index-data/index.json.gz",
           "https://cdn.jsdelivr.net/gh/akshit9162/skillscout@index-data/index.json.gz")
TIMEOUT = 15            # seconds without a byte before a source counts as down
VERSION = 1
FIELDS = ("id", "repo", "repo_id", "name", "description", "path", "commit", "stars", "license", "pushed_at", "flags", "risk", "scan_key", "audits")


class Unavailable(Exception):
    pass


def build(records: list[dict], out: Path, rules: str) -> dict:
    data = {"version": VERSION, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rules": rules, "skills": [{k: r.get(k) for k in FIELDS} for r in records]}
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    return data


def load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != VERSION:
        raise Unavailable(f"index version {data.get('version')} isn't supported; upgrade skillscout")
    return data


def sources(url: str) -> list[str]:
    """Where to try, in order. The default index is also published to the `index-data` branch, byte for byte,
    for networks that reset GitHub release downloads; a custom url is tried alone."""
    return [url, *MIRRORS] if url == URL else [url]


def repo_of(url: str) -> str | None:
    m = re.match(r"https://(?:github\.com/([^/]+/[^/]+)/releases/download/|raw\.githubusercontent\.com/([^/]+/[^/]+)/"
                 r"|cdn\.jsdelivr\.net/gh/([^/@]+/[^/@]+)@)", url)
    return next((g for g in m.groups() if g), None) if m else None


def verify(path: Path, url: str) -> None:
    """Check the GitHub attestation the weekly job signs the index with: built by that repo's index workflow,
    from that repo's code, byte for byte. Needs the GitHub CLI."""
    repo = repo_of(url)
    if not repo:
        raise Unavailable(f"can't verify an index that isn't published from a GitHub repo: {url}")
    if not shutil.which("gh"):
        raise Unavailable("verifying the index needs the GitHub CLI (`gh`); install it or set [index] verify = false")
    run = subprocess.run(["gh", "attestation", "verify", str(path), "--repo", repo,
                          "--signer-workflow", f"{repo}/.github/workflows/index.yml"], capture_output=True, text=True)
    if run.returncode:
        raise Unavailable(f"the index failed verification: {(run.stderr or run.stdout).strip()[:300]}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(url: str, cache: Path, check: bool = False) -> dict:
    """Download the index if it changed since the cached copy (ETag), trying the mirrors when the release
    download fails; fall back to the cached copy offline. With `check`, an index that fails attestation is
    never used, including a copy cached before `check` was turned on."""
    tag, mark = cache.with_suffix(".etag"), cache.with_suffix(".verified")
    errors = []
    for src in sources(url):
        req = urllib.request.Request(src, headers={"User-Agent": "skillscout"})
        if src == url and cache.exists() and tag.exists():
            req.add_header("If-None-Match", tag.read_text(encoding="utf-8").strip())
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                cache.parent.mkdir(parents=True, exist_ok=True)
                part = cache.with_suffix(".part")
                part.write_bytes(r.read())
                load(part)                              # a truncated or foreign file never replaces a good one
                if check:
                    try:
                        verify(part, src)
                    except Unavailable:
                        part.unlink()
                        raise
                part.replace(cache)
                etag = r.headers.get("ETag") if src == url else None
                tag.write_text(etag, encoding="utf-8") if etag else tag.unlink(missing_ok=True)
                mark.write_text(_sha(cache), encoding="utf-8") if check else mark.unlink(missing_ok=True)
                break
        except urllib.error.HTTPError as e:
            if e.code == 304:
                break
            errors.append(f"{urllib.parse.urlsplit(src).hostname}: HTTP {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors.append(f"{urllib.parse.urlsplit(src).hostname}: {getattr(e, 'reason', e)}")
    else:
        if not cache.exists():
            raise Unavailable(f"couldn't download the index ({'; '.join(errors)}); run `skillscout discover --local`")
        print(f"index download failed; using the copy built {load(cache)['built_at'][:10]}")
    if check and not (mark.exists() and mark.read_text(encoding="utf-8").strip() == _sha(cache)):
        verify(cache, url)
        mark.write_text(_sha(cache), encoding="utf-8")
    return load(cache)
