# Evaluation on Soliton

This page is the full write-up behind the numbers in the [README](../README.md#results). The
target is [Soliton](https://github.com/iamsohungrynow/Soliton) at commit `9f01a58`, cloned
read-only. Per-question results are in [`eval/results/`](../eval/results/).

To run the same evaluation:

```bash
git clone https://github.com/iamsohungrynow/Soliton ../soliton && git -C ../soliton checkout 9f01a58
repo-ask eval eval/soliton.yaml --repo ../soliton --out eval/results/$(date +%F)-full.json
repo-ask eval eval/soliton-heldout.yaml --repo ../soliton --retrieval-only
```

## Sample output

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

## Development set

The eval file [`eval/soliton.yaml`](../eval/soliton.yaml) has 20 answerable questions with
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

Run on 2026-09-27 (per-question results in [`eval/results/`](../eval/results/)):

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

**Re-checked on 2026-10-31**, after file listing switched to `git ls-files` so gitignored files are
skipped. A fresh clone of Soliton at `9f01a58` gives a byte-identical index (2269 chunks from 279
files), and all six retrieval-only runs (both eval sets, all three configurations) retrieve the
same chunks for every question as the files in [`eval/results/`](../eval/results/). The answer,
abstention and latency columns were not re-run; the model would see the same retrieved chunks.

**How these numbers were reached.** The questions were committed before any tuning (commit
`aba5820`). Two changes were then made after looking at results on this same set, so the numbers
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

## Held-out set

[`eval/soliton-heldout.yaml`](../eval/soliton-heldout.yaml) has 10 answerable and 2 unanswerable
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

## A tried fix that did not work

q17 and q18 looked like a short-chunk bias, so BM25 length normalisation
was made configurable (`--bm25-b`, default 0.75) and swept on the development set
only:

| BM25 `b` | 1.0 | 0.9 | **0.75** | 0.6 | 0.5 | 0.4 | 0.3 | 0.2 | 0 |
|---|---|---|---|---|---|---|---|---|---|
| Hybrid file | 18/20 | 18/20 | **18/20** | 16/20 | 15/20 | 15/20 | 14/20 | 14/20 | 10/20 |
| Hybrid lines | 12/20 | 13/20 | **13/20** | 12/20 | 12/20 | 12/20 | 11/20 | 11/20 | 8/20 |

No value is better than the default, and q17 and q18 miss at every value, so the default stays.
The real cause is different: the question's words ("admin", "user") are also in the paths and
symbol names of many small admin route handlers (`src/app/api/admin/users/...`), and path and
symbol are counted twice, so those chunks outrank `isAdminUser` whatever their length.

## Known failure modes seen in these runs

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
