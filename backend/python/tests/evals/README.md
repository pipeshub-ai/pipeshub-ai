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
