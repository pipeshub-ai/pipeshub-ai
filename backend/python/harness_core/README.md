# harness_core

Product-neutral agent harness core. It never imports `app.*` (import-linter
contract in `pyproject.toml`, plus `tests/unit/harness_core/test_import_boundary.py`)
and never emits AG-UI.

| Package | Contents |
|---|---|
| `contracts/` | Generated pydantic models for the harness contracts (P0.5) |
| `clock.py`, `ids.py`, `failpoints.py` | Clock, id and failpoint ports (P0.8) |
| `testing/` | Deterministic test kit, see `testing/README.md` |
| `bindings.py` | Seams the embedding application binds at composition time |
| `journal/` | Journal, checkpoint, content and live-bus ports; in-memory reference adapters (P1a S4b) |
| `effects/` | `EffectRunner`, the `EffectLog` port, `RequestEffectLog` and `JournalEffectLog`; tool effect value types (F1) |
| `kernel/` | The agent loop kernel moved from `app/agent_loop_lib` (P1a S4a) |
| `kernel/fold.py`, `kernel/durable/` | Journal fold and the durable kernel (P1a S4b), see below |
| `tools/` | Tool base class, registry, decorators, executor, special routes, `task_complete` |
| `sandbox/` | Sandbox broker ports (`SandboxBroker`, `SandboxHandle`, `ProcessHandle`, `ByteStream`), `SandboxSpec` and its value types, typed errors, `BrokerAuth`; `sandbox/remote/` is the `/v1` HTTP API (aiohttp server over any broker, client implementing the port) (P5.1) |
| `models/` | `Model` abstraction and native transports (Anthropic, OpenAI chat and Responses, Azure, Gemini, Ollama, Opik tracing) |
| `workspace/` | Workspace types, `WorkspaceStore` (CAS commit, fork, three-way merge, diff, rollback), object-store and key-provider ports, `SealedObjectStore` (one key per partition, crypto-shred) and in-memory adapters (P3.2); contract suites in `testing/contracts/workspace.py` |
| `engines/` | Engine types, events, ports, allowlist registry, tool policy (E5; ARCHITECTURE §4.1) |
| `sandbox/tar.py` | Tar streams for `put_tar`/`get_tar` (E5); `put_files` writes absolute paths into their top-level mounts (X1) |
| `interactions/` | `InteractionBridge` port (§4.5) |
| `access/` | `ModelApi`, `AccessRouteRef` (minimal until A1/E3) |

Installable on its own (`pyproject.toml` in this directory, distribution
`harness-core`); `tests/unit/harness_core/test_packaging.py` builds and imports
it without the app. Dependencies: the standard library and pydantic everywhere;
`workspace/sealing.py` needs `cryptography` (extra `sealing`) and `sandbox/remote`
needs `aiohttp` (extra `broker-http`); the transports also import their provider
SDK (`openai`, `httpx`, and lazily `anthropic`, `langchain_core`,
`google-genai`, `opik`).

## What moved from `app/agent_loop_lib` (P1a S4a)

| Old path (`app.agent_loop_lib.`) | New path (`harness_core.`) |
|---|---|
| `agent` (`Agent`, `step()`), `agent.{loops,tool_loop,hook_dispatch,observability,resume,prompt,streaming,spec,phase_driver,single_shot_runner,goal,intent,feasibility}` | `kernel.agent.*` |
| `roles.prompt_template` | `kernel.agent.prompt_template` |
| `core.*` (types, messages, scope, context, …) | `kernel.core.*` |
| `context.{base,manager,window}` | `kernel.context.*` |
| `events.base` | `kernel.events` |
| `hooks.{events,registry}`, `hooks.middleware` and its `{context,decisions,pipeline,routing,wrapper}` | `kernel.hooks.*` |
| `hooks.middleware.builtin.{turn_guards,stall_detection,budget_guard,truncation_recovery,supervisor_gate}` | `kernel.hooks.builtin.*` |
| `modules.pipeline.planner.base` | `kernel.planner` |
| `runtime.runtime` (`AgentRuntime`) | `kernel.runtime` |
| `modules.providers.budget.{base,pricing,tracker}` | `kernel.budget.*` |
| `modules.stores.*.base`, `modules.providers.{knowledge,memory}.base` | `kernel.ports.*` |
| `tools.{base,errors,registry,toolset,provider,decorators,summarizer,tags,invocation,executor,special_route}`, `tools.builtin.planning.task_complete` | `tools.*`, `tools.task_complete` |
| `models.{base,transport}`, `transport.*` | `models.{base,transport_model}`, `models.transports.*` |

Every old path is still importable. A moved module's old file replaces itself in
`sys.modules` with the moved module, so `app.agent_loop_lib.core.types is
harness_core.kernel.core.types` and patching either name patches both. The two
moved packages with content (`agent`, `hooks.middleware`) re-export the moved
package from their old `__init__.py` and keep the submodules that did not move.
`tests/unit/agent_loop_lib/test_legacy_aliases.py` checks every alias.

