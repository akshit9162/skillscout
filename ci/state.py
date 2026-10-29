"""Pack or unpack the crawl state the weekly index job carries between runs (scan results, repo
metadata, list rotation), so a run without a warm cache continues instead of starting over.

    python ci/state.py pack state.tar.gz
    python ci/state.py unpack state.tar.gz
"""

import io
import json
import sys
import tarfile
from pathlib import Path

STATE = Path.home() / ".skillscout"
FILES = ("repos.json", "lists.json", "crawl.json")


def pack(out: Path) -> None:
    with tarfile.open(out, "w:gz") as tar:
        for name in FILES:
            p = STATE / name
            if not p.exists():
                continue
            data = json.loads(p.read_text())
            if name == "repos.json":            # checkouts are machine-local: keep the metadata, drop the paths
                data = {k: {**v, "root": "/nonexistent"} for k, v in data.items()}
            blob = json.dumps(data, sort_keys=True).encode()
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))


def unpack(src: Path) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with tarfile.open(src, "r:gz") as tar:
        for m in tar.getmembers():
            if m.name in FILES and m.isfile() and not (STATE / m.name).exists():
                (STATE / m.name).write_bytes(tar.extractfile(m).read())


if __name__ == "__main__":
    {"pack": pack, "unpack": unpack}[sys.argv[1]](Path(sys.argv[2]))
