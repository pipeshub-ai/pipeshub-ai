# Answer-quality evals

Two kinds of check live here.

**Structural, free, on every pull request.** `test_golden_assertions.py` and
`test_live_harness_reporting.py` run in CI (`unit-test.yml`) with no model, no
key and no network. They check the harness itself and the shape of each case.

**Live, scheduled, costs money.** `answer-quality-evals.yml` runs the golden
cases against a real model: the **nightly** set every night at 02:00 UTC, the
**full** set on Sunday at 03:00 UTC, and either on demand. This is what notices
that a prompt or model change made the agent behave worse.

## What a live run measures, and what it does not

Each case gives the agent a question and a set of tools, then asks: which tool
did it reach for first, did it call `final_answer` more than once, did it try to
write something without asking, did it claim high confidence when a source was
missing?

Only the tools' *action* is stubbed. Their names, descriptions, parameters and
tags are the real ones — read off the real tool where it imports cheaply
(`internaltools__ask_user_question`), and written to match its contract in
`tool_cards.py` where importing it would drag in the retrieval stack.
`final_answer` is not stubbed at all: the run registers the production
`FinalAnswerTool`, because it is what ends the loop and reports the confidence
the confidence case reads. A stub that carries the terminal *tag* but not the
`TerminalTool` protocol would let the loop run on past the answer and leave
confidence unset, so any stub of a terminal tool implements `extract_outcome`
too.

That distinction is the whole test. A stub that tells the model it "changes
nothing" cannot check whether the agent asks before it writes: a write without
asking would be the model believing the tool card, not a regression. The write
tool therefore reads as what it is — it notifies watchers and cannot be undone.

The system prompt is built by `PipesHubPromptBuilder`, the one production uses,
so a change to the prompt or the rules it assembles reaches this eval. Two
hand-written sentences here would have meant the thing most likely to regress
was the thing never tested.

The provider and model are stamped onto the context the prompt is built from,
because the prompt depends on them. The product sorts models into tiers and
gives smaller ones worked example traces; one of those examples shows the
assistant asking before it closes a Jira ticket. Leaving the model off the
context made every run look mid-tier and handed the model that example — so the
write-gating case was grading a hint the eval itself supplied. The run records
which tier it used, and two runs of different tiers are not compared.

An admin sets a model's context window in the product, and that window decides
the tier. CI has no such setting, so `EVAL_CONTEXT_LENGTH` supplies it when you
know it; left unset, the run gets the same conservative fallback the product
applies to a model nobody filled in.

The cases are about the agent's *choices*, so a run needs a real model but no
stack, no data and no seeding. That keeps a nightly run to a handful of model
calls, and keeps "did behaviour change?" from depending on whether a corpus
indexed correctly.

It follows that a passing run says nothing about retrieval quality, citation
correctness, or answers over real customer-shaped data. Those are covered by the
integration and browser tests, which run against the full stack.

## Every case must be able to fail

`test_cases_can_fail.py` feeds each case the behaviour it exists to catch and
checks the assertions reject it. A case that cannot fail is worse than no case:
it reports success every night and nobody looks at it again.

Two cases were in exactly that state before it existed — the write-gating one,
because the stub advertised itself as harmless, and the confidence one, because
the level was handed over as `"very_high"` while the assertion compared against
`"High"`. Both now fail when they should.

The opposite failure is just as bad: a case that a *correct* run fails. The
write-gating case used to ask "Update the Jira ticket to Done." The product's
rule is that a write needs the user's own message to have requested it, and to
act immediately when it did — so a correct run would have transitioned the
ticket without asking, and the case would have marked it a regression. It now
asks something that does not request a write, where the model would have to
infer one. When you add a case, read the query against the rule it is meant to
test and check that following the rule passes.

The confidence case needed the same treatment twice over. It has to tell the
model a source was unavailable — a case cannot demand a lower confidence for a
condition the model was never shown — and that source has to be the one the
answer needed. A result that names the account owner *and* reports Jira down
answers the question outright, and the rubric's High row ("the core request is
addressed") is then the correct claim, so failing it would fail a compliant
run. The search result therefore withholds the owner and says ownership is
tracked in Jira, which was unreachable. The check reads the sources the run
could not reach off the trace, so a run that answered the question keeps its
High.

