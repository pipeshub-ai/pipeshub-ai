# Adding a harness adapter

Checklist item C18.07. A harness adapter connects one coding engine (opencode, Codex, Claude Code, an ACP agent) to
PipesHub's durable runs. This guide says what an adapter is, which of the two routes to take, and how the
conformance kit decides when it is done. The design is in [ARCHITECTURE.md](../ARCHITECTURE.md); the ACP driver's
design is ACP-driver.

## What an adapter is

An adapter implements `EngineAdapter` in `backend/python/harness_core/engines/ports.py`. It is stateless:

- `kind` names the engine. Native kinds are a closed set, `NativeEngineKind` in
  `backend/python/harness_core/engines/types.py`; an ACP agent's kind is `acp-<id>`.
- `capabilities()` returns `EngineCapabilities` (same file): whether it can steer or compact, its native skills and
  sub-agents, the model APIs it speaks (`model_apis`), the most tools it takes, and how it is driven (`stdio` or
  `port`). These are data the rest of the system reads: `release_model_apis()` in
  `backend/python/harness_engines/release.py` lets the model gateway serve an engine only the APIs its adapter
  declares, which is how "Codex speaks only the Responses API" is enforced (`CodexAdapter` in
  `backend/python/harness_engines/codex/adapter.py`).
- `render_launch(profile, endpoints, skills_dir)` is pure: the engine's argv, environment and files. It holds
  placeholders, never secrets; the egress proxy swaps a placeholder for the run's token on the way out.
  An engine with a step bound of its own renders `native_steps` (`backend/python/harness_core/engines/allowance.py`),
  two steps above the profile's, so the controller's limit and its writing reserve bind first; an engine that still
  stops at its own bound is cut short as a step limit, never a lost turn
  (`backend/python/tests/unit/harness_core/engines/test_wp70_allowance.py::test_wp70_ownlimit_an_engine_that_stops_at_its_own_step_bound_completes_cut_short`).
  Near any limit the controller steers the engine to write its deliverable when `capabilities()` says it can steer,
  and refuses its later gateway calls with the same words either way.
- `open(sandbox, launch, restore)` starts the engine in its sandbox and returns an `EngineSession`. It refuses saved
  state from another engine version or an unknown schema (`EngineStateIncompatible`) before starting anything, and
  verifies the engine binary's digest (`verify_binary` in `backend/python/harness_core/engines/binary.py`).

The session (`EngineSession`, same file) runs a turn as a stream of `EngineEvent`s from
`backend/python/harness_core/engines/events.py` ending in `TurnCompleted`, and offers `steer`, `interrupt`,
`compact`, `answer` (an approval or question), `capture_state` (never credentials), `health` and `close`. The
controller (`backend/python/harness_core/engines/controller.py`) writes the events to the journal; an adapter never
touches the journal, the gateways or the stores. Wrap-up reads the journal and needs nothing from the adapter.

Each adapter package has the same files: `adapter.py`, `launch.py`, `mapper.py` (native events to `EngineEvent`s),
`session.py` and `state.py`. See `backend/python/harness_engines/opencode/` and
`backend/python/harness_engines/claude_code/`. Adapters import `harness_core` only; the import-linter contracts in
`backend/python/pyproject.toml` hold that.

## Pick a route

**An ACP agent: data, not code.** If the engine speaks the Agent Client Protocol, it needs no adapter code. One
`AcpAdapter` serves every ACP agent from a profile in `backend/python/harness_engines/acp_agents.json` (parsed by
`backend/python/harness_engines/acp/profiles.py`). This is the route to take when you can.

**A native adapter: code.** An engine with its own protocol needs a package under
`backend/python/harness_engines/<engine>/`, a new member of `NativeEngineKind`, and its adapter in
`release_adapters()` in `backend/python/harness_engines/release.py`.

Both routes need:

1. **An allowlist entry** in `backend/python/harness_engines/allowlist.json`: `kind`, `version`, `binaryDigest`,
   `image`, and the asset with its sha256. `AllowlistEngineRegistry`
   (`backend/python/harness_core/engines/registry.py`) refuses any version, kind or digest not listed. The first
   entry of a kind is its default.
2. **An image** under `deployment/sandbox-images/<engine>/`, built by `deployment/sandbox-images/engine-build.sh`
   from the same allowlist entry, with `/pipeshub` laid out by
   `backend/python/harness_engines/pipeshub/install.sh` (see the [contract reference](pipeshub-contract-reference.md)).
