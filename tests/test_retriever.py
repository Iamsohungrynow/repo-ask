import shutil
from pathlib import Path

import pytest

from repo_ask.chunker import chunk_repo
from repo_ask.retriever import Retriever, identifiers, tokenize

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")

FILES = {
    "engine/records.ts": """\
export async function fallbackRecallFromRecords(query: string) {
  const rows = await db.from("memory_records").select("*");
  return rows.filter((row) => row.title.includes(query));
}
""",
    "engine/route.ts": """\
export async function POST(request: Request) {
  const results = await provider.recall(request);
  return results.length ? results : fallbackRecallFromRecords(request.query);
}
""",
    "lib/billing.ts": """\
export async function fetchSuiUsdPrice() {
  const response = await fetch("https://api.binance.com/api/v3/ticker/price?symbol=SUIUSDT");
  return Number((await response.json()).price);
}
""",
    "lib/wiki.ts": """\
export function wikiSlug(title: string) {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}
""",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for name, text in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    return tmp_path


def test_tokenize_splits_identifiers_and_drops_stopwords():
    tokens = tokenize("Where is fallbackRecallFromRecords and walrus_memory_records?")
    assert "fallbackrecallfromrecord" in tokens  # whole identifier (stemmed)
    # The naive stemmer turns "walrus" into "walru"; harmless, as queries are stemmed the same way.
    assert {"fallback", "recall", "record", "walru", "memory"} <= set(tokens)
    assert "where" not in tokens and "is" not in tokens


def test_identifiers_only_picks_code_looking_words():
    question = "Where is fallbackRecallFromRecords used with walrus_memory_records and `wikiSlug` in the project?"
    assert identifiers(question) == ["fallbackRecallFromRecords", "walrus_memory_records", "wikiSlug"]
    assert identifiers("How is the project prompt verified?") == []


@needs_rg
def test_identifier_query_returns_the_defining_chunk_first(repo):
    retriever = Retriever(repo, chunk_repo(repo))
    top = retriever.search("Where is fallbackRecallFromRecords defined?")
    assert (top[0].path, top[0].start_line, top[0].end_line) == ("engine/records.ts", 1, 4)
    assert "engine/route.ts" in {c.path for c in top}  # the call site is found too


@needs_rg
def test_ripgrep_alone_ranks_definition_above_usage(repo):
    retriever = Retriever(repo, chunk_repo(repo), use_bm25=False)
    assert [c.path for c in retriever.search("fallbackRecallFromRecords")] == ["engine/records.ts", "engine/route.ts"]


def test_bm25_finds_natural_language_question(repo):
    retriever = Retriever(repo, chunk_repo(repo), use_rg=False)
    assert retriever.search("How is the SUI price fetched?")[0].symbol == "fetchSuiUsdPrice"
    assert retriever.search("How is a wiki slug generated from a title?")[0].symbol == "wikiSlug"


def test_nothing_relevant_returns_no_chunks(repo):
    assert Retriever(repo, chunk_repo(repo)).search("zebra quantum telescope") == []


def test_results_are_distinct_and_capped_at_k(repo):
    top = Retriever(repo, chunk_repo(repo)).search("fallbackRecallFromRecords price slug", k=3)
    assert len(top) == 3
    assert len({(c.path, c.start_line) for c in top}) == 3


def test_prose_chunks_are_capped(repo):
    for n in range(5):
        (repo / f"doc{n}.md").write_text("The wiki slug is generated from the title.\n")
    top = Retriever(repo, chunk_repo(repo), use_rg=False).search("wiki slug generated title")
    assert sum(c.path.endswith(".md") for c in top) == 2
    assert "lib/wiki.ts" in {c.path for c in top}


def test_extra_ranker_is_fused(repo):
    chunks = chunk_repo(repo)
    billing = next(i for i, c in enumerate(chunks) if c.symbol == "fetchSuiUsdPrice")
    retriever = Retriever(repo, chunks, use_bm25=False, use_rg=False, extra_rankers=(lambda q: [billing],))
    assert retriever.search("anything")[0].symbol == "fetchSuiUsdPrice"


def test_bm25_length_normalisation_is_configurable(repo):
    assert Retriever(repo, chunk_repo(repo), use_rg=False)._bm25.b == 0.75
    assert Retriever(repo, chunk_repo(repo), use_rg=False, bm25_b=0.3)._bm25.b == 0.3
