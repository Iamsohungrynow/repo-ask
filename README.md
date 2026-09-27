# repo-ask

`repo-ask` is a small command-line tool that answers questions about a codebase and backs every
answer with `file:start-end` citations. It splits the repo into function- and class-level chunks
with tree-sitter and retrieves the most relevant ones with BM25 plus ripgrep. Claude answers from
those chunks only, and every citation is checked against the chunks that were actually retrieved.
If there is no supported answer it prints `Not found in this repo` instead of guessing. The whole
tool is about 670 lines of Python plus about 460 lines of tests.

Built with [Claude Code](https://claude.com/claude-code) as a pair programmer: Claude Code wrote
most of the code, tests and evaluation questions to my specification.

## Install

```bash
git clone https://github.com/iamsohungrynow/repo-ask && cd repo-ask
pip install -e ".[dev]"          # Python 3.11+; ripgrep (`rg`) on PATH is recommended
export ANTHROPIC_API_KEY=...     # read from this variable only; never commit it (.env is gitignored)
export REPO_ASK_MODEL=claude-sonnet-5   # optional; this is the default
pytest
```

The index is written to `~/.cache/repo-ask/` (override with `REPO_ASK_HOME`), never into the
repository being indexed.

## Examples

All output below is real, from Soliton at commit `9f01a58`, run on 2026-09-27.

```text
$ repo-ask index ../soliton
Indexed 2269 chunks from 279 files -> ~/.cache/repo-ask/soliton-d8a19ba1fd.json
```
(The index path is shortened; the run used a custom `REPO_ASK_HOME`.)

```text
$ repo-ask ask "where does the runner fall back to keyword recall?" --repo ../soliton --show-chunks
  retrieved supabase/config.toml:401-414
  retrieved engine/memory/runner.mjs:616-644 recallRunnerTaskMemory
  retrieved legacy/openclaw-runtime/server/custom-server.js:15-34 standaloneConfig
  retrieved docs/implementation/sui-walrus-build-plan.md:561-639
  retrieved engine/memory/runner.mjs:187-199 recallMemWalMemory
  retrieved engine/memory/runner.mjs:576-584 taskRecallQuery
engine/memory/runner.mjs:616-644
engine/memory/runner.mjs:187-199
  In `recallRunnerTaskMemory`, if the MemWal recall call fails or is unavailable, the catch block returns an empty array, and the function then merges results with `indexedProjectMemories`, which serves as the keyword/indexed fallback for recall data (built from the task's title, kind, and payload via `taskRecallQuery`).
```

The `retrieved` lines come from `--show-chunks`. The answer itself is the validated citations
followed by the indented answer, or `Not found in this repo`.

```text
$ repo-ask eval eval/soliton.yaml --repo ../soliton --out eval/results/2026-09-27-full.json
id   hit@6  lines  answer   latency  question
q01  yes    yes    yes        3.74s  Where is the memory recall API route?
...
q17  no     no     no         2.73s  How does the app decide whether a user is an admin?
...
u1   -      -      yes        2.30s  Where does the Kafka consumer process trade events?
...
retrieval hit@6 (file):   18/20 = 90%
retrieval hit@6 (lines):  13/20 = 65%
answer accuracy:          13/20 = 65%
abstention accuracy:      3/3 = 100%
rejected citations:       0
errors:                   0
average latency:          3.29 s
```

Add `--retrieval-only` to skip the answer step and run without an API key.

## Evaluation

The target is [Soliton](https://github.com/iamsohungrynow/Soliton), commit `9f01a58`, cloned
read-only. The eval file [`eval/soliton.yaml`](eval/soliton.yaml) has 20 answerable questions with
expected files and line ranges, plus 3 questions that have no answer in the repo. Questions 1-5
are the seed questions; 6-20 were drafted with Claude Code by reading the code. Every expected
range in both eval sets was checked against the code: it answers its question and starts and ends
on the definition it names. Two questions accept a second location (marked `also`). A separate
held-out set is described [below](#held-out-set).

- **hit@6 (file)**: an expected file is among the 6 retrieved chunks.
- **hit@6 (lines)**: a retrieved chunk overlaps an expected line range, which is stricter.
- **answer accuracy**: the answer has a validated citation in an expected file that overlaps the
  expected lines.
- **abstention accuracy**: the 3 unanswerable questions get `Not found in this repo`.

Run on 2026-09-27 (per-question results in [`eval/results/`](eval/results/)):

| Configuration | hit@6 (file) | hit@6 (lines) | Answer accuracy | Abstention | Avg latency |
|---|---|---|---|---|---|
| **Hybrid: BM25 + ripgrep (default)** | **18/20 (90%)** | **13/20 (65%)** | **13/20 (65%)** | **3/3 (100%)** | **3.29 s** |
| BM25 only (`--no-rg`) | 18/20 (90%) | 14/20 (70%) | – | – | – |
| ripgrep only (`--no-bm25`) | 2/20 (10%) | 0/20 (0%) | – | – | – |

The answer, abstention and latency columns come from the full run,
`repo-ask eval eval/soliton.yaml --repo ../soliton --out eval/results/2026-09-27-full.json`, which
printed `rejected citations: 0` and `errors: 0`. Every question whose retrieved chunks overlapped
the expected lines was answered correctly, and none of the others was. So on this set, answer
accuracy is limited by retrieval, not by the model. The ablation rows are retrieval-only, so they
have no answer columns.

**How these numbers were reached.** The questions were committed before any tuning (commit
`6319687`). Two changes were then made after looking at results on this same set, so the numbers
above are optimistic; the held-out set below is the check on that.

| Stage | Hybrid file | Hybrid lines |
|---|---|---|
| First version | 14/20 | 11/20 |
| + a function's doc comment is kept in its chunk (it was being split into a separate window) | 14/20 | 12/20 |
| + ripgrep only fires on real identifiers (camelCase, snake_case, backticked) | 18/20 | 13/20 |

The first version also treated any word that matched a symbol name as an identifier. Plain words
like "project" (in 144 chunks) and "prompt" (65) then became ripgrep searches with arbitrary matches, and
the hybrid scored *worse* than BM25 alone. On this eval set ripgrep still adds nothing: only 2 of
the 20 questions contain an identifier. On q04 ("MemWal") it pushes the defining chunk out of the
top 6, because `MemWal` appears in 76 chunks. The unit tests show the case where it does
help: an identifier query returns the defining chunk first.

### Held-out set

[`eval/soliton-heldout.yaml`](eval/soliton-heldout.yaml) has 10 answerable and 2 unanswerable
questions about parts of Soliton the development set does not touch (Sui wallet challenges, the
Walrus HTTP store, memory branches and commit graphs, the auth proxy, onboarding, wiki attachments,
token quota events). It was written after all the tuning above and committed before it was first
run, and nothing has been tuned on it since.

| Configuration | hit@6 (file) | hit@6 (lines) | Answer accuracy | Abstention | Avg latency |
|---|---|---|---|---|---|
| **Hybrid: BM25 + ripgrep (default)** | **9/10 (90%)** | **9/10 (90%)** | **8/10 (80%)** | **2/2 (100%)** | **3.12 s** |
| BM25 only (`--no-rg`) | 9/10 (90%) | 9/10 (90%) | – | – | – |
| ripgrep only (`--no-bm25`) | 0/10 (0%) | 0/10 (0%) | – | – | – |

The full run, `repo-ask eval eval/soliton-heldout.yaml --repo ../soliton --out
eval/results/2026-09-27-heldout-full.json`, printed `rejected citations: 0` and `errors: 0`.
Retrieval held up: file hit@6 is the same as on the development set. The line score is higher (90%
against 65%), but that more likely means these questions are easier than that retrieval
generalises better. They were drafted with Claude Code by someone who knew how the retriever
works, so their wording follows the code closely.

The two wrong answers fail in different ways:

- **h07** ("which onboarding step a user still has to complete") is a retrieval miss, the same
  pattern as q17. The model cited the page that calls `getRequiredOnboardingStep`, not the function
  that makes the decision.
- **h01** ("how long is a Sui wallet challenge valid") retrieved the right function, which computes
  `Date.now() + WALLET_CHALLENGE_TTL_MS`. The constant's value, 10 minutes, is defined on line 7,
  outside the retrieved chunk, so the model replied `Not found in this repo` instead of guessing.
  That is the intended behaviour, but it shows a gap: a chunk does not carry the module-level
  constants it uses.

**A tried fix that did not work.** q17 and q18 looked like a short-chunk bias, so BM25 length
normalisation was made configurable (`--bm25-b`, default 0.75) and swept on the development set
only:

| BM25 `b` | 1.0 | 0.9 | **0.75** | 0.6 | 0.5 | 0.4 | 0.3 | 0.2 | 0 |
|---|---|---|---|---|---|---|---|---|---|
| Hybrid file | 18/20 | 18/20 | **18/20** | 16/20 | 15/20 | 15/20 | 14/20 | 14/20 | 10/20 |
| Hybrid lines | 12/20 | 13/20 | **13/20** | 12/20 | 12/20 | 12/20 | 11/20 | 11/20 | 8/20 |

No value is better than the default, and q17 and q18 miss at every value, so the default stays.
The real cause is different: the question's words ("admin", "user") are also in the paths and
symbol names of many small admin route handlers (`src/app/api/admin/users/...`), and path and
symbol are counted twice, so those chunks outrank `isAdminUser` whatever their length.

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
dependency, and the ablation above does not yet show a retrieval gap they would clearly close.

**Validated citations.** The model returns JSON (`answer`, `citations`, `found`), constrained by
the API's structured-output schema. The reply is parsed and shape-checked anyway, with one retry.
A citation is kept only if its whole range lies inside a single retrieved chunk. If `found` is
false, or no citation survives, the tool prints `Not found in this repo`. An answer is never shown
without evidence the tool itself retrieved, which makes the "don't guess" rule checkable instead of
a prompt instruction the model may ignore. Rejected citations are reported on stderr.

## Limitations

- **Common words in paths drown out the right function.** Small route handlers whose paths repeat
  the question's words ("admin", "user") outrank the one function that answers it (q17, q18, h07).
  Changing BM25 length normalisation does not help (see the sweep above). Down-weighting path
  tokens might, but it has not been tried.
- **Only files are evaluated, not answer text.** A citation in the right place with a wrong
  explanation still counts as correct.
- **A citation is valid only if it fits in one chunk.** A correct answer spanning two adjacent
  chunks is rejected.
- **The index is never refreshed.** Re-run `repo-ask index` after the code changes. There is no
  incremental update.
- **The stemmer is naive** (`walrus` becomes `walru`). This is consistent between queries and
  documents, but crude.
- **The eval sets are small.** 20 + 3 development and 10 + 2 held-out questions from one repo,
  drafted with Claude Code rather than collected from real users.
- **Chunks lack the constants they use.** A function that reads a module-level constant is
  retrieved without the constant's value (h01).

## Scaling to a large Java monorepo, and measuring whether it helps

- **Indexing.** Incremental re-indexing keyed on git blob hashes, run in CI on merge. Per-module
  shards so a question can be scoped to a service. Java chunks carry the package, class, method
  signature and annotations (for example `@RestController` or `@Transactional`).
- **Retrieval.** At that size BM25 needs a proper engine (Lucene or OpenSearch), with symbol and
  signature fields boosted. The ripgrep step becomes a symbol index (ctags, SCIP or LSIF), so
  "where is X defined, who calls X" is a lookup and not a text search. Embeddings earn their cost
  there, as a third ranker for paraphrased questions. Identifiers that appear in thousands of files
  get IDF-style down-weighting (the q04 lesson above).
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

## Licence

MIT, see [LICENSE](LICENSE).
