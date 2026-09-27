"""Command line entry point: repo-ask index | ask | eval."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .answer import answer_question
from .evaluate import format_report, run_eval, save
from .index import build_index, load_index
from .retriever import BM25_B, Retriever


def load_retriever(repo: Path, **options) -> Retriever:
    if not repo.is_dir():
        raise SystemExit(f"not a directory: {repo}")
    chunks = load_index(repo)
    if chunks is None:
        print(f"No index for {repo}; building it now.", file=sys.stderr)
        chunks, _ = build_index(repo)
    return Retriever(repo.resolve(), chunks, **options)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="repo-ask", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    index = commands.add_parser("index", help="build the index for a repository")
    index.add_argument("repo", type=Path)

    ask = commands.add_parser("ask", help="answer a question with cited file:line ranges")
    ask.add_argument("question")
    ask.add_argument("--repo", type=Path, default=Path("."))
    ask.add_argument("--show-chunks", action="store_true", help="print retrieved chunks to stderr")

    evaluate = commands.add_parser("eval", help="run an evaluation file")
    evaluate.add_argument("eval_file", type=Path)
    evaluate.add_argument("--repo", type=Path, required=True)
    evaluate.add_argument("--retrieval-only", action="store_true", help="skip the LLM; score retrieval only")
    evaluate.add_argument("--no-bm25", action="store_true", help="ablation: disable BM25")
    evaluate.add_argument("--no-rg", action="store_true", help="ablation: disable ripgrep")
    evaluate.add_argument("--bm25-b", type=float, default=BM25_B,
                          help=f"BM25 length normalisation, 0-1 (default {BM25_B})")
    evaluate.add_argument("--out", type=Path, help="write per-question results as JSON")

    args = parser.parse_args(argv)

    if args.command == "index":
        chunks, path = build_index(args.repo)
        print(f"Indexed {len(chunks)} chunks from {len({c.path for c in chunks})} files -> {path}")

    elif args.command == "ask":
        retriever = load_retriever(args.repo)
        chunks = retriever.search(args.question)
        if args.show_chunks:
            for c in chunks:
                print(f"  retrieved {c.path}:{c.start_line}-{c.end_line} {c.symbol}", file=sys.stderr)
        answer = answer_question(args.question, chunks)
        print(answer.render())
        if answer.rejected:
            rejected = ", ".join(map(str, answer.rejected))
            print(f"(rejected citations outside the retrieved code: {rejected})", file=sys.stderr)

    elif args.command == "eval":
        retriever = load_retriever(args.repo, use_bm25=not args.no_bm25, use_rg=not args.no_rg,
                                   bm25_b=args.bm25_b)
        result = run_eval(args.eval_file, retriever, retrieval_only=args.retrieval_only)
        result["date"] = date.today().isoformat()
        print(format_report(result))
        if args.out:
            save(result, args.out)


if __name__ == "__main__":
    main()
