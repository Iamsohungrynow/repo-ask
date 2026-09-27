"""Ask Claude about the retrieved chunks and keep only citations that point inside them."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .chunker import Chunk

DEFAULT_MODEL = "claude-sonnet-5"
NOT_FOUND = "Not found in this repo"

SYSTEM = """You answer questions about a code repository using only the numbered source excerpts \
provided. Each excerpt is labelled path:start-end and every line starts with its line number.

Rules:
- Use only the excerpts, not outside knowledge of the project.
- Cite each location that supports your answer as {path, start_line, end_line}. A citation must lie \
inside a single excerpt's range; cite the narrowest range that supports the claim.
- If the excerpts do not contain the answer, set found to false, leave citations empty and say \
briefly what is missing. Never guess.
- Answer in one to three sentences."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path", "start_line", "end_line"],
                "additionalProperties": False,
            },
        },
        "found": {"type": "boolean"},
    },
    "required": ["answer", "citations", "found"],
    "additionalProperties": False,
}


class AnswerFormatError(Exception):
    """The model's reply was not valid JSON of the required shape, even after one retry."""


@dataclass(frozen=True)
class Citation:
    path: str
    start_line: int
    end_line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass
class Answer:
    found: bool
    text: str
    citations: list[Citation] = field(default_factory=list)
    rejected: list[Citation] = field(default_factory=list)  # cited, but outside every chunk
    chunks: list[Chunk] = field(default_factory=list)  # what retrieval returned

    def render(self) -> str:
        if not self.found:
            return NOT_FOUND
        return "\n".join([*map(str, self.citations), f"  {self.text}"])


def make_client():
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(api_key=key)


def answer_question(question: str, chunks: list[Chunk], client=None, model: str | None = None) -> Answer:
    if not chunks:
        return Answer(False, NOT_FOUND)  # nothing relevant retrieved: don't ask the model to guess
    client = client or make_client()
    model = model or os.environ.get("REPO_ASK_MODEL", DEFAULT_MODEL)
    reply = call_model(client, model, question, chunks)
    cited = [Citation(c["path"], c["start_line"], c["end_line"]) for c in reply["citations"]]
    valid, rejected = validate_citations(cited, chunks)
    if not reply["found"] or not valid:
        return Answer(False, NOT_FOUND, [], rejected, chunks)
    return Answer(True, reply["answer"].strip(), valid, rejected, chunks)


def call_model(client, model: str, question: str, chunks: list[Chunk]) -> dict:
    """Request a JSON answer; parse and check its shape, retrying once if it is malformed."""
    prompt = f"{format_context(chunks)}\n\nQuestion: {question}"
    for attempt in range(2):
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return parse_reply(text)
        except ValueError as error:
            if attempt == 1:
                raise AnswerFormatError(f"invalid reply after retry: {error}") from error
            prompt += f"\n\nYour previous reply was rejected ({error}). Reply with only the JSON object."
    raise AssertionError("unreachable")


def parse_reply(text: str) -> dict:
    """json.loads plus a shape check; raises ValueError (JSONDecodeError is one) on any problem."""
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    reply = json.loads(text)
    if not isinstance(reply, dict):
        raise ValueError("reply is not a JSON object")
    if not isinstance(reply.get("answer"), str) or not isinstance(reply.get("found"), bool):
        raise ValueError("missing 'answer' string or 'found' boolean")
    citations = reply.get("citations")
    if not isinstance(citations, list):
        raise ValueError("'citations' is not a list")
    for c in citations:
        if not (isinstance(c, dict) and isinstance(c.get("path"), str)
                and type(c.get("start_line")) is int and type(c.get("end_line")) is int):
            raise ValueError(f"malformed citation: {c!r}")
    return reply


def validate_citations(cited: list[Citation], chunks: list[Chunk]) -> tuple[list[Citation], list[Citation]]:
    """Split citations into (valid, rejected); valid ones lie entirely inside one retrieved chunk."""
    valid, rejected = [], []
    for c in dict.fromkeys(cited):  # dedupe, keep order
        inside = any(
            c.path == ch.path and ch.start_line <= c.start_line <= c.end_line <= ch.end_line
            for ch in chunks
        )
        (valid if inside else rejected).append(c)
    return valid, rejected


def format_context(chunks: list[Chunk]) -> str:
    parts = []
    for n, c in enumerate(chunks, 1):
        label = f"[{n}] {c.path}:{c.start_line}-{c.end_line}" + (f" ({c.symbol})" if c.symbol else "")
        body = "\n".join(f"{c.start_line + i:>5}  {line}" for i, line in enumerate(c.text.split("\n")))
        parts.append(f"{label}\n{body}")
    return "\n\n".join(parts)
