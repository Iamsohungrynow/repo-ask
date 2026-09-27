"""Run an eval file: retrieval hit@6, answer accuracy, abstention accuracy and latency."""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from .answer import answer_question, make_client
from .retriever import Retriever


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def matches(path: str, start: int, end: int, expected: list[dict]) -> bool:
    return any(path == e["path"] and overlaps(start, end, *e["lines"]) for e in expected)


def run_eval(eval_file: Path, retriever: Retriever, client=None, retrieval_only=False) -> dict:
    questions = yaml.safe_load(eval_file.read_text())["questions"]
    if not retrieval_only:
        client = client or make_client()  # fail fast if the key is missing
    rows = []
    for q in questions:
        answerable = q.get("answerable", True)
        expected = q.get("expected", [])
        start = time.perf_counter()
        chunks = retriever.search(q["question"])
        row = {
            "id": q["id"], "question": q["question"], "answerable": answerable,
            "retrieved": [f"{c.path}:{c.start_line}-{c.end_line}" for c in chunks],
            "hit_file": any(c.path in {e["path"] for e in expected} for c in chunks),
            "hit_lines": any(matches(c.path, c.start_line, c.end_line, expected) for c in chunks),
        }
        if not retrieval_only:
            try:
                answer = answer_question(q["question"], chunks, client=client)
                row["found"] = answer.found
                row["citations"] = [str(c) for c in answer.citations]
                row["rejected"] = [str(c) for c in answer.rejected]
                row["correct"] = (
                    answer.found and any(matches(c.path, c.start_line, c.end_line, expected) for c in answer.citations)
                    if answerable else not answer.found
                )
            except Exception as error:  # an API or format failure is a wrong answer, and is reported
                row["error"] = f"{type(error).__name__}: {error}"
                row["correct"] = False
        row["latency_s"] = round(time.perf_counter() - start, 2)
        rows.append(row)
    return {"eval_file": str(eval_file), "retrieval_only": retrieval_only, "rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    summary = {
        "retrieval_hit_file": [sum(r["hit_file"] for r in answerable), len(answerable)],
        "retrieval_hit_lines": [sum(r["hit_lines"] for r in answerable), len(answerable)],
        "avg_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 2),
    }
    if "correct" in rows[0]:
        summary["answer_accuracy"] = [sum(r["correct"] for r in answerable), len(answerable)]
        summary["abstention_accuracy"] = [sum(r["correct"] for r in unanswerable), len(unanswerable)]
        summary["errors"] = sum("error" in r for r in rows)
        summary["rejected_citations"] = sum(len(r.get("rejected", [])) for r in rows)
    return summary


def format_report(result: dict) -> str:
    def mark(value):
        return {True: "yes", False: "no", None: "-"}[value]

    lines = [f"{'id':<5}{'hit@6':<7}{'lines':<7}{'answer':<8}{'latency':>8}  question"]
    for r in result["rows"]:
        hit = (r["hit_file"], r["hit_lines"]) if r["answerable"] else (None, None)
        answer = "ERROR" if "error" in r else mark(r.get("correct"))
        lines.append(f"{r['id']:<5}{mark(hit[0]):<7}{mark(hit[1]):<7}{answer:<8}"
                     f"{r['latency_s']:>7.2f}s  {r['question']}")

    s = result["summary"]

    def ratio(pair):
        return f"{pair[0]}/{pair[1]} = {pair[0] / pair[1]:.0%}" if pair[1] else "n/a"

    lines += ["", f"retrieval hit@6 (file):   {ratio(s['retrieval_hit_file'])}",
              f"retrieval hit@6 (lines):  {ratio(s['retrieval_hit_lines'])}"]
    if "answer_accuracy" in s:
        lines += [f"answer accuracy:          {ratio(s['answer_accuracy'])}",
                  f"abstention accuracy:      {ratio(s['abstention_accuracy'])}",
                  f"rejected citations:       {s['rejected_citations']}",
                  f"errors:                   {s['errors']}"]
    else:
        lines.append("answer / abstention:      not run (--retrieval-only)")
    lines.append(f"average latency:          {s['avg_latency_s']:.2f} s")
    return "\n".join(lines)


def save(result: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
