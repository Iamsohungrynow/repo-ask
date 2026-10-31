# Design notes

Why `repo-ask` is built the way it is, and how the same approach would scale. Evaluation numbers
quoted here are from [evaluation.md](evaluation.md).

## Design choices

**Function-level chunks.** A function or class is the unit engineers ask about and cite. Fixed
windows cut functions in half, so the model sees half the logic and cites ranges that don't match
the code. Chunks also carry a symbol name, which is a strong retrieval signal. Definitions longer
than 150 lines are split into their nested definitions (in Java: class into methods). Lines outside
any definition, such as imports, config objects and non-code files, become 80-line windows, so
every non-blank line belongs to exactly one chunk. That matters for citation checking.

**Hybrid retrieval.** BM25 handles natural-language questions ("how is a wiki slug generated").
The tokenizer splits `fallbackRecallFromRecords` into its words, so the question and the code share
vocabulary. ripgrep handles the exact-identifier case, where an engineer pastes a name and wants its
definition. It is exact, needs no index, and ranks definition sites above usages. The two rankings
are merged with reciprocal rank fusion, `sum(1 / (60 + rank))`, which needs no score calibration.
Prose files are capped at 2 of the 6 slots, because docs mention everything and crowded out code.
Embeddings would be one more ranker (`extra_rankers`). They are off because they add a model
dependency, and the [ablation](evaluation.md#development-set) does not yet show a retrieval gap they would clearly close.

**Validated citations.** The model returns JSON (`answer`, `citations`, `found`), constrained by
the API's structured-output schema. The reply is parsed and shape-checked anyway, with one retry.
A citation is kept only if its whole range lies inside a single retrieved chunk. If `found` is
false, or no citation survives, the tool prints `Not found in this repo`. An answer is never shown
without evidence the tool itself retrieved, which makes the "don't guess" rule checkable instead of
a prompt instruction the model may ignore. Rejected citations are reported on stderr.

## Scaling to a large Java monorepo, and measuring whether it helps

- **Indexing.** Incremental re-indexing keyed on git blob hashes, run in CI on merge. Per-module
  shards so a question can be scoped to a service. Java chunks carry the package, class, method
  signature and annotations (for example `@RestController` or `@Transactional`).
- **Retrieval.** At that size BM25 needs a proper engine (Lucene or OpenSearch), with symbol and
  signature fields boosted. The ripgrep step becomes a symbol index (ctags, SCIP or LSIF), so
  "where is X defined, who calls X" is a lookup and not a text search. Embeddings earn their cost
  there, as a third ranker for paraphrased questions. Identifiers that appear in thousands of files
  get IDF-style down-weighting (the [q04 lesson](evaluation.md#development-set)).
- **Eval first.** Build a question set from real internal questions (chat threads, onboarding
  docs, code-review comments), split into dev and held-out halves. Report held-out numbers only.
- **Measuring whether engineers get unblocked faster:**
  - *Answer acceptance rate*: the share of answers where the engineer opened a cited file and did
    not re-ask or escalate.
  - *Time to first correct file*: from question to opening the file they eventually edit, compared
    with a control group using code search.
  - *Time from first question to merged PR*, for onboarding tasks and small bug fixes.
  - *"Not found" precision*: how often a human confirms it really isn't in the repo. A wrong
    "not found" costs trust as much as a wrong answer.

  These would come from opt-in CLI or IDE telemetry and an A/B rollout, not from a survey.