3. **The catalogs**: `contracts/control-plane/catalog/engines.json` and the engine profile vectors in
   `contracts/harness/vectors/engine-profiles.json`, then regenerate the contracts
   (`contracts/harness/generate.sh`).
4. **The conformance suite**, below.

## The conformance kit

The kit is `EngineAdapterContract` in `backend/python/harness_core/testing/engines/contract.py`. Subclass it and give
it an `engine_harness` fixture that returns an `EngineConformanceHarness`: the adapter, its version, the names it
shows the model for native and gateway tools, a way to make a sandbox, and the secrets it handed out. Every case
then runs against your adapter. The module's docstring maps the spec's checks
([test-scenarios.md](../test-scenarios.md), CF-01..CF-20) to the built cases; the built
`cfN` numbers are not the spec's CF numbers.

| Spec | What it checks | Built cases |
|---|---|---|
| CF-01 | Capabilities are declared, exercised, and degrade cleanly | `test_cf1_exact_tool_list`, `test_cf7_steer` |
| CF-02 | Headless, with only injected config | `test_cf1_exact_tool_list`, `test_cf2_instructions_reach_the_model_once` |
| CF-03 | Injected MCP servers are connected | `test_cf13_max_tools` |
| CF-05 | Only the model gateway, with a placeholder token | `test_cf3_token_placeholder_only`, `test_cf12_no_credentials_in_saved_state` |
| CF-06 | Native events map to canonical ones | `test_cf6_redriven_turn_has_identical_command_ids`, plus the adapter's `test_mapper.py` goldens |
| CF-07, CF-08 | Tool round trip and the permission bridge | `test_cf4_approval_round_trip`, `test_cf5_long_wait_defers_and_the_recall_is_deduplicated` |
| CF-09 | Interrupt in 2 s, kill in 10 s | `test_spec_cf09_an_interrupt_ends_the_turn_within_2s`, `test_cf8_cancel` |
| CF-10 | Steer mid-turn | `test_cf7_steer`, `test_cf16_a_steered_slash_is_text_the_model_reads` |
| CF-11 | Resume after a restart | `test_cf9_resume_in_a_fresh_sandbox`, `test_cf10_refuses_state_from_another_version` |
| CF-13 | Usage equals the gateway ledger | `test_spec_cf13_the_gateway_ledger_holds_every_call_and_bounds_the_report` |
| CF-15 | Compaction keeps the mapping | `test_cf19_compact_summarises_a_restored_session_between_turns`, `test_cf20_the_engines_own_compaction_is_an_event` |
| CF-16 | A crash is a typed error | `test_cf14_health_and_close` |
| CF-19 | Sub-agent events are attributed | `test_cf11_sub_agent_limits` |
| CF-20 | Non-ASCII, RTL and emoji round-trip | `test_spec_cf20_non_ascii_rtl_and_emoji_round_trip_byte_exact` |

