# ADR-RUN-03: One answer contract on every path: ask back, say what was not found, confidence from grounding

- Extends: [ADR-RUN-01](ADR-RUN-01-run-outcome-architecture.md) D1 (the outcome is a typed record; words come from it,
  not from a model) and D5 (a delegated task is a `TaskSpec`), to the question of whether an answer is about what the
  person asked.
- Related: [ADR-OWD-09](ADR-OWD-09-policy-and-approvals.md) (asks and approvals), DEEPROUTE (deep work goes to the
  engine), C3.70 (`ask_user` at the gateway).
- Row: ANSWER-CONTRACT-1.

## Context

In a manual test (2026-11-11) a person asked for **a risk report for "SAAS launch"**. The chat handed the request to a
Codex job. The job searched the knowledge base seven times and listed files, found nothing about a SAAS launch, and
still wrote a full "preliminary risk report" from a product FAQ and a generic on-call runbook, assuming the FAQ's
product was the one launching. A "Medium" confidence chip sat on top. The tester's summary: the answer was unrelated to the request, it asked
no question back, it never said it could not find the information, and it did not show how confident it was. These are the parts of PipesHub that must keep working as they do in the classic
chat.

### How the classic chat keeps the contract today

| | Classic chat (query service) and the harness native loop | The job path (DEEPROUTE → `code_task` → engine → hand-back) |
|---|---|---|
| **(a) Ask back** | Pre-run: the intent call (`router.select_loop_and_goal`) returns clarifying questions for a fatally ambiguous request; the turn ends on the question card (`clarification.emit_pre_run_clarification`; harness: the goal slot's `clarifyingQuestions`, `ClarificationGate`, `PipesHubPresenter._clarify`). Mid-run: `internaltools__ask_user_question`, which ends the turn (`hooks/ask_user_question.py`: at most `MAX_CARDS_PER_REQUEST` = 2 cards per request, never the same question twice; prompt: "Only ask the user when intent is genuinely ambiguous"). Nothing asks because a subject was not found: that is (b)'s prompt. | `deep_route.classify` hands off up front on the request's words alone, with no check that its subject exists. The engine child had no `ask_user`: `asking.asks_person` offered it only to `interactive` runs ("a child's question has nowhere to go"), so the tested job could not have asked. |
| **(b) Say what was not found** | Prompt text only: internal search's `_GROUNDING` ("If the answer is not present in the retrieved context, say so explicitly rather than guessing"), the agent prompt's "Empty results: say so plainly", `_NO_KNOWLEDGE_SOURCES`; a failed or refused upfront search becomes a constraint (`prefetch.failure_notice`, `filter_notice`). The harness loop builds the same prompt (`PipesHubPromptBuilder`). | The engine is not given PipesHub's prompt, and its own task instructions say nothing about it (they are frozen by SR-17). Its hand-back (`ChildReport`: output, success, needs_input, truncatedBy) says nothing about what its searches found, and the parent presents the engine's prose (`DeepRouteDirector._presented`) or rewrites it under `PRESENT_NOTE`. Nothing composes a not-found line. |
| **(c) Confidence** | The model picks it from a rubric (`confidence.rubric_text`, the prompt's "Answer Confidence" section) and writes a trailer the finalizer parses off (`parse_confidence_from_answer`, `AnswerFinalizer`); an agent's `final_answer` tool reports one instead. `confidence.reconcile` (cap at Medium with no citation or an unavailable source) exists and has no caller. A pre-run question's answer hard-codes "Medium". | The engine is never asked for one, and its hand-back carries none. The test's "Medium" can only be a trailer the parent model wrote on its own answer under the rubric ("partially completed, or the results were ambiguous"); nothing computed it from the job's seven empty searches. |

### Design fault

Grounding was a prompt instruction, not a fact. On the classic path one model sees the retrieval and is told to
behave; on the job path the model that writes the answer is not the one that searched, the one that searched was
never told, and nothing between them carried what the searches found. Every symptom in that test follows from that: no
question (no signal to ask on, and no way for the job to ask), no not-found line (no fact to say it from), and a
confidence that reflects a model's mood (no signal to compute it from).

## Decision

Grounding is a typed fact the harness builds from search results, never from what a model wrote. One owner module,
`app/harness/grounding.py`, holds the signal and its words; the goal step, the hand-back, the director and the
presenter read only its types.

### D1. Ground before delegating

- When the up-front route hands a request to a job, the goal step checks that the **subject the request names** is
  in what the person can reach (`GroundingGate`, `assembly/grounding_gate.py`).
- The subject is read deterministically (`named_subject`): the first quoted phrase, else the first capitalised name
  that does not start a sentence (formats, roles, months and the like excluded). A request that names nothing is not
  checked and never asked about.
- The evidence is the search the chat already ran (the mode's upfront retrieval, `shape_chat_goal`'s prefetch), else
  **one search for the subject alone**: a built-in assistant turn's own retrieval within its knowledge filters, or an
  agent's own knowledge search tool (`knowledgegraph__search`, else `retrieval__search_internal_knowledge`), called
  once in the run's tool scope, so the agent's sources and the caller's access apply as to the model's call. The tested
  chat is the assistant agent, which has no prefetch. What the probe writes into the citation state is put back: the
  model never saw it, so its answer cannot cite it. A turn probes only when it is routed to a job up front, or its
  request carries a deep-work signal (`classify`'s signals: a report, an analysis, a plan…) and the model may hand
  off itself; a turn that stays a chat reply pays no search.
- A hand-off the model makes itself (`code_task`) while the subject was not found and not yet asked about is refused
  (`NOT_FOUND_REFUSAL`), and the director takes the next turn with the same question card: the structure, not the
  model, decides that the person is asked first.
- `assess` finds the subject when one hit holds **every word of it** (stop words dropped, light stemming), in the hit's
  text, record name or connector. Relevance scores order the near matches but do not decide: they are not comparable
  across vector stores and search modes, and the person can check "this document does not mention it".
- Not found, and the turn may ask (`may_ask`: someone is in the chat, not a schedule's run or a sub-agent, it does not
  answer a card, and the request has cards left under `MAX_CARDS_PER_REQUEST`), the run **asks first with the existing
  pre-run card** and stays on the loop: no job starts. The card names the subject, offers the near matches, then "Use
  what you can find" (the app adds "Something else").
- The answer starts the next run. `grounding_answer` reads it from the card reply: "Use what you can find" goes on
  with the not-found fact kept (`proceeded`); a picked record or a typed subject is checked again and never asked
  about again.
- The request a hand-off works on, for a run that answers a card, is the request that card asked about followed by
  the answer (`hand_off_request`), both as the person wrote them. Before this, the engine of such a run was handed
  "User selections: …" alone, and the route was decided on those words.
- **Research is not asked about up front.** A request the router reads as research (`classify`'s `research`,
  `deep_research` or `multi_url` signal: "do a deep research on where product X is ahead") is about the web as much as the
  person's knowledge, so a subject missing from the knowledge says nothing; the job's own grounding still applies. A
  job that read the web as well as the knowledge never reports its subject as not found (`job_grounding`: `unchecked`),
  so the not-found line only ever says what is true: nothing about it in the knowledge, and nowhere else looked.
- A search that could not run, or whose filter was refused, is `failed`: nothing is known to be missing, and nothing
  is asked.

### D2. Jobs can ask, and hand back a typed grounding summary

- **Ask.** A job (`run_kind` `child`) is offered the gateway's `ask_user`, as a person's run is (`asks_person`). The
  job's question ends the job `needs_input` as before; its result document carries the question it asked
  (`child_result.asked_question`, from its own question card). The parent's director asks the same question **with the
  run's own question card** (a synthetic `internaltools__ask_user_question` call, which runs like the model's and ends
  the run waiting for the answer). The answer starts the next run, whose hand-off carries it in the request (D1).
  A turn that may not ask (`can_ask`: no card tool, or its cards used) presents the job's result as before.
- **Hand back.** The job's result document carries `grounding` (`job_grounding`): the subject of the person's request
  (the head of its task, `task_spec.request_of`; a card reply's subject is the person's choice), its knowledge searches
  counted from its own `tool.intent` events, and its hits from its citation state, which the citation journal
  restores on every attempt. No model writes any of it.
- The director records the summary on the parent run (`deepRoute.jobGrounding` slot) for every job result, whichever
  path presents it: a routed hand-off's engine answer, or a parent model's own answer after `code_task`.
- The job's step in the chat says what it did not find, from the same summary ("It found nothing about “SAAS
  launch” in your knowledge.", `_result_frame`).

### D3. Confidence from grounding

`confidence_for(grounding, cited=…)`, where `cited` is the answer's citations:

| Grounding | Cited | Confidence |
|---|---|---|
| Subject not found | any | Low |
| Search failed | 0 / ≥1 | Low / Medium |
| Subject found | 0 | Low |
| Subject found, ≥ 2 records about it | ≥ 2 | High |
| Subject found, otherwise | ≥ 1 | Medium |
| No subject named, searches ran | 0 / 1–2 / ≥ 3 | Low / Medium / High |
| No subject named, nothing searched, nothing cited | — | none shown |

The run's grounding is the job's when it checked something, else the check before delegating (`combined`). The
answer's confidence is that value, or a model's own rating when it is lower (`capped`): a model never claims more
than its grounding allows, and an answer with nothing to judge from shows no confidence rather than a default. A
question is not an answer (ANSWER-CONTRACT-FU-5): a turn that ends on a question card carries no confidence on any
path, the classic chat's own clarification (`clarification._emit_text_answer`) and the harness's pre-run card alike,
and the chat draws no chip on a row that ended on its card (still open, skipped by a new message, or replied to in the
chat); an answered card's row shows its answer's confidence.

### D4. The person never reads a confident report about something that was not found without being told first

When the run's grounding says not found, the presenter (`PipesHubPresenter._answer`, `_grounded`) **leads the answer
with a plain line composed from the typed summary** ("**I couldn't find anything about “SAAS launch” in your
knowledge.** What follows draws on 2 related documents that do not mention it, so check it before you rely on it."),
and the confidence is Low. It holds whoever wrote the rest of the answer.

### What is not changed

- No prompt text. The engine's instructions stay byte for byte (SR-17); the only engine-visible change is that a
  job's tool list now holds `ask_user` with its existing description. The structure works without any model following
  a new instruction: the check, the card, the summary, the line and the confidence are all the harness's.
- The classic chat path (query service, `agent_loop`) is unchanged.

### Consequences

- **UX.** That request now ends on a card ("I couldn't find anything about “SAAS launch” in your knowledge. Did you
  mean one of these?") before any job starts. "Use what you can find" runs the job, and the answer opens with the
  not-found line and a Low confidence; a subject that exists runs as before with a confidence its sources earn. A job
  that needs the person asks them in the chat.
- **Cost.** Nothing for internal search (its prefetch is reused); one search, no model call, for a built-in assistant
  turn without a prefetch that hands off and names a subject.
- **Compatibility.** No contract change: the facts ride the goal slot, a director slot and the child's result document
  (namespaced keys), and the words ride the existing answer and step summary. Old journals have no grounding and are
  presented as before.

## As built

R26-CONTRACT (`harness/v2-r26contract`): `app/harness/grounding.py` (signal, words, confidence),
`app/harness/assembly/grounding_gate.py` (D1), `GoalStep._ground` and `_route` (`loop.py`), `shape_chat_goal` returns
its prefetch (`chat.py`), `hand_off_request` for `code_task` and the director (`assembler.py`), `asks_person`
(`harness_core/gateway/asking.py`), `EngineChildResult` (`engines/child_result.py`), `DeepRouteDirector._job_question`
and `_with_job_grounding` (`deep_route.py`), `run_grounding`/`_grounded` and the pre-run question's confidence
(`presentation.py`), the step summary (`code_task.py`). Tests: `tests/unit/harness/assembly/test_answer_contract.py`.

### Follow-ups (R26-ACFU)

- FU-1, the found case end to end, on the engine lane with indexing (`run.sh durable`/`durable-up` now run indexing):
  `answer-contract-engine.spec.ts` AC-E4 (records uploaded through the KB API name the subject: no question, one job
  that searches and cites both, confidence High) and AC-E5 (a subject only partly present: the record that holds
  part of it is a card option).
- FU-2: `grounding.not_found_view`, `grounding.grounding_card`, `presentation._pending_card` and `_clarification`;
  Node `projection/timeline.notFoundOf`, `transcript.notFoundField`, `audiences.sharedNotFound`, the saved message's
  `notFound`; the web chat's `subject-not-found.ts`, `NotFoundNotice`, `grounding-card.ts` (`cardWords`) and the merged
  row's `durableRunId` (`runtime.ts`).
- FU-3: `parentCanAsk` and the child's `parentAsks` (`domain/child-runs.ts`), `CHILD_ASKS_FEATURE`, the service's
  `parentAsks` (`application/child-run-service.ts`); the worker's `pinned_parent_asks` and `asks_person`.

### Findings from manual testing (2026-11-11), resolved by R26-ACFU

- **Skip asked the same thing again** (ANSWER-CONTRACT-SKIP; conversation `6af49a22a36ec3fe5176e72b`). Read on the test stack
  (logs and conversation, GET only): the card was shown at 16:39:11; the app container restarted at 16:39:29, and the
  three answers sent at 16:39:33, :43 and :49 got 503 `HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE` (the query service
  was not up yet: `fetch failed` on `/api/v1/control-plane/drivers/directory/…/teams`); the web app keeps the card
  when its answer fails, so each Skip "asked again". That is the deploy, not the gate. Two gate faults behind it were
  real and are fixed: Skip sends `[No preference]`, which `grounding_answer` read as a record the person picked,
  named "[No preference]", and searched for; and a new message typed instead of an answer is a new request whose
  turn may show cards again, so the same subject was asked about again. Now `[No preference]` is "Use what you can
  find" (`SKIPPED_ANSWER`: the not-found fact kept, the answer led by it, Low), and the gate never asks about a
  subject this conversation already put to the person, however they dealt with the card (`asked_before`: the
  pre-run card's text or the director's tool row). A request naming a different subject is asked about once.
- **The check searched only PipesHub knowledge** (ANSWER-CONTRACT-SOURCES; conversation `6af49b907886370390f65ceb`,
  `grounding.checked … searches=1 hits=4 relevant=0 status=not_found` with Atlassian Rovo connected). Now, when the
  knowledge does not hold the subject, the gate searches every connected source that can (`grounding_sources`):
  picked by what its tools declare, never by vendor name (kind `read`, a search word in the tool's name, one text
  argument and nothing else required), only where the person's or the instance's rule lets the call run with nobody
  asked (`tool_approvals.decide`, watched false), once each, in the run's tool scope, with a time bound. A result's
  JSON items or text paragraphs are hits; one that says it found nothing has none. Found in a source, the subject is
  found (`found_in`) and the job's task says where (`FOUND_LINE`: "Where PipesHub found “X”: Atlassian Rovo (its
  `search` tool)"); found nowhere, the not-found line, the card and the typed fields name every place looked in
  (`places`: "I looked in your PipesHub knowledge and Atlassian Rovo and couldn't find anything about “X”"), never
  "in your knowledge" alone. Whether the model and the engine then use those tools is R26-MCPUSE's.

## Deviations

- **D-AGENT (resolved in the same package).** An agent run is probed with its own search tool, not a raw retrieval
  call, so a probe never reads past the agent's scope. The probe is not journaled as a tool call: its results are
  shown only to the person who asked (the near matches on the card), never to the model.
- **Words** (resolved by ANSWER-CONTRACT-FU-2, R26-ACFU). The not-found line and the card were English prose. Each
  now travels as typed fields beside its English, which a surface with no UI (the Slack bot, an API text consumer)
  keeps, and the web chat words them through its locale keys in all ten locales:
  - the answer's `notFound` on `x.pipeshub.answer.finalized` (`grounding.not_found_view`: subject, hits, `near` when
    the person could not be asked, `proceeded`, and `line`, the English the answer starts with), carried by Node to
    the completion, its final part and the saved message; the chat draws its own note above the answer and leaves
    the line out. Record names (`near`) ride the typed field only, never the answer's text, and Node drops them for
    any other viewer of a shared chat: they come from the owner's own search.
  - the card's `grounding` beside the tool's payload (`grounding.grounding_card`: subject, near matches and the
    English labels of its own options). Only what the person sees is translated; the reply still sends the English
    question and labels, which `grounding_answer` reads.
  - A `code_task` the model calls while the person is asked first (D1) is refused by design; the presenter knows it
    from the pre-run slots and marks the call hidden, so the steps never read "1 failed". The row an answered
    question's reply lands on follows that reply's run (its summary, cost and activity), so "Waiting for your answer"
    does not stay under an answered question.
- **A job of a run nobody watches** (resolved by ANSWER-CONTRACT-FU-3, R26-ACFU). Node pins on the child, at its
  admission, whether its parent can ask a person (`x.pipeshub.parentAsks`: a person's run, `runKind` interactive, or
  a child whose own pin says true; false under a schedule's or a webhook's firing, `runKind` scheduled), behind the
  worker feature `child_asks`, pinned only while a live worker reads it. The worker's gateway offers the job
  `ask_user` only when it is true (`asks_person(spec, parent_asks=…)`); a job pinned without it is not offered it.
  The job of a schedule's run then never ends waiting on nobody: its grounding rides its result, and the run's answer
  leads with the not-found fact, naming the closest records (the question it could not ask) in the typed field.
- **The pre-run question's "Medium"** (resolved by ANSWER-CONTRACT-FU-5, R26-CARDFAIL). The pre-run card first kept
  the classic clarification's hard-coded "Medium" for golden parity. Both now carry none (D3); the clarification
  goldens (`backend/python/tests/contracts/golden/agent_pre_run_clarification.sse`, its Node upstream copy and the
  journal vector) were re-recorded on purpose, "Medium" to `null`, the only change in them.
- **Live model.** Whether a real engine uses `ask_user` when it is offered, and how often the subject heuristic asks
  where a person would not want it, needs live runs, after the credential rotation (ANSWER-CONTRACT-FU-4).

## Owner amendments

### D5 (R32-REPO, 2026-11-12): the job and the parent say what the run could not do

A manual-test report of 2026-11-12 (a job asked to check out code that never did) is the same fault as D4, for
something that could not be **done** rather than found: the Codex job could clone no repository, was not told so,
read web pages instead, and the answer never said it had not read either code base. Decided from first
principles, best UX, the contract now covers it, built from what the run holds:

- **The brief.** An engine run's instructions end with "What this task can't do here" when the run lacks web access,
  may clone from no git host (an admin turned it off, or no web access, ADR-REPO-01), or has a granted source the
  person has not connected: one line each, why, what would enable it, and "say plainly in your answer what you could
  not do … never present your answer as covering something you could not read"
  (`app/harness/engines/capability_limits.py`, `run_limits`, `limits_instructions`). This is an appended section,
  like the allowed-websites and public-code lines; the SR-17 task instructions stay byte for byte.
- **The hand-back.** The job's result document carries `limits`: the clones that did not happen, read from its
  journal (declined by the person, refused by a rule, a host not allowed, failed; `refused_clones`), then the run's
  own limits. `code_task` shows the parent's model a plain `couldNotDo` note instead of the raw list, and the job's
  step line ends with "It could not read …" for what the job met, whoever wrote the answer.
- **In the answer itself (R33-WORDS, 2026-11-13, R32REPO-FU-1).** The answer leads with a plain line for what the
  run tried and could not do, as D4's not-found line does (after it when both hold), whoever wrote the rest:
  "I couldn't read github.com/example/repo: the clone was declined." It travels typed as `limits` on
  `x.pipeshub.answer.finalized` (`items` of `ext/pipeshub/could-not-do.schema.json`: a kind, the repository, a count;
  and `line`, the English for a surface with no UI), carried by Node to the completion, its final part, the saved
  message and a shared viewer, and worded by the web chat in ten locales above the answer (`CouldNotDoNotice`),
  the line left out. Decisions, on first principles:
  - **Only what the journal shows was tried.** The items are the clones that did not happen (declined, refused by a
    rule, a host not allowed, failed; `met_items`), never a limit the run started with and did not meet: "I couldn't
    search the web" on a task that never needed the web would be a claim nobody made. Those stay the model's to word
    (`couldNotDo`). No model-written text reaches the line.
  - **Read where it happened.** A job's step frame carries its items (`x.pipeshub.tool.result_presented.limits`), and the
    parent's presenter reads them from its own journal (`run_could_not_do`); an engine run's own answer reads its
    journal at the end (`EngineRunPresenter.finished(journal=…)`), so a job's answer and a person's engine run say it
    too. Equal items from two jobs are said once.
  - **Words true for every reader.** "the clone was declined", not "you declined": a shared chat's viewer did not.
- **A file the turn named that never reached the model (R34-FIX, 2026-11-13, R33JGAPS-FU-6).** The same line names
  it: "I couldn't read the attached file notes.txt: access to it was refused." (`attachment.not_accessible`) or "…: its
  content could not be read." (`attachment.unreadable`), with the file's name (`file`). Read from the run's own
  attachment resolution, rebuilt on every attempt, rather than from the journal, since that is where the drop happens;
  an image shown to a model without vision as "[Image attached]" is not one, the model is told. Typed only for a Node
  that names `attachment_limits`. Who may read such a file is ADR-OWD-02 D-ATT-1.
- **D5 amendment (R34FIX-FU-1, R35-SLACK).** Every path that reads a person's attached files names the ones the model
  was not given, through one rule (`attachment_limits.unread_attachments`) and one finaliser (`with_limits`). The
  turn's own attachments (the agent loop and the assistant's chat modes, which share the goal step; the chat modes
  already named them, now held by a test) are read again on every attempt, so their list lives in the attempt's tool
  state (`record_unread`). A steered (mid-run) message is read once, when the kernel consumes it, and a resumed
  attempt does not read it again; so its unread files are journaled with the consumption as a `loop.state_saved` slot
  (`pipeshub.unread_attachments.input.<messageSeq>`, `steered_unread`) and the finaliser reads them back from the
  run's journal (`unread_of`). The answer names each file once, the turn's first, then the steered ones in order, typed
  for a Node that advertises `attachment_limits` and as the plain line otherwise.
- **D5 amendment (R36-COVER: R35SLACK-FU-2, R35SLACK-FU-1).** A coding engine never reads the person's attached
  files itself: every engine run is a `code_task` child (admission resolves an engine for a chat turn only as the
  parent's hand-off authority), and the child is given only the files its parent could read, as input artifacts
  (`childInput`: `attachments: []`). So the run that reads a turn's files is always the loop that hands off, and its
  finaliser names the ones the model was not given, once; the job neither re-reads nor re-names them. Held on a stack:
  the attachment journeys of every engine path attach another person's file beside the person's own, and the answer
  names it once as not accessible while the job reads the person's own. Steering did not reach the worker at all for
  such a file: Node refused the whole message, 403 "The actor may not use this agent", where a new turn goes on and
  names the file. Node now queues a steered file the caller may not read as a new turn's is (a record the message
  names is still refused, with that reason), and the worker, which reads a steered file again with the caller's
  access, names it (`J-*-steer`). Left: an input artifact the job itself refuses (one over 100 MB, or gone) fails the
  whole job with the generic step line; naming it needs the refusal's words on `child.completed` (R36COVER-FU-3).
- **R44-SMALL (R36COVER-FU-3), an input the job refuses on its own.** Decided UX-first: the job goes on without the
  file and names it, rather than refusing up front. The parent already chose to hand the work over, the job's other
  inputs and its tools still serve the request, and a refusal before the start would discard all of it for one file
  the person can replace; failing whole gave only "The coding engine stopped with an error". The kernel resolves the
  inputs one artifact at a time (`resolve_each`): an artifact over the per-input cap, or no longer available, is
  skipped and pinned beside the others on the first `turn.started` (`skippedInputs`: artifact, reason, plain words,
  the name when the refusal may say it), so a re-driven turn says the same. The engine's first task names each one
  and asks it to say so (`skipped_note`); the job's hand-back carries them as `attachment.unreadable` limits, so the
  parent's model reads them in `couldNotDo`, the job step says "It could not read plan.csv (it is too large to give
  to a task: 140.0 MB, and the limit is 100.0 MB)" and the answer leads with the file's could-not-do line. A gone
  input is named only when it is the run's own conversation's (the person attached it); another's stays unnamed, so
  a refusal still reveals no artifact. A Node that does not name `skipped_inputs` keeps the old behaviour (the job
  fails), since its schema would refuse the field. Two input files on one name, or no input source, still fail.
- **R46FIX-2 (MR-16 on a lane), the skipped input follow-through.** Three faults showed when a lowered cap met two of the
  conversation's own files. (1) The kernel resolved a job's inputs under its run binding, whose scope is the job's own
  stream, not the conversation: every file of the conversation read as "unavailable" and none could be named. Inputs now
  resolve under the conversation's binding, as the job's published files already did (WP-70), so only the file over the
  cap is skipped and the other is delivered. (2) A file over the cap travelled as `attachment.unreadable`, so the
  answer said "its content could not be read"; it is now its own kind, `attachment.too_large` ("it is over the size
  limit for files given to a task"), sent only to a Node that names `attachment_too_large` (else unreadable, as
  before). (3) The step repeated its verb ("could not read one of the files…; read one of the files…"): each verb is
  said once and the last clause joins with "and"; files that may not be named are counted ("2 of the files handed to
  it"), and the answer line and the chat say "one of the attached files", never "(unnamed)". A file already saved over
  a lowered cap is skipped by the next save (`workspace_sync.skipped reason=file_too_large`) and kept unchanged, never
  deleted.

## Status

**Accepted** 2026-11-11: decided from first principles, long term, UX-first, answering the manual-test report.
