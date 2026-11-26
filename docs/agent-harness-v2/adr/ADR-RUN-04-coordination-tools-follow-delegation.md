# ADR-RUN-04: Only the run that delegates is offered the coordination tools

- Related: K-L (deep mode on the durable kernel, `backend/python/app/harness/assembly/deep.py`),
  [ADR-OWD-07](ADR-OWD-07-tool-identity-and-pinning.md) (R4: one tool list per chat session),
  `child_budget_share` (`backend/python/harness_core/journal/children.py`: a deep sub-agent spawns nothing).
- Follow-up of: QA-CLASSIC-FU-5 (CJFIX-B, `sub_agent_pool`), QAFINAL-FU-PW113 (ST-1 row PW-113).

## Context

Deep mode's coordinator plans and hands work to sub-agents with four tools: `create_plan`, `critique_plan`,
`verify_result` and `spawn_agent`. A sub-agent is a child run whose pin grants it no children (`maxChildren: 0`): the
tree is one level deep. Whether a run could delegate was decided in three places that did not agree:

- `sub_agent_pool` dropped the four tools from the pool a spawn with no `tools` argument is given (QA-CLASSIC-FU-5);
- the assembler registered them for the coordinator and for every sub-agent ("legacy's tree shares one registry");
- a sub-agent's tool list is the whole registry when its definition names no tools.

PW-113 met the gap: the coordinator's programmatic dispatch hands a plan step that names no tools over as
`tools: []`, so the sub-agent was pinned `toolNames: []`, read the whole registry and was offered the four tools. Its
model planned and called `spawn_agent`, which the sub-agent's port does not hold, and the session-list check told it the
tool was "removed after this chat started", which is not what happened. The lane's scripted model then took the call
as a coordinator would, so the row failed on whose model served the coordinator, not on the product.

## Decision

- **D1. One predicate.** `RunContext.coordinates` (`backend/python/app/harness/assembly/context.py`) says whether a run
  plans and delegates: a deep root. The assembler registers and routes the coordination tools, and builds the deep
  delegation and director, only for it. A sub-agent's registry never holds them, so no way of naming its tools (none,
  `[]`, a domain agent) can offer them. `COORDINATION_TOOLS` (`deep.py`) is the one list `sub_agent_pool` and the
  withheld set below read.
- **D2. A refusal says the true reason.** A sub-agent's tool port withholds the four tools with a reason
  (`RegistryToolPort(withheld=…)`, `withheld_from_sub_agent`): a call to one is told that a sub-agent cannot start
  sub-agents of its own and should do the task with its own tools. The kernel's session-list check asks the port for it
  (`WithheldTools`, `changed_since_pinned(withheld=…)`) before saying a tool was removed, so a list journaled before
  this change answers the same. A tool really removed after the chat started is still said so.
- **D3. Test fakes do not stand in for the product.** The lane's scripted model plans and delegates only for the
  coordinator: a request whose system prompt is a sub-agent's, or whose task carries the delegated-task header, is
  never answered by the coordinator rule (`deployment/e2e/fake_llm/server.py` `is_sub_agent`). Every ST-1 row checks that no sub-agent request offered a
  coordination tool (`integration-tests/harness/e2e/checks.py`, "a sub-agent is offered no coordination tools"), so a
  regression is reported as itself.

- **D4. A run that can start no sub-agent is not deep (QAFINAL-FU-PW113-2).** Deep mode is planning work and handing
  it to sub-agents. A deep-mode turn of an agent whose definition pins `maxChildren: 0` is pinned
  `budgets.maxChildren: 0`, so every spawn would be refused (`CHILDREN_NOT_GRANTED`). `RunContext.deep` therefore also
  needs `max_children > 0`; such a turn runs as the agent's own loop, with its own tools, and answers. The loop's
  existing `mode.downgraded` path turns its orchestrator into react. It is offered no coordination tool and no
  delegation is built, because `coordinates` is `deep`. It is not refused, because the person asked a question, and it
  does not run as `planExecute`, which is not durable. The agent builder says it under Sub-agent models: such an
  agent starts no sub-agents, so their models are not used, and a Deep turn is answered by the agent alone. Node's
  pin does not change.

