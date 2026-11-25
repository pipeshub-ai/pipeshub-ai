# The research-run scenario lane (ADR-RUN-01 D7)

A deterministic replay of the Onyx research task's shape (runs 5 to 8), played by the scripted model, so a harness defect in a long research run shows up in the round gate instead of in a paid run. Scenario IDs are `RUN01-SC-*` in [test-scenarios.md](../test-scenarios.md).

## What it runs

One definition, `backend/python/tests/support/research_run.py`:

- two repositories of 10,000 files each (one fixed commit each, the same on every host);
- 150 public reads (`fetch_url`, `web_search`), half before the clones and half after;
- a report of about 140 KB, appended to `out/report.md` in seven steps;
- model steps on a virtual clock (8 s while gathering, 210 s for each append, 318 s for the last step), so the 30-minute working time cuts the last step in the middle;
- separate runs for a refused save, a failed engine child, a harness steer leaked into the parent's brief, and a child that gathers nothing.

Two lanes run it:

| Lane | File | Marker | What is real |
|---|---|---|---|
| In process | `tests/unit/harness/engines/test_run01_research_scenario.py` (rig: `research_rig.py`) | none: the unit set | The loop parent (`PipesHubRuntimeAssembler`), the durable kernels, `EngineController`, the production `repository_fetch` over a local https git host, the production approval composition. The engine is `FakeEngine` in the fake sandbox. Repositories have 1,000 files each, because every save before a gateway call scans the sandbox. |
| Docker | `tests/unit/harness/engines/test_run01_research_scenario_docker.py` | `docker` | Node's journal server and workspace store (with the 20,000-file cap), the egress proxy, the tool gateway, and the release engine image (opencode, or `E1_ENGINE=codex`) under the Docker broker. Full size. The clones are production's (WP94-FU-2): the engine runs `ph git clone`, the worker's `repository_fetch` fetches from a local https git host (`git_fixture`, dialled on loopback), a person approves each card, the copy is handed onto the turn's shelf and `ph` checks it out. The worker's source ledger, repository asks and provenance stamp are production's, so SC-01, SC-02a/b, SC-03a/b/c and SC-08b run here (`WorkerConfig.git_host`). |

The working-time cut, the refused save, the failed child and the leaked steer run only in the in-process lane, because they need a virtual clock, a store refusal or a parent loop.

## Running it

```bash
cd backend/python
pytest tests/unit/harness/engines/test_run01_research_scenario.py            # about 25 s
pytest -m docker tests/unit/harness/engines/test_run01_research_scenario_docker.py   # one lane at a time
```

The round gate picks both up: the unit set runs `-m "not docker and not slow" tests/unit`, and the Docker lane runs `-m "docker and not slow" tests/unit`.

## Red baseline

A case today's code fails is a strict `xfail` whose reason names the ADR-RUN-01 decision that fixes it (`ADR-RUN-01 D1`, `D3`, `D4`, `D5`, `D6`). When that package lands, the case fails as XPASS. Remove its marker in the same commit, and tick its row in test-scenarios.md.

All cases pass on the built D1-D6: `SC-02b` went green with WP-83, `SC-07` with WP-93 (D5), `SC-04c`, `SC-06b` and `SC-08b` with WP-95 (D1), `SC-03c` with WP-96 (D3), `SC-04b` and `SC-09b` with WP-99 (D4), and `SC-05b` with WP-94b's fix (a save refused mid-turn is a `SaveRefused` fact). A new red case follows the same rule: strict `xfail` naming its decision. Assert on the settled outcome, the ledgers and the stores, never on English in the answer: the chat words the outcome (WP-89).

The outcome cases read WP-95's events (`x.pipeshub.outcome.recorded` facts, `x.pipeshub.outcome.settled`) through `outcome_facts` in `research_run.py`, and the provenance case reads the session's `ProvenanceLedger` from the engine's events.
