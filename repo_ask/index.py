"""Build, save and load the chunk index. Stored outside the target repo so it is never modified."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from .chunker import Chunk, chunk_repo


def index_path(repo: Path) -> Path:
    repo = repo.resolve()
    home = Path(os.environ.get("REPO_ASK_HOME", Path.home() / ".cache" / "repo-ask"))
    digest = hashlib.sha1(str(repo).encode()).hexdigest()[:10]
    return home / f"{repo.name}-{digest}.json"


def build_index(repo: Path) -> tuple[list[Chunk], Path]:
    chunks = chunk_repo(repo.resolve())
    path = index_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"repo": str(repo.resolve()), "chunks": [asdict(c) for c in chunks]}
    path.write_text(json.dumps(payload))
    return chunks, path


def load_index(repo: Path) -> list[Chunk] | None:
    path = index_path(repo)
    if not path.exists():
        return None
    return [Chunk(**c) for c in json.loads(path.read_text())["chunks"]]