Not in the kit: CF-04 (skills and AGENTS.md) and CF-17 (auto-update and telemetry off) are checked by each
adapter's launch tests and the real-binary lanes; CF-12 (fork) and CF-14 (structured output) have no adapter that
declares them; CF-18 (the engine's inner sandbox off under gVisor or Kata) waits for a host with `runsc`, which is
deferred to the end of the project. The per-case status is in [test-scenarios.md](../test-scenarios.md).

A case the adapter does not declare a capability for is skipped, not failed (CF-01): an engine that cannot steer must
raise `SteeringUnsupported`, and `test_cf7_steer` checks that it does.

The kit has two layers:

- **Fast, no Docker.** The adapter against a recorded or scripted engine. FakeEngine runs it in
  `backend/python/tests/unit/harness_core/engines/test_fake_engine_conformance.py`; opencode in
  `backend/python/tests/unit/harness_engines/opencode/test_conformance.py`; the ACP driver in
  `backend/python/tests/unit/harness_engines/acp/test_conformance.py`.
- **The real binary**, marked `docker`: the real engine in its image, behind the real broker, egress proxy and
  gateways (`backend/python/tests/unit/harness/engines/real_stack.py`). One file per engine, for example
  `backend/python/tests/unit/harness/engines/test_real_codex_conformance.py`. These ran on runc only.

## Worked example: the smallest adapter

FakeEngine (`backend/python/harness_core/testing/engines/fake_engine.py`) is a complete adapter that runs a scripted
agent loop inside a fake sandbox and reaches the gateways through the sandbox's egress with the placeholder token.
It answers to the native kind `jcode`, because native kinds are a closed set. Its whole conformance suite is one
small harness and a subclass, from `test_fake_engine_conformance.py`:

```python
@dataclass
class FakeEngineHarness:
    adapter: FakeEngineAdapter
    ids: SeededIds = field(default_factory=SeededIds)

    @property
    def version(self) -> EngineVersion:
        return self.adapter.version

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        return frozenset({NATIVE_NAMES[tool]})

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset()

    def exposed_name(self, granted: str) -> str:
        return granted

    async def new_sandbox(self, egress: FakeEgress) -> FakeSandboxHandle:
        return await new_engine_sandbox(egress, ids=self.ids)

    def secrets(self) -> frozenset[str]:
        return frozenset()


class TestFakeEngineConformance(EngineAdapterContract):
    @pytest.fixture
    def engine_harness(self) -> EngineConformanceHarness:
        return FakeEngineHarness(FakeEngineAdapter())
```

A second subclass, `TestFakeEngineWithoutSteeringOrWithAToolLimit`, runs the same cases with steering off and a
40-tool limit, which is how the skips and the tool limit are tested. Copy this harness for a new adapter and
replace the four engine-specific answers: the adapter, the names the engine shows the model, its sandbox, and the
secrets it hands its processes.

### A real one: opencode as an ACP agent

The first ACP profile (commit `dcc603da3c`, ACP-2) added opencode's ACP server as an engine with no new binary or
image. It added:

1. The profile in `backend/python/harness_engines/acp_agents.json`: argv, model API, gateway environment, native tool
   names, the MCP server declaration, resume and file-system settings (the fields are listed in ACP-driver,
   "Registry, not code per agent").
2. The matching `acp-opencode` entry in `backend/python/harness_engines/allowlist.json`; `installed_acp` in
   `backend/python/harness_engines/release.py` pairs the two.
3. The catalog rows (`contracts/control-plane/catalog/engines.json`, `contracts/harness/vectors/engine-profiles.json`)
   and regenerated contracts.
4. Tests: the ACP conformance subclass, the profile tests in
   `backend/python/tests/unit/harness_engines/acp/test_profiles.py`
   (`test_every_acp_engine_in_the_allowlist_has_its_profile_and_back`), and the release registry tests in
   `backend/python/tests/unit/harness_engines/test_release_registry.py`.

That commit also carried the one-time ACP plumbing, so a second agent touches less. Gemini CLI (commit `531acce669`,
WP-13) is the example of an agent that needed more: its own image under `deployment/sandbox-images/acp-gemini/`, a
new model API (`backend/python/harness_core/gateway/gemini_wire.py`) and a real-binary lane,
`backend/python/tests/unit/harness/engines/test_real_gemini_acp_conformance.py`.

`acp_agents.json` is never signed in the tree. Unsigned, its agents install only with
`PIPESHUB_ACP_PROFILES_UNSIGNED=true`, the development setting (`backend/python/harness_engines/profile_trust.py`); a
release build signs it with `scripts/engines/sign_vendor_rules.py --acp-out`, and an e2e stack at the production floor
(`E2E_SANDBOX_RUNTIME=runsc`) signs it with its own test root (ADR-SEC-01 D7).

## Running the kit

From `backend/python`:

```bash
pytest tests/unit/harness_core/engines/test_fake_engine_conformance.py \
       tests/unit/harness_engines/opencode/test_conformance.py \
       tests/unit/harness_engines/acp/test_conformance.py
pytest tests/unit/harness_engines/acp/test_profiles.py tests/unit/harness_engines/test_release_registry.py
pytest tests/unit/harness_engines/acp/test_conformance.py -k cf9          # one case
pytest -m docker tests/unit/harness/engines/test_real_conformance.py      # the real binary; needs Docker and the image
```

An adapter is done when its fast subclass and its real-binary lane pass, its mapper has golden fixtures, and the
release registry test accepts its allowlist entry. Review it against [checklist.md](../checklist.md) §13
(Compatibility) and §14 (Upgrades and versioning).