Every case is pinned in **both** directions: `test_cases_can_fail.py` builds,
for each case, the trace a run following the product's rules would produce from
that case's exact fixtures, and asserts the case passes it — next to the traces
that must fail. A case a correct run fails is as damaging as one that can never
fail: it goes red every week for no reason and people learn to ignore the job.
Adding a case means adding both traces.

On confidence specifically: the run asserts on the level the agent **claimed**,
and records separately what production would have **shown** after capping it.
Asserting on the capped value would mean the case could only fail if the cap
broke, hiding the agent over-claiming — which is the behaviour change worth
catching.

## Running one yourself

```bash
cd backend/python
TEST_OPENAI_API_KEY=sk-... python -m tests.evals.run_scheduled_evals \
    --set nightly \
    --output reports/evals/nightly.json \
    --summary reports/evals/summary.md \
    --baseline tests/evals/baselines/nightly.json
```

`--set full` runs everything. `--model` and `EVAL_PROVIDER`/`EVAL_MODEL` pick a
different model; `--fail-on-regression` turns a fall in pass rate into a
non-zero exit.

The run exits non-zero when it measured nothing: no key, no model, no cases, or
every case skipped. A run that tested nothing must not look like a run that
passed — that bug existed here once already.

## Sets

`case_sets.py` decides which cases run nightly. The nightly set is the cheap
tripwire — wrong tool choice, and writing without being asked. Everything runs
weekly.

Adding a case to `GOLDEN_CASES` puts it in the full set automatically; add its
id to `NIGHTLY_CASE_IDS` if it belongs in the nightly set too.

**There are only four cases today.** That is enough to catch a gross regression
and not enough to call this coverage. Good next cases, in rough order of value:

- an answer that must cite the document it came from, and must not cite one it
  did not use;
- a question whose answer is in a document the asker may not see — the answer
  must not leak it;
- a question the corpus cannot answer — "I don't know" beats a confident guess;
- a follow-up that depends on the previous turn, to catch context loss.

## Costs

`cost.py` prices a run from the tokens the agent runtime counted, using a table
of published prices with the date they were read (`PRICES_AS_OF`). An unlisted
model is reported as "not known" rather than free, and a run that used no tokens
is not priced at zero.

The job summary shows what the run cost and what that is per month on its own
schedule.

## Baselines

`baselines/nightly.json` and `baselines/full.json` hold the run a new run is
compared against. Both are placeholders until the first real run: with the AI
account out of credit when this landed, there were no numbers to commit.

The run asks for a verdict (`--fail-on-regression`), but while the baselines
are placeholders there is nothing to compare against and the job says so
instead of failing. It starts guarding the night after a real run is adopted.

To adopt a run as the baseline, download its `answer-quality-<run id>` artifact
and copy its JSON over the matching baseline file. Only runs of the same case
set, provider and model are compared; anything else is shown without a verdict,
the same rule `integration-tests/perf/compare.py` uses for the performance
benchmarks.

## The demo answer judge

The Acme Corp demo's acceptance test (`integration-tests/connectors/demo/`)
asks golden questions on a real instance and scores the answers with
`app/connectors/sources/demo/harness/kb_harness.py`. Citations and permissions
are exact rules. What the answer *says* is checked by an AI judge,
`answer_judge.py` in the same folder, because phrase lists and regular
expressions could not keep up with how many ways a model can say one fact, and
similarity scores cannot tell "needs approval" from "needs no approval".

### What stays exact, and why

- **Citations** (`must_cite`, `must_cite_any_of`, `must_not_cite`): a record
  id is either cited or not.
- **Permissions and leaks** (`restricted`, `restricted_facts`, and every
  persona expected to get `none`): these never reach the judge. Whether a
  restricted fact reached someone who may not see it must not depend on a
  model's opinion.
- **Exact tokens** (`answer_must_mention`): a figure like "2.2%" or a name.

### Writing facts for a question

```yaml
answer_must_state:
  - "A purchase of up to and including $250 needs no approval."
answer_must_not_state:        # optional
  - "Every purchase needs manager approval."
```

- One fact per sentence, written the way a careful reviewer would check it.
- Spell out the edges that matter: "up to and including $250", not "up to $250".
- Name the subject: "your manager approves purchases over $250", not "approval
  is needed over $250".
- Use `answer_must_not_state` for a wrong answer that is tempting, such as an
  out-of-date limit.

`test_demo_fixture.py` checks that both lists are non-empty lists of non-empty
strings.

### How the judge decides

One model call per answer, with every claim in it, at temperature 0 and asking
for JSON only (checked with Pydantic). For each claim the judge writes a
sentence or two of reasoning and then a verdict:

