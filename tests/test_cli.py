import json

import pytest

from repo_ask import __version__, cli
from repo_ask.answer import Answer, Citation


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == f"repo-ask {__version__}"


def test_ask_json_passes_model_and_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REPO_ASK_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "wiki.ts").write_text("export function wikiSlug(title: string) {\n  return title;\n}\n")
    seen = {}

    def fake_answer(question, chunks, model=None):
        seen["model"] = model
        return Answer(True, "In wikiSlug.", [Citation("wiki.ts", 1, 3)], [], chunks)

    monkeypatch.setattr(cli, "answer_question", fake_answer)
    cli.main(["ask", "how is a wiki slug made", "--repo", str(repo), "--json", "--model", "m"])
    out = json.loads(capsys.readouterr().out)
    assert seen["model"] == "m"
    assert out["found"] and out["citations"] == [{"path": "wiki.ts", "start_line": 1, "end_line": 3}]
