# harness_core.testing

Deterministic doubles for harness tests. Nothing here touches the network, the
real clock or random ids unless you ask it to. `harness_core` never imports
`app.*`; product adapters for these doubles live in `app/harness/testing_adapters.py`.

| Module | What it gives you |
|---|---|
| `scripted_model.py`, `model_script.py`, `model_types.py` | `ScriptedModel` + YAML script DSL |
| `fake_saas.py` | `FakeSaaS`: effect recorder with idempotency keys, lost acks, rate limits, lookup lag; `log_path` keeps effects across a SIGKILL |
| `clock.py` | `ManualClock` (implements `harness_core.clock.Clock`) |
| `virtual_time.py` | `VirtualTimeLoop`, `run_on_virtual_time`: an asyncio loop whose clock moves only by the deadlines the code waits on, for timing a bound without the host's scheduler in the reading (CF-09) |
| `ids.py` | `SeededIds` (implements `harness_core.ids.IdSource`) |
| `failpoints.py` | `armed(...)`, `write_arms(...)` for `harness_core.failpoints` |
| `replay/` | record a run, scrub it, replay it with frozen model + tool outcomes, `harness-diff` |
| `durable.py` | `ScriptedModelPort`, `FunctionToolPort`: the durable kernel's model and tool ports over the doubles above |
| `contracts/journal.py` | `JournalContract`: the suite every `JournalPort` adapter runs (in-memory here, Node-backed in S5) |
| `run_spec.py` | `minimal_run_spec()` |
| `sandbox/` | `FakeSandboxBroker` (in-memory `SandboxBroker`: programs as coroutines, per-sandbox `install`, read-only root, loopback `connect` and handler-style `listen`, request-level `FakeEgress`) and `SandboxBrokerContract`, the suite every broker adapter runs (P5.1; engine use since X1) |
| `engines/` | `FakeEngine`, `ConformanceGateway` + `FakeInteractionBridge`, `EngineAdapterContract` and `EngineRegistryContract`, `new_engine_sandbox` (an engine-shaped sandbox on `FakeSandboxBroker`) (E5) |
| `contracts/interactions.py` | `InteractionBridgeContract` |
| `cassette.py` | `CassetteTransport`: the VCR gateway (G-49). `record(inner)` keeps every call a provider transport answers, stream events included; `replay(cassette)` answers from the cassette and refuses a call that differs (`CassetteMismatch`). Cassettes say whether a scripted model or a provider was recorded; `save` refuses one holding a credential |
| `engines/gateway_model.py` | `GatewayModelEndpoint`: an `EngineWorld`'s model served by the real `ModelGateway` (token, grant, budget, metering) over any provider transport; `BrainTransport`: the scripted brain as a provider transport, what engine cassettes are recorded from |

Node equivalents (failpoints, manual clock, scripted AG-UI upstream) are in
`backend/nodejs/apps/tests/support/harness/`.

## A scripted model test

```yaml
# tests/.../fixtures/approval_jira_create.yaml
id: approval-jira-create
turns:
  - expect: { last_user_contains: "create a P1 bug" }   # guard -> ScriptMismatch
    reply:
      tool_calls: [{ id: toolu_01, name: jira_create_issue, args: { summary: "Login 500" } }]
      usage: { input_tokens: 120, output_tokens: 14, cache_read_tokens: 100 }
  - expect: { tool_result_for: jira_create_issue, status: succeeded }
    reply: { text: "Created PROJ-1.", chunks: 4 }
  - reply: { error: { kind: rate_limited, retry_after_s: 2 } }       # 429
  - reply: { text: "partial", error: { kind: server_error, status: 503 } }  # cut mid-stream
```

```python
model = ScriptedModel.from_yaml(FIXTURE, sleep=clock.sleep)  # delay_ms follows ManualClock
response = await model.complete(ModelRequest((RequestMessage("user", "create a P1 bug"),)))
model.assert_exhausted()
```