## What stayed, and why

- ControlPlane, `serve/`, CLI, `builder.py`, roles and role registry, commands,
  eval: standalone tooling around the loop, not the kernel.
- Stores and providers (`modules/stores/*`, memory, knowledge, skills,
  workspace): adapters. Only their abstract bases (the ports the kernel uses)
  moved.
- Builtin tools (`tools/builtin/*` except `task_complete`), `agent.spawn_scheduler`,
  `agent.best_of_n`, lazy toolsets, tool index, the remaining middleware
  (compaction, safety, skills, sandbox guards): they depend on sandboxes,
  skills, planners or PipesHub behaviour. The kernel reaches them only through
  `bindings` (spawn scheduling, special-route fallback).
- Legacy sandbox stack (`sandbox/`): replaced by the sandbox broker later (§6.8).
- `final_answer` and the skills validator: PipesHub product tools; they read
  their settings through `app/agent_loop_lib/composition.py`.

## Bindings

`app/agent_loop_lib/composition.py` installs `bindings.KernelBindings` when
`app.agent_loop_lib` is imported, so the legacy path keeps its behaviour:
invalid-tool-call repair (`app.agents.agent_loop.converters`), the learned
API-mode store, `final_answer` answer streaming, spawn scheduling, builtin
special routes and the AG-UI aliases (`app/agent_loop_lib/ag_ui.py`). Code
that imports only `harness_core` gets neutral behaviour. `AgentRuntime`
copies its per-run seams (`derive_events`, `streamed_answer_argument`,
`spawn_scheduler`) from the bindings when built; pass `None` to opt out.

## Typing debt

Moved legacy modules carry `# pyright: basic` and per-line
`# pyright: ignore[...]` for errors pyright already reported in
`app/agent_loop_lib`; new modules are strict. Ruff keeps the
`app/agent_loop_lib` relaxations for `kernel/`, `tools/` and `models/`.

## Durable kernel (P1a S4b)

`kernel/fold.py` folds a session's journal into `SessionState` and each run's
events into `RunState` (REPORT 6.1.6). Both are pure and total; an unknown
critical event raises `FoldIncompatible` and the caller releases its claim.
`RunState.cursor.next_action` is one of `call_model`, `dispatch_pending`,
`await_barrier`, `suspended`, `assemble`, `done`.

`kernel/durable/DurableKernel.run_claimed(claim)` resumes a claimed attempt from
the cursor, never from a turn counter, and commits by the rules in REPORT 6.1.4:

| What | Commit |
|---|---|
| user message, `turn.started`, `model.request`, `model.response` (+ `rawRef`), slot updates | one commit after sampling, before any dispatch |
| `tool.intent` for `write`/`execute` | its own commit before the effect; the outcome is its own commit after |
| `tool.intent` + `tool.outcome` for `read`/`workspace` | together, for the whole read batch |
| suspension (`interaction.requested` + `run.suspended`), yield, terminal | own commits; `run.*` is written by the journal (Node) |

Recovery of an intent without an outcome: `read` re-executes; an idempotent
write retries with the key `org:run:invocation`; anything else commits
`tool.unknown` and suspends for a human (`ix.unknown.<invocation>`; approve
retries, deny records an error outcome). A crash before `model.response`
re-samples; after it, the journaled response is used (rendered from `rawRef`
when the model port can restore it). Pre-run steps journal their result as
slot `pre_run.<key>` (with the presenter's `pre_run` events) and never run
twice. A `TurnShaper` builds each model call from the journaled state (system
prompt, tools, context shaping); a tool's slot updates ride with its outcome;
a terminal tool's answer completes the run in its outcome's commit; a
`ResponseReview` may continue a no-tool turn with an injected message (the
fold reads a completed no-tool turn as continued). Cancel, failure and quarantine give
every dangling tool call a synthetic outcome. The worker self-fences on the
suspend-aware clock before every effect and commit; `LeaseKeeper` renews
without the commit lock.

Ports: `JournalPort`, `ContentStorePort`, `CheckpointPort`, `LiveSinkPort`
(`journal/`), `DurableModelPort` and `DurableToolPort` (`kernel/durable/ports.py`).
`classify_tool_effect` derives `effect`/`idempotent` from the tool's reviewed
`effect` tag (`tools/tags.py`; fail closed: no read evidence means a
non-idempotent write) until P2 descriptors exist. Failpoints: `worker.model.{before,after}_response_commit`,
`worker.tool.{after_intent,after_effect,before_outcome_commit}`,
`worker.prerun.after_commit`, `worker.postrun.after_commit` (DUR2: a post-run step's `started` slot committed, the step not run), `worker.batch.before_suspend`,
`worker.commit.after_ack`, `worker.compaction.before_event` (P4.2: the summary is computed, its `context.compacted` not committed),
`worker.model.after_fallback_commit` (P4.7: `model.fallback` committed, the new route not yet called),
`worker.context.after_cap_commit` (P4.9: a smaller per-result cap committed after a too-large refusal, the request
not yet sent again).
