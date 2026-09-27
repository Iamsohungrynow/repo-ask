import json
from types import SimpleNamespace

from repo_ask.chunker import Chunk
from repo_ask.evaluate import format_report, run_eval

EVAL = """\
questions:
  - id: q1
    question: where is recall
    expected:
      - {path: a.ts, lines: [10, 20]}
  - id: q2
    question: where is price
    expected:
      - {path: b.ts, lines: [1, 5]}
  - id: u1
    question: where is kafka
    answerable: false
"""


class StubRetriever:
    def search(self, question):
        return [Chunk("a.ts", 1, 30, "f", "function_declaration", "x")]


class ScriptedClient:
    def __init__(self, replies):
        self.replies = replies
        self.messages = self

    def create(self, **request):
        text = json.dumps(self.replies.pop(0))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def test_metrics(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(EVAL)
    client = ScriptedClient([
        {"answer": "a", "found": True, "citations": [{"path": "a.ts", "start_line": 12, "end_line": 14}]},
        {"answer": "b", "found": True, "citations": [{"path": "a.ts", "start_line": 1, "end_line": 2}]},
        {"answer": "no", "found": False, "citations": []},
    ])
    result = run_eval(path, StubRetriever(), client=client)
    s = result["summary"]
    assert s["retrieval_hit_file"] == [1, 2]
    assert s["answer_accuracy"] == [1, 2]  # q2 cites the wrong file
    assert s["abstention_accuracy"] == [1, 1]
    assert s["errors"] == 0
    assert "answer accuracy:          1/2 = 50%" in format_report(result)


def test_api_failure_is_counted_and_reported(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(EVAL)

    class Broken:
        messages = SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))

    result = run_eval(path, StubRetriever(), client=Broken())
    assert result["summary"]["errors"] == 3
    assert result["summary"]["answer_accuracy"] == [0, 2]
    assert "ERROR" in format_report(result)


def test_retrieval_only_needs_no_client(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(EVAL)
    result = run_eval(path, StubRetriever(), retrieval_only=True)
    assert "answer_accuracy" not in result["summary"]
    assert "not run" in format_report(result)
