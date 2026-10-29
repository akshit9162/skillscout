"""GitHub search (REST API) and shallow clones. A token is optional: GITHUB_TOKEN, or the GitHub CLI's login if `gh` is installed."""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
MAX_WAIT = 120  # seconds to wait out one rate-limit window
TRIES = 4


_token = None


def token() -> str:
    """GITHUB_TOKEN, else the GitHub CLI's login, else empty (anonymous: 60 API requests an hour)."""
    global _token
    if _token is None:
        _token = os.environ.get("GITHUB_TOKEN", "")
        if not _token and shutil.which("gh"):
            try:
                _token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
            except (subprocess.SubprocessError, OSError):
                _token = ""
    return _token


def _get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "skillscout"})
    if t := token():
        req.add_header("Authorization", f"Bearer {t}")
    for attempt in range(TRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            limited = e.code == 429 or (e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0")
            if not limited or attempt == TRIES - 1:
                raise
            reset = e.headers.get("Retry-After") or max(0, int(e.headers.get("X-RateLimit-Reset", 0)) - int(time.time()))
            wait = min(int(reset) + 1, MAX_WAIT)
            print(f"GitHub rate limit hit; waiting {wait}s" + ("" if token() else " (set GITHUB_TOKEN or log in with `gh auth login` to avoid this)"))
            time.sleep(wait)


def _repo(d: dict) -> dict:
    return {"full_name": d["full_name"], "stars": d.get("stargazers_count", 0), "pushed_at": d.get("pushed_at"),
            "license": (d.get("license") or {}).get("spdx_id"), "archived": d.get("archived", False),
            "description": d.get("description") or "", "size_kb": d.get("size", 0)}


def search(query: str, per_page: int) -> list[dict]:
    q = urllib.parse.quote(query)
    return [_repo(d) for d in _get(f"/search/repositories?q={q}&sort=stars&order=desc&per_page={per_page}")["items"]]


def repo(full_name: str) -> dict:
    return _repo(_get(f"/repos/{full_name}"))


def readme(full_name: str) -> str:
    """The README at the default branch, from raw.githubusercontent.com (no API quota used)."""
    for name in ("README.md", "readme.md", "README.markdown", "README"):
        req = urllib.request.Request(f"https://raw.githubusercontent.com/{full_name}/HEAD/{name}",
                                     headers={"User-Agent": "skillscout"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    raise FileNotFoundError(f"no README in {full_name}")


# github.com paths that look like owner/repo but aren't repos
NOT_OWNERS = {"about", "apps", "collections", "contact", "customer-stories", "enterprise", "features", "login",
              "marketplace", "orgs", "pricing", "readme", "security", "settings", "site", "sponsors", "topics",
              "trending", "users", "join", "explore", "notifications", "new", "codespaces", "events"}
_LINK = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})")


def linked_repos(text: str) -> list[str]:
    """owner/repo for every GitHub repo linked from markdown, first mention first, no duplicates."""
    out, seen = [], set()
    for owner, name in _LINK.findall(text):
        name = name.removesuffix(".git").rstrip(".")
        if owner.lower() in NOT_OWNERS or not name or name.startswith("."):
            continue
        full = f"{owner}/{name}"
        if full.lower() not in seen:
            seen.add(full.lower())
            out.append(full)
    return out


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


def trees(root: Path) -> dict[str, str]:
    """Folder path -> git tree hash at HEAD, one git call per repo. A folder's hash changes when any file in it does."""
    out = {".": git("rev-parse", "HEAD^{tree}", cwd=root)}
    for line in git("ls-tree", "-r", "-d", "HEAD", cwd=root).splitlines():
        meta, path = line.split("\t", 1)
        out[path] = meta.split()[2]
    return out


class Stale(Exception):
    """The refresh failed; the cached checkout is usable but may be behind upstream."""

    def __init__(self, root: Path, commit: str):
        super().__init__(f"refresh failed, using the cached copy at {commit[:10]}")
        self.root, self.commit = root, commit


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
                    raise Stale(dest, git("rev-parse", "HEAD", cwd=dest))
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
