from pathlib import Path

import pytest

from repo_ask import chunker
from repo_ask.chunker import chunk_file, chunk_repo


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def spans(chunks):
    return [(c.start_line, c.end_line, c.symbol) for c in chunks]


PYTHON = """\
import os

def alpha(x):
    return x + 1

# Doc comment for Beta
class Beta:
    def gamma(self):
        return 2

CONSTANT = 3
"""


def test_python_functions_and_classes_get_exact_line_ranges(tmp_path):
    chunks = chunk_file(write(tmp_path, "m.py", PYTHON), "m.py")
    assert spans(chunks) == [(1, 1, ""), (3, 4, "alpha"), (6, 9, "Beta"), (11, 11, "")]
    assert chunks[2].text.startswith("# Doc comment for Beta\nclass Beta:")
    assert chunks[2].kind == "class_definition"


TYPESCRIPT = """\
import { x } from "./x";

/**
 * Recall from records.
 */
export async function fallbackRecall(query: string) {
  return query;
}

export const handler = async () => {
  return 1;
};

const single = 1;

class Store {
  get() {
    return 2;
  }
}
"""


def test_typescript_exported_function_includes_its_doc_comment(tmp_path):
    chunks = chunk_file(write(tmp_path, "a.ts", TYPESCRIPT), "a.ts")
    assert (3, 8, "fallbackRecall") in spans(chunks)
    assert (10, 12, "handler") in spans(chunks)  # multi-line const arrow function
    assert (16, 20, "Store") in spans(chunks)
    assert (14, 14, "") in spans(chunks)  # one-line const stays in a window


def test_every_non_blank_line_is_covered_exactly_once(tmp_path):
    chunks = chunk_file(write(tmp_path, "a.ts", TYPESCRIPT), "a.ts")
    lines = TYPESCRIPT.split("\n")
    covered = [n for c in chunks for n in range(c.start_line, c.end_line + 1)]
    assert len(covered) == len(set(covered))
    assert {n for n, line in enumerate(lines, 1) if line.strip()} <= set(covered)


def test_chunk_text_matches_the_file_lines(tmp_path):
    for chunk in chunk_file(write(tmp_path, "a.ts", TYPESCRIPT), "a.ts"):
        assert chunk.text == "\n".join(TYPESCRIPT.split("\n")[chunk.start_line - 1 : chunk.end_line])


JAVA = """\
package demo;

public class Greeter {
    private final String name = "x";

    public String hello() {
        return "hi " + name;
    }

    public String bye() {
        return "bye";
    }
}
"""


def test_java_class_is_one_chunk_when_short(tmp_path):
    chunks = chunk_file(write(tmp_path, "Greeter.java", JAVA), "Greeter.java")
    assert (3, 13, "Greeter") in spans(chunks)


def test_long_java_class_is_split_into_methods(tmp_path, monkeypatch):
    monkeypatch.setattr(chunker, "MAX_DEF_LINES", 5)
    chunks = chunk_file(write(tmp_path, "Greeter.java", JAVA), "Greeter.java")
    assert (6, 8, "Greeter.hello") in spans(chunks)
    assert (10, 12, "Greeter.bye") in spans(chunks)
    assert (1, 4, "") in spans(chunks)  # package, class header and field form one window


def test_rust_functions_and_impl_blocks(tmp_path):
    source = "struct S;\n\nimpl S {\n    fn new() -> S { S }\n}\n\nfn main() {\n    let _ = S::new();\n}\n"
    chunks = chunk_file(write(tmp_path, "main.rs", source), "main.rs")
    assert spans(chunks) == [(1, 1, "S"), (3, 5, "S"), (7, 9, "main")]


def test_unknown_language_falls_back_to_80_line_windows(tmp_path):
    source = "\n".join(f"select {n};" for n in range(1, 201))
    chunks = chunk_file(write(tmp_path, "q.sql", source), "q.sql")
    assert spans(chunks) == [(1, 80, ""), (81, 160, ""), (161, 200, "")]
    assert {c.kind for c in chunks} == {"window"}


def test_repo_walk_skips_vendored_generated_lockfiles_and_binaries(tmp_path):
    write(tmp_path, "src/app.py", "def main():\n    pass\n")
    write(tmp_path, "node_modules/lib/index.js", "function x() {}\n")
    write(tmp_path, "dist/bundle.js", "function y() {}\n")
    write(tmp_path, "build/out.py", "def z():\n    pass\n")
    write(tmp_path, "package-lock.json", "{}\n")
    write(tmp_path, "Cargo.lock", "x\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x00binary")
    assert {c.path for c in chunk_repo(tmp_path)} == {"src/app.py"}


@pytest.mark.parametrize("name", ["a.js", "a.mjs", "a.tsx", "a.py", "a.rs", "A.java"])
def test_all_supported_languages_parse(tmp_path, name):
    body = {
        ".py": "def f():\n    pass\n",
        ".rs": "fn f() {\n}\n",
        ".java": "class A {\n  void f() {}\n}\n",
    }.get(Path(name).suffix, "function f() {\n  return 1;\n}\n")
    chunks = chunk_file(write(tmp_path, name, body), name)
    assert chunks and chunks[0].kind != "window"