### Amendment R36-COVER (2026-11-13, decided UX-first): the coordinator may ask

The durable coordinator held only the four coordination tools, and a sub-agent cannot ask the person (`TAG_UI_ONLY`,
`harness_core/tools/tags.py`; `may_ask` is false for a sub-agent, ADR-RUN-03). A sub-agent's need is handed up as an
`[ESCALATION]` line, which `orchestrator.py` says the root turns into `ask_user_question`, but the root had no such
tool: a deep run could never ask once it had started. Found by the journey runner (R31JOURNEY-FU-6, "the deep run
answered without asking"): the question probe on `agent.deep` ended with the answer and no card.

- **D1 amended.** The coordinator holds the coordination tools and, where its run offers it, the person's question
  (`COORDINATOR_QUESTION_TOOLS`, `orchestrator.py`; `PipesHubTurnShaper.tool_names`, `assembly/loop.py`). The run's
  own rules still decide whether it may ask (an unattended or answering run is not offered it); a sub-agent still is
  not. Asking ends the turn with `needs_input`, as every loop run's question does, and the person's answer resumes it.
  `session_tool_grant` treats the question as part of the coordinator's grant (no record tool is added).
- Classic chat's orchestrator is unchanged (it has no ask-back, X-CLASSIC).
- Tests: `test_parity.py` AS-1 (the coordinator's list) and the journey `J-agent.deep-seeded-question` (asked,
  answered, went on; on the base the run answered without asking). `question` on `loop.subagent` is impossible by
  design (X-SUBAGENT-ASK).

## As built

- R29-PW113 (`harness/v2-r29pw113`): `RunContext.coordinates`; the assembler's `_compose`, `_shape` and
  `_deep_and_steps` read it; `COORDINATION_TOOLS`, `not_for_sub_agent` and `withheld_from_sub_agent` in `deep.py`;
  `RegistryToolPort(withheld=…)` and `withheld_reason` (`backend/python/harness_core/tools/durable_port.py`); the
  `WithheldTools` facet (`harness_core/kernel/durable/ports.py`) read by `SessionToolLists.refusal`; the scripted
  model's `is_sub_agent`; the ST-1 check. Tests:
  `backend/python/tests/unit/harness/assembly/test_qafinal_pw113_sub_agent_tools.py` (PW113-1..8). ST-1 matrix 25 of
  25 (PW-113 failed on the base).

- QAFINAL-FU-PW113-2 (`harness/v2-r30small`): `RunContext.deep` reads the pinned `max_children`. The builder's
  `SubAgentModelsSection` takes `startsSubAgents` (the stored `maxChildren`, carried into the agent node's config).
  Tests: `backend/python/tests/unit/harness/assembly/test_qafinal_pw113_2_deep_without_children.py` (PW113-2-1..3;
  all three fail on the base); the deep fixtures pin what Node pins for a deep turn (`world.DEEP_CHILDREN`);
  `frontend/app/(main)/agents/agent-builder/__tests__/ma1-role-models.test.tsx` PW113-2-UI-1/2.

## Deviations

- Classic chat is unchanged: its `OrchestratorLoop` and `domain_spec_factory` are not on this path, and there a
  spawn whose `tools` is `[]` still reads the shared registry.
- D4 reads the one number Node pins. An agent that pins `maxChildren: 0` but has an engine authority is pinned
  `maxChildren: 1`, the `code_task` handoff's child. Its deep turn is still deep, and that one child may be a
  sub-agent or the handoff. The pin does not say which, and no ST-1 row or agent today has that combination.

## Owner amendments

None.

## Status

**Accepted** (2026-11-12, decided from first principles, UX-first).
