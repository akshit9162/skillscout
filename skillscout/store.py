"""Small files on disk: JSON state and TOML strings, written whole or not at all."""

import json
import shutil
import tomllib
from pathlib import Path


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, data, sort_keys: bool = True) -> None:
    """Atomic: a crash mid-write leaves the previous file, never half of one. A symlinked file (dotfiles
    managed with stow or chezmoi) is written through to its target and keeps its permissions."""
    path = path.resolve() if path.is_symlink() else path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".skillscout.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=sort_keys, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.exists():
        shutil.copymode(path, tmp)
    tmp.replace(path)


def toml_str(s: str) -> str:
    """A TOML basic string. json.dumps would escape an emoji as a surrogate pair, which TOML rejects."""
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007F")


def write_toml(path: Path, text: str) -> None:
    """Write a profile only if it still parses, so no command can leave one that breaks every later run."""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"not writing {path}: the result wouldn't be valid TOML ({e})")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_toml(path: Path) -> dict:
    """A profile, or one line naming the file and what's wrong with it (users edit it by hand)."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"no profile at {path}; run `skillscout init --about \"what you work on\"`")
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"{path} isn't valid TOML: {e}. Fix that line, or move the file away and run `skillscout init`")
