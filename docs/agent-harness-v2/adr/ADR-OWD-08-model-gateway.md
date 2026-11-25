# ADR-OWD-08 — Model traffic always goes through the Model Gateway

## Context

[OWD-8](../one-way-doors.md): harnesses never call model providers directly, even with a user's own key. If some did, metering, budgets, placement, audit and key custody would fork, and users would configure keys inside harnesses.

## Decision

Adopt OWD-8. A PipesHub Model Gateway is mandatory for engine model traffic. It serves the Anthropic Messages, OpenAI Responses and Chat Completions wires (and Gemini when a Gemini engine ships), passes reasoning, thinking and cache controls through faithfully, maps model aliases to the org's configured models, and holds BYO keys in PipesHub. Build, not adopt (the v2 architecture report D13; DP10-2).

## As built

Audited at `959c3bda72`. Decision rows: REPORT D13; [ARCHITECTURE.md](../ARCHITECTURE.md) §4.6.

- Process `backend/python/app/model_gateway_main.py` (port 8096, reached only through the egress proxy). Wires in `backend/python/harness_core/gateway/` (`messages_wire.py`, `responses_wire.py`, `chat_wire.py`, `model_gateway.py`).
- Engines ask for a neutral model id and the gateway maps it to the run's granted model. Vendor model names are refused, not mapped (stricter than the spec).
- Passing in the gate:
  - `backend/python/tests/unit/harness_core/gateway/test_messages_wire.py` (98), `test_responses_wire.py` (62), `test_chat_wire.py` (43), `test_model_gateway.py` (77) in the same folder
  - `backend/python/tests/unit/harness_core/models/test_provider_state.py::test_ps3_responses_replays_reasoning_before_the_turns_items_own_wire_only`
  - `backend/python/tests/unit/harness_core/gateway/test_model_gateway.py::test_mg10_vendor_keys_are_refused`

### The gateway ledger is the authority on usage (WP-16)

Every call the gateway settles tells the run's budget-ledger account the provider's own counts (input with cached tokens, output, cache read, cache write) beside its charge, before the call's last chunk reaches the engine. A turn's usage is the account's `used` read after the turn less the one read before it, whatever the engine reports: opencode over ACP reports only its last call (CF-13), and the ledger counts every call. The engine's own reports still drive the in-turn token limit; a report above the ledger is logged (`engine.turn.usage`, `usage_over_ledger`) as a call the gateway never saw. The grant auditor reconciles each ended run's journal usage with the ledger (C9.01). A run on its actor's own plan (the SIGNIN exception below) bypasses the gateway, so its usage stays the engine's report.

- Code: `backend/python/harness_core/engines/usage.py`, `harness_core/engines/controller.py` (`_ledger_used`), `harness_core/gateway/model_gateway.py` (`_chunks`, `_commit`), `harness_core/audit/usage.py`; Node `domain/budget-ledger.ts` (`BudgetUsage`, `used`).
- Tests: `backend/python/tests/unit/harness_core/engines/test_cf13_ledger_usage.py`, `tests/unit/harness/engines/test_real_acp_controller.py::test_rxacpc3_*` (real opencode over ACP, docker), `tests/unit/harness_core/gateway/test_model_gateway.py::test_ev10_*`, shared vector LV-14 in `contracts/harness/vectors/budget-ledger.json`.

## Deviations

1. **Dormant vendor-direct rows.** Signed release data `backend/python/harness_engines/vendor_rules.json` marks the `vendor_injected` routes `anthropic-api-key-*-injected` as `allowed`. No code path resolves `RouteKind.VENDOR_INJECTED` (`backend/python/harness_core/access/types.py`), so it was dormant, but it contradicted OWD-8. **Fixed in WP-06 SECRETS-RESIDUE** by a code floor: both loaders read every `vendor_injected` row as `prohibited`, whatever the signed document says (`GATEWAY_BYPASS_ROUTES` in `backend/python/harness_core/access/vendor_rules.py` and `backend/nodejs/apps/src/modules/agent-harness/domain/model-access.ts`; vectors VR-4, VR-5, VR-8, VR-12 in `contracts/harness/vectors/vendor-rules.json`). Editing the rows in the signed document waits for the owner's credential rotation and the re-signing with the new release key; until then the floor decides, and it stays as defence in depth afterwards.
2. **No Gemini wire.** Added when a Gemini engine ships (WP-13 ENGINE-REACH adds the Gemini CLI profile). Not a divergence until then.

## Owner amendments

- **2026-10-26, SIGNIN exception**. A run that uses an admin-enabled Claude sign-in sends its model traffic from the sandbox to Anthropic under the user's own plan, not through the gateway, and that usage is not metered by the gateway. Every other route goes through the gateway. See [ADR-OWD-17](ADR-OWD-17-vendor-subscription-logins.md).

## Status

**Accepted** (2026-10-26). Built and tested for the three wires. Since WP-16 the gateway ledger is the authority on a turn's usage (above). The vendor-direct rows are prohibited by the code floor (WP-06, `backend/python/tests/unit/harness/security/test_ga23_vendor_logins_refused.py`); the signed data is re-signed after the credential rotation. The Gemini wire follows a Gemini engine.