Reply fields: `text`, `chunks` (count or explicit list), `reasoning`, `tool_calls`
(`id` defaults to `call_<turn>_<i>`), `error` (`server_error | rate_limited |
content_filter | timeout | malformed`, optional `status`, `retry_after_s`),
`delay_ms`, `usage`, `stop_reason`. Unknown keys are rejected. In Python a turn
may use `Turn(from_request=fn)` to compute its reply from the request. An extra
call raises `ScriptExhausted`.

To drive the legacy `agent_loop_lib` agent with the same script, wrap it:
`ScriptedModelTransport(model)` from `app.harness.testing_adapters`.

## Arming a failpoint

Call sites are free when nothing is armed:

```python
from harness_core.failpoints import failpoint, afailpoint
failpoint("worker.tool.after_intent")          # sync; delay blocks
await afailpoint("worker.tool.after_intent")   # async; delay yields
```

In-process (the block disarms on exit):

```python
from harness_core.failpoints import Arm, Delay, Kill9, Nth, Raise
from harness_core.testing import armed

with armed({"worker.tool.after_intent": Raise()}):           # fires once
    ...
with armed({"worker.tool.after_intent": Arm(Raise(), Nth(3))}):
    ...
```

Cross-process: write a file and have the child install it (there is no env
var; the test passes the path, the compose overlay will mount it):

```python
write_arms(failpoint_file, {"worker.tool.after_intent": Arm(Kill9(), Nth(2))})
# child: registry.install_file(Path(sys.argv[1]))
```

`Kill9` is a real `SIGKILL`: no `finally`, no flush. See
`tests/unit/harness_core/test_failpoint_kill9.py`.

## Recording and replaying a run (P0.4)

`replay/` records one run's model calls, tool executions, external effects and
final answer into a JSON `Recording`, scrubs it, and turns it back into doubles:

```python
recorder = RunRecorder(scrub=scrubber.text)          # digests are taken over scrubbed text
model = RecordingModel(ScriptedModel(script), recorder)
# ... drive the harness; the tool boundary calls recorder.tool(...) per execution
recording = scrub_recording(recorder.finish(Manifest(scenario="x")), scrubber=scrubber)

replay_model = ScriptedModel(recording_to_script(recording))   # recorded responses
frozen = FrozenTools(recording)                                 # recorded tool outcomes
report = diff_recordings(recording, replayed, compare_effects=False)
print(report.render())   # "First divergence at step 4: status differs ..."
```

`python -m harness_core.testing.replay diff a.json b.json` prints the same report
(exit 1 on divergence); `... scrub in.json out.json --canary TEXT` scrubs a file
and refuses to write one that still carries a secret.

The scrubber redacts JWTs, provider API keys (OpenAI, Anthropic, Slack, GitHub,
GitLab, AWS, Google, PipesHub PATs), bearer tokens, private keys, URL userinfo,
credential query parameters and `password=`-style pairs, and replaces emails
with HMAC pseudonyms (stable within a recording, order-independent).
Diffs ignore call ids and mask UUIDs.

The legacy-route driver, scenarios and committed corpus live in
`tests/harness_replay/` (`python -m tests.harness_replay.record --all [--check]`);
`app.harness.testing_adapters.tool_boundary` records at `ToolExecutor` and serves
frozen outcomes, `RecordingTransport` records a real model.

## The GA-26 offline journeys (G-49)

`tests/harness_replay/ga26/` runs each P0 journey on the mock harness (the durable loop, or `FakeEngine` under the
engine kernel), with the model behind a `CassetteTransport` (through `ModelGateway` for engines) and the data from the
seeded tenant (`tests/fixtures/tenant/atlas.yaml`, `tests/support/seeded_tenant.py`: Slack, Jira, Drive and
Confluence records with varied access, also loaded into real Neo4j and ArangoDB by the store lane). Cassettes are
recorded from scripted models only:

```bash
python -m tests.harness_replay.ga26 --record     # or --check
pytest -m ga26 tests/harness_replay              # also in the default run, which re-records and compares every cassette
```

