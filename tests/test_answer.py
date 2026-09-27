import json
from types import SimpleNamespace

import pytest

from repo_ask.answer import (
    NOT_FOUND,
    AnswerFormatError,
    Citation,
    answer_question,
    make_client,
    validate_citations,
)
from repo_ask.chunker import Chunk

CHUNKS = [
    Chunk("engine/memory/runner.mjs", 616, 644, "recallRunnerTaskMemory", "function_declaration", "code"),
    Chunk("engine/memory/records.ts", 275, 313, "fallbackRecallFromRecords", "function_declaration", "code"),
    Chunk("engine/memory/records.ts", 314, 330, "next", "function_declaration", "code"),
]


class FakeClient:
    """Stands in for anthropic.Anthropic: returns queued replies and records each request."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls = []
        self.messages = self

    def create(self, **request):
        self.calls.append(request)
        text = self.replies.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")


def reply(found=True, answer="Recalls up to 5 memories.", citations=(("engine/memory/runner.mjs", 616, 644),)):
    cites = [{"path": p, "start_line": s, "end_line": e} for p, s, e in citations]
    return json.dumps({"answer": answer, "citations": cites, "found": found})


def test_validator_keeps_citations_inside_a_retrieved_chunk():
    valid, rejected = validate_citations([Citation("engine/memory/runner.mjs", 616, 644),
                                          Citation("engine/memory/runner.mjs", 620, 625)], CHUNKS)
    assert len(valid) == 2 and rejected == []


@pytest.mark.parametrize("citation", [
    Citation("engine/memory/runner.mjs", 600, 620),  # starts before the chunk
    Citation("engine/memory/runner.mjs", 640, 650),  # ends after the chunk
    Citation("engine/memory/runner.mjs", 700, 710),  # entirely outside
    Citation("engine/memory/other.mjs", 616, 644),  # file that was not retrieved
    Citation("engine/memory/records.ts", 310, 320),  # spans two chunks
])
def test_validator_rejects_citations_outside_retrieved_chunks(citation):
    valid, rejected = validate_citations([citation], CHUNKS)
    assert valid == [] and rejected == [citation]


def test_found_answer_renders_citations_then_answer():
    client = FakeClient(reply())
    answer = answer_question("How many memories?", CHUNKS, client=client, model="m")
    assert answer.found
    assert answer.render() == "engine/memory/runner.mjs:616-644\n  Recalls up to 5 memories."


def test_found_false_prints_not_found():
    answer = answer_question("Kafka?", CHUNKS, client=FakeClient(reply(found=False, citations=())), model="m")
    assert not answer.found and answer.render() == NOT_FOUND


def test_answer_whose_citations_are_all_rejected_is_not_found():
    bad = reply(citations=(("engine/memory/runner.mjs", 1, 20),))
    answer = answer_question("q", CHUNKS, client=FakeClient(bad), model="m")
    assert answer.render() == NOT_FOUND
    assert answer.rejected == [Citation("engine/memory/runner.mjs", 1, 20)]


def test_invalid_citations_are_dropped_but_valid_ones_kept():
    mixed = reply(citations=(("engine/memory/runner.mjs", 616, 644), ("nope.ts", 1, 2)))
    answer = answer_question("q", CHUNKS, client=FakeClient(mixed), model="m")
    assert answer.citations == [Citation("engine/memory/runner.mjs", 616, 644)]
    assert answer.rejected == [Citation("nope.ts", 1, 2)]


def test_no_retrieved_chunks_means_not_found_without_calling_the_model():
    client = FakeClient()
    assert answer_question("q", [], client=client).render() == NOT_FOUND
    assert client.calls == []


def test_malformed_json_is_retried_once():
    client = FakeClient("not json {", reply())
    assert answer_question("q", CHUNKS, client=client, model="m").found
    assert len(client.calls) == 2
    assert "rejected" in client.calls[1]["messages"][0]["content"]


def test_wrong_shape_counts_as_malformed():
    client = FakeClient(json.dumps({"answer": "x"}), reply())
    assert answer_question("q", CHUNKS, client=client, model="m").found
    assert len(client.calls) == 2


def test_malformed_twice_raises():
    with pytest.raises(AnswerFormatError):
        answer_question("q", CHUNKS, client=FakeClient("oops", "still oops"), model="m")


def test_request_uses_model_env_var_schema_and_labelled_chunks(monkeypatch):
    monkeypatch.setenv("REPO_ASK_MODEL", "claude-test")
    client = FakeClient(reply())
    answer_question("q", CHUNKS, client=client)
    request = client.calls[0]
    assert request["model"] == "claude-test"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert "[1] engine/memory/runner.mjs:616-644 (recallRunnerTaskMemory)" in request["messages"][0]["content"]


def test_default_model(monkeypatch):
    monkeypatch.delenv("REPO_ASK_MODEL", raising=False)
    client = FakeClient(reply())
    answer_question("q", CHUNKS, client=client)
    assert client.calls[0]["model"] == "claude-sonnet-5"


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        make_client()