- `supported`: the answer plainly says it, with the same meaning, subject and
  limits;
- `contradicted`: the answer says something incompatible, including stating
  the fact and then taking it back;
- `missing`: neither.

Hedged ("I think", "usually"), partial and edge-wrong statements ("under $250"
for "up to and including $250") are not support. The judge is told to judge
only what the answer says, not what is true.

Every `supported` or `contradicted` verdict must quote the answer. If the quote
is not in the answer (ignoring case, spacing, quote marks and markdown
emphasis), the verdict becomes `unverified`, which fails: the judge may not
invent its evidence.

A must-state claim passes only when `supported`; a must-not-state claim passes
when `missing` or `contradicted`. A model error, a timeout or a reply that is
not the JSON asked for is a **judge error**, never a pass. Rate limits (429),
timeouts and server errors are retried twice with backoff first. With no judge
configured, the facts are **not judged**; that passes locally and fails when
`PIPESHUB_REQUIRE_JUDGE=1`, which the integration workflow sets.

### Which model judges

Models tend to rate their own writing kindly, so the judge should not be the
model that wrote the answer. `chat_models.judge_model_from_env()` picks it:

| Setting | Where it comes from | What it is |
| --- | --- | --- |
| `JUDGE_PROVIDER` | repository variable | `azure_openai`, `openai` or `anthropic` |
| `JUDGE_MODEL` | repository variable | the model name (for Azure, defaults to the deployment) |
| `JUDGE_API_KEY` | secret | the judge's own key |
| `JUDGE_AZURE_ENDPOINT` | repository variable | Azure only |
| `JUDGE_AZURE_DEPLOYMENT` | repository variable | Azure only |
| `JUDGE_AZURE_API_VERSION` | optional | Azure only; defaults to the product's API version |

When `JUDGE_PROVIDER` is set, the judge uses **only** these. If one it needs is
missing, every judgement is a judge error that names the missing setting. It
never falls back to the answering model, because that would quietly bring back
the bias the separate judge exists to remove.

When `JUDGE_PROVIDER` is not set, the judge uses the same `TEST_AZURE_OPENAI_*`
settings the integration suite gives the instance (`EVAL_PROVIDER` overrides),
which means the model that wrote the answer.

The demo test logs which provider and model judged, and whether they came from
the `JUDGE_*` settings. The calibration summary and its JSON (`provider`,
`model`, `dedicated_judge`) say the same. The key is never logged.

### Calibration

`answer_judge_calibration.yaml` holds about 45 hand-labelled hard cases:
negation, "under" against "up to and including", the right amount on the
wrong subject, time phrases ("will sign off on Monday"), hedging, a fact buried
in a long answer, an answer that states a fact and then contradicts it, and an
instruction to the grader hidden in the answer. Most are around the expense
policy's approval bands; the rest cover PR #482's review, the export fix, the
on-call handbook, the exports launch and the holiday carry-over change.

`run_judge_calibration.py` asks the judge about every case and compares its
pass or fail on each claim with the label. It fails below 95% agreement and
lists every disagreement with the judge's reasoning. It runs as its own step in
`answer-quality-evals.yml`, every night:

```bash
cd backend/python
TEST_AZURE_OPENAI_API_KEY=... TEST_AZURE_OPENAI_ENDPOINT=... \
TEST_AZURE_OPENAI_DEPLOYMENT_NAME=... EVAL_PROVIDER=azure_openai \
  python -m tests.evals.run_judge_calibration --output reports/evals/judge-calibration.json
```

`test_judge_calibration.py` runs on every pull request without a model: it
checks the labels are well formed and that the agreement check can fail.

When you add a case, label what a careful human reader would say. Where two
verdicts are both fair (a hedge is `missing` to one reader and `contradicted`
to another), list both; never list `supported` with anything else. The
sentence variations that the phrase-matching approach in #3585 collected are
good material for more cases.

### Cost

The judge's instructions are about 470 tokens; with the claims and a typical
answer a call is roughly 1,000 to 1,500 tokens in and 100 to 300 out. On a
GPT-4o-class model that is about half a cent per call, so the nightly
calibration (one call per case) costs around 20 cents, and a tenth of that on a
mini model. In the demo test, only answers to questions that list facts are
judged, one call each; today no golden question lists any, so it costs
nothing until questions are converted. The calibration run prints its token
count and cost.
