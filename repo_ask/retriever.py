"""Hybrid retrieval: BM25 over chunk text and symbol names, plus ripgrep for exact identifiers."""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Callable

from rank_bm25 import BM25Okapi

from .chunker import Chunk

TOP_K = 6
RRF_K = 60  # standard Reciprocal Rank Fusion constant
MAX_DOCS = 2  # at most this many prose chunks in the top k
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt")

# A ranker maps a question to chunk ids, best first. Embeddings would be one more ranker.
Ranker = Callable[[str], list[int]]

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "call", "called", "code", "do",
    "does", "for", "from", "how", "in", "is", "it", "its", "many", "of", "on", "or", "the",
    "this", "to", "what", "when", "where", "which", "who", "why", "with",
}
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase words; identifiers also yield their camelCase / snake_case parts."""
    tokens = []
    for word in WORD.findall(text):
        parts = PART.findall(word)
        for token in {word.lower(), *(p.lower() for p in parts)}:
            if len(token) > 1 and token not in STOPWORDS:
                tokens.append(_stem(token))
    return tokens


def identifiers(question: str, symbols: set[str]) -> list[str]:
    """Words in the question that look like code: `quoted`, camelCase, snake_case or a known symbol."""
    found = set(re.findall(r"`([^`\s]+)`", question))
    for word in WORD.findall(question):
        camel = re.search(r"[a-z][A-Z]", word)
        if camel or "_" in word.strip("_") or (word in symbols and len(word) > 3):
            found.add(word)
    return sorted(found)


class Retriever:
    def __init__(self, repo: Path, chunks: list[Chunk], use_bm25=True, use_rg=True,
                 extra_rankers: tuple[Ranker, ...] = ()):
        self.repo, self.chunks = repo, chunks
        self.symbols = {c.symbol.split(".")[-1] for c in chunks if c.symbol}
        self.by_path = defaultdict(list)
        for i, c in enumerate(chunks):
            self.by_path[c.path].append(i)
        self.rankers = [r for r, on in ((self.bm25, use_bm25), (self.ripgrep, use_rg)) if on]
        self.rankers += list(extra_rankers)
        if use_bm25:
            # Path and symbol are repeated so a name match outweighs a passing mention in the body.
            docs = [tokenize(f"{c.path} {c.symbol} {c.symbol} {c.text}") for c in chunks]
            self._bm25 = BM25Okapi(docs)

    def search(self, question: str, k: int = TOP_K) -> list[Chunk]:
        """Fuse all rankings with Reciprocal Rank Fusion; return the top k distinct chunks."""
        scores: dict[int, float] = defaultdict(float)
        for ranker in self.rankers:
            for rank, i in enumerate(ranker(question)):
                scores[i] += 1 / (RRF_K + rank + 1)
        picked, docs = [], 0
        for i in sorted(scores, key=lambda i: (-scores[i], i)):
            is_doc = self.chunks[i].path.endswith(DOC_SUFFIXES)
            if is_doc and docs >= MAX_DOCS:
                continue  # prose mentions everything; don't let it crowd out code
            docs += is_doc
            picked.append(self.chunks[i])
            if len(picked) == k:
                break
        return picked

    def bm25(self, question: str) -> list[int]:
        tokens = tokenize(question)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
        return [i for i in ranked[: TOP_K * 5] if scores[i] > 0]

    def ripgrep(self, question: str) -> list[int]:
        names = identifiers(question, self.symbols)
        if not names:
            return []
        patterns = [arg for name in names for arg in ("-e", name)]
        cmd = ["rg", "--no-heading", "--line-number", "--fixed-strings", "--word-regexp",
               "--color", "never", "--max-columns", "400", *patterns, "."]
        try:
            out = subprocess.run(cmd, cwd=self.repo, capture_output=True, text=True, timeout=20).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []  # ripgrep is a booster; BM25 still works without it

        matched: dict[int, set[str]] = defaultdict(set)
        for line in out.splitlines():
            path, number, text = (line.split(":", 2) + ["", ""])[:3]
            i = self._chunk_at(path.removeprefix("./"), int(number) if number.isdigit() else 0)
            if i is not None:  # None: file is not indexed (vendored, lockfile, binary)
                matched[i].update(name for name in names if name in text)

        def rank(i: int):
            # The chunk that defines an identifier beats chunks that only use it.
            defines = self.chunks[i].symbol.split(".")[-1] in matched[i]
            return (not defines, -len(matched[i]), i)

        return sorted(matched, key=rank)[: TOP_K * 5]

    def _chunk_at(self, path: str, line: int) -> int | None:
        for i in self.by_path.get(path, ()):
            if self.chunks[i].start_line <= line <= self.chunks[i].end_line:
                return i
        return None
