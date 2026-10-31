"""The shared weekly index: one crawl of the ecosystem, published for everyone to score locally."""

import gzip
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://github.com/akshit9162/skillscout/releases/download/index/index.json.gz"
VERSION = 1
FIELDS = ("id", "repo", "repo_id", "name", "description", "path", "commit", "stars", "license", "pushed_at", "flags", "risk", "scan_key")


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


def verify(path: Path, url: str) -> None:
    """Check the GitHub attestation the weekly job signs the index with: built by that repo's index workflow,
    from that repo's code, byte for byte. Needs the GitHub CLI."""
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/releases/download/", url)
    if not m:
        raise Unavailable(f"can't verify an index that isn't a GitHub release download: {url}")
    if not shutil.which("gh"):
        raise Unavailable("verifying the index needs the GitHub CLI (`gh`); install it or set [index] verify = false")
    repo = m.group(1)
    run = subprocess.run(["gh", "attestation", "verify", str(path), "--repo", repo,
                          "--signer-workflow", f"{repo}/.github/workflows/index.yml"], capture_output=True, text=True)
    if run.returncode:
        raise Unavailable(f"the downloaded index failed verification: {(run.stderr or run.stdout).strip()[:300]}")


def fetch(url: str, cache: Path, check: bool = False) -> dict:
    """Download the index if it changed since the cached copy (ETag); fall back to the cached copy offline.
    With `check`, a download that fails attestation is discarded."""
    tag = cache.with_suffix(".etag")
    req = urllib.request.Request(url, headers={"User-Agent": "skillscout"})
    if cache.exists() and tag.exists():
        req.add_header("If-None-Match", tag.read_text().strip())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            cache.parent.mkdir(parents=True, exist_ok=True)
            part = cache.with_suffix(".part")
            part.write_bytes(r.read())
            load(part)                              # a truncated or foreign file never replaces a good one
            if check:
                try:
                    verify(part, url)
                except Unavailable:
                    part.unlink()
                    raise
            part.replace(cache)
            if etag := r.headers.get("ETag"):
                tag.write_text(etag)
    except urllib.error.HTTPError as e:
        if e.code != 304 and not cache.exists():
            raise Unavailable(f"couldn't download the index ({e.code}); run `skillscout discover --local`")
        if e.code != 304:
            print(f"index download failed ({e.code}); using the copy from {_age(cache)}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        if not cache.exists():
            raise Unavailable(f"couldn't download the index ({e}); run `skillscout discover --local`")
        print(f"index download failed; using the copy from {_age(cache)}")
    return load(cache)


def _age(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
