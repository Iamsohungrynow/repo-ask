"""Split a repository into chunks: one per function/class, with line windows as fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import tree_sitter as ts
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_typescript

WINDOW = 80  # lines per fallback window
MAX_DEF_LINES = 150  # longer definitions are split into their nested definitions, or windows
MAX_FILE_BYTES = 1_000_000  # bigger files are almost always generated or minified

SKIP_DIRS = {"node_modules", "dist", "build", "target", ".git", ".next", "__pycache__", ".venv", "venv"}
SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "Cargo.lock",
    "poetry.lock", "uv.lock", "Gemfile.lock", "composer.lock", "go.sum",
}
SKIP_SUFFIXES = (".lock", ".min.js", ".map", ".svg")

_JS = ts.Language(tree_sitter_javascript.language())
LANGUAGES = {
    ".py": ts.Language(tree_sitter_python.language()),
    ".js": _JS, ".jsx": _JS, ".mjs": _JS, ".cjs": _JS,
    ".ts": ts.Language(tree_sitter_typescript.language_typescript()),
    ".tsx": ts.Language(tree_sitter_typescript.language_tsx()),
    ".rs": ts.Language(tree_sitter_rust.language()),
    ".java": ts.Language(tree_sitter_java.language()),
}

# Node types that become their own chunk (union over all supported grammars).
DEFINITIONS = {
    # Python
    "function_definition", "class_definition", "decorated_definition",
    # JavaScript / TypeScript
    "function_declaration", "generator_function_declaration", "class_declaration",
    "abstract_class_declaration", "method_definition", "interface_declaration",
    "type_alias_declaration", "enum_declaration",
    # Rust
    "function_item", "impl_item", "struct_item", "enum_item", "trait_item", "mod_item",
    # Java (class/interface/enum declarations are shared with TypeScript above)
    "record_declaration", "method_declaration", "constructor_declaration",
}
# `const f = () => {...}` and multi-line config objects: definitions only when multi-line.
DECLARATIONS = {"lexical_declaration", "variable_declaration"}
COMMENTS = {"comment", "line_comment", "block_comment"}


@dataclass
class Chunk:
    path: str  # relative to the repo root, forward slashes
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    symbol: str  # "" for plain windows
    kind: str  # tree-sitter node type, or "window"
    text: str


def iter_files(repo: Path):
    """Yield indexable files under repo, sorted, skipping vendored, generated and binary files."""
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name in SKIP_FILES or name.endswith(SKIP_SUFFIXES):
                continue
            path = Path(root) / name
            if path.is_file() and not path.is_symlink():
                yield path


def chunk_repo(repo: Path) -> list[Chunk]:
    chunks = []
    for path in iter_files(repo):
        chunks.extend(chunk_file(path, path.relative_to(repo).as_posix()))
    return chunks


def chunk_file(path: Path, rel: str) -> list[Chunk]:
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES or b"\0" in data[:8192]:
        return []  # generated or binary
    # split("\n") rather than splitlines(): tree-sitter rows are counted on "\n" only.
    lines = [line.rstrip("\r") for line in data.decode("utf-8", errors="replace").split("\n")]

    chunks, covered = [], set()
    language = LANGUAGES.get(path.suffix)
    if language is not None:
        tree = ts.Parser(language).parse(data)
        for node, symbol in _definitions(tree.root_node):
            start, end = _start_row(node) + 1, node.end_point[0] + 1
            if node.end_point[1] == 0 and end > start:
                end -= 1  # node ends at column 0 of the next line
            chunks.extend(_windows(rel, lines, start, end, symbol, node.type, MAX_DEF_LINES))
            covered.update(range(start, end + 1))

    # Everything outside a definition (imports, top-level code, non-code files) goes into windows.
    start = None
    for number in range(1, len(lines) + 2):
        if number <= len(lines) and number not in covered:
            start = start or number
        elif start:
            chunks.extend(_windows(rel, lines, start, number - 1, "", "window", WINDOW))
            start = None
    return sorted(chunks, key=lambda c: c.start_line)


def _definitions(node: ts.Node, prefix: str = ""):
    """Yield (node, symbol) for the outermost definitions under node, descending into long ones."""
    for child in node.named_children:
        if not _is_definition(child):
            yield from _definitions(child, prefix)
            continue
        symbol = ".".join(part for part in (prefix, _name(child)) if part)
        too_long = child.end_point[0] - child.start_point[0] + 1 > MAX_DEF_LINES
        nested = list(_definitions(child, symbol)) if too_long else []
        if nested:
            yield from nested  # lines of the parent outside its children become windows
        else:
            yield child, symbol


def _start_row(node: ts.Node) -> int:
    """First row of a definition, extended upward over the comments directly above it (doc comments)."""
    if node.parent is not None and node.parent.type == "export_statement":
        node = node.parent
    row, prev = node.start_point[0], node.prev_named_sibling
    while prev is not None and prev.type in COMMENTS and prev.end_point[0] >= row - 1:
        before = prev.prev_named_sibling
        if before is not None and before.end_point[0] >= prev.start_point[0]:
            break  # trailing comment on another statement's line
        row, prev = prev.start_point[0], before
    return row


def _is_definition(node: ts.Node) -> bool:
    if node.type in DECLARATIONS:
        return node.end_point[0] > node.start_point[0]
    return node.type in DEFINITIONS


def _name(node: ts.Node) -> str:
    if node.type == "decorated_definition":
        node = node.child_by_field_name("definition") or node
    if node.type in DECLARATIONS:
        declarator = next((c for c in node.named_children if c.type == "variable_declarator"), None)
        node = declarator or node
    name = node.child_by_field_name("name") or node.child_by_field_name("type")  # Rust impl blocks
    return name.text.decode("utf-8", errors="replace") if name else ""


def _windows(rel, lines, start, end, symbol, kind, size):
    """Cut lines[start..end] (1-based, inclusive) into chunks of at most `size` lines, trimming blanks."""
    chunks = []
    for lo in range(start, end + 1, size):
        hi = min(lo + size - 1, end)
        while lo <= hi and not lines[lo - 1].strip():
            lo += 1
        while hi >= lo and not lines[hi - 1].strip():
            hi -= 1
        if lo <= hi:
            chunks.append(Chunk(rel, lo, hi, symbol, kind, "\n".join(lines[lo - 1 : hi])))
    return chunks
