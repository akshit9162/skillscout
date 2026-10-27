"""GitHub search (REST API) and shallow clones. Set GITHUB_TOKEN for higher rate limits; it is optional."""

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"


def _get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "skillscout"})
    if token := os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _repo(d: dict) -> dict:
    return {"full_name": d["full_name"], "stars": d.get("stargazers_count", 0), "pushed_at": d.get("pushed_at"),
            "license": (d.get("license") or {}).get("spdx_id"), "archived": d.get("archived", False),
            "description": d.get("description") or "", "size_kb": d.get("size", 0)}


def search(query: str, per_page: int) -> list[dict]:
    q = urllib.parse.quote(query)
    return [_repo(d) for d in _get(f"/search/repositories?q={q}&sort=stars&order=desc&per_page={per_page}")["items"]]


def repo(full_name: str) -> dict:
    return _repo(_get(f"/repos/{full_name}"))


def git(*args: str, cwd: Path | None = None, stdin: str | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, input=stdin, check=True, capture_output=True, text=True,
                          timeout=120, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).stdout.strip()


SKIP_DIRS = {"node_modules", "dist", "build", ".git"}


# Media, fonts and archives aren't needed to judge a skill and are most of the bytes in big repos.
# (Sizes would need every blob fetched one by one in a blobless clone, so filter by extension instead.)
HEAVY = ["png", "jpg", "jpeg", "gif", "webp", "avif", "svg", "ico", "mp4", "mov", "webm", "mkv", "mp3", "wav",
         "pdf", "zip", "tar", "gz", "7z", "psd", "fig", "ttf", "otf", "woff", "woff2", "bin", "onnx", "pt",
         "safetensors", "parquet", "npy", "pkl"]


def _skill_patterns(dest: Path, ref: str) -> list[str]:
    """Sparse-checkout patterns: folders that hold a SKILL.md (minus heavy files and vendored dirs), plus licences."""
    files = git("ls-tree", "-r", "--name-only", ref, cwd=dest).splitlines()
    dirs = {str(Path(f).parent) for f in files
            if Path(f).name == "SKILL.md" and not SKIP_DIRS & set(Path(f).parts)}
    escape = lambda p: "".join("\\" + c if c in "*?[]!#\\" else c for c in p)
    keep = sorted("/*" if d == "." else f"/{escape(d)}/" for d in dirs)
    drop = [f"!*.{ext}" for ext in HEAVY] + [f"!**/{d}/" for d in sorted(SKIP_DIRS)]
    return ["/LICENSE*", "/COPYING*"] + keep + drop


def sync(full_name: str, cache: Path) -> tuple[Path, str]:
    """Sparse, shallow clone (only skill folders are downloaded) or refresh. Returns (path, commit).
    Never runs repo code."""
    dest = cache / full_name.replace("/", "__")
    if (dest / ".git").exists():
        for attempt in range(2):
            try:
                git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", "HEAD", cwd=dest)
                break
            except subprocess.SubprocessError:
                if attempt:     # refresh failed twice: keep the cached copy rather than dropping the repo
                    return dest, git("rev-parse", "HEAD", cwd=dest)
                time.sleep(3)
        ref = "FETCH_HEAD"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "-q", "--depth", "1", "--no-tags", "--filter=blob:none", "--no-checkout",
            f"https://github.com/{full_name}.git", str(dest))
        ref = "HEAD"
    git("sparse-checkout", "set", "--no-cone", "--stdin", cwd=dest, stdin="\n".join(_skill_patterns(dest, ref)) + "\n")
    git("reset", "-q", "--hard", ref, cwd=dest)
    return dest, git("rev-parse", "HEAD", cwd=dest)
