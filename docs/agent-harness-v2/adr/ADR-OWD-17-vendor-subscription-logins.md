# ADR-OWD-17 — Vendor subscription logins inside harnesses

## Context

[OWD-17](../one-way-doors.md): may users authenticate harnesses with personal vendor subscriptions (Claude Max/Pro, ChatGPT plans)? Supporting it puts long-lived vendor tokens in sandboxes (against OWD-1), bypasses gateway metering and placement (against OWD-8), and raises terms-of-service questions. Once users rely on it, removal is a visible regression. The spec said: not in v1, and images disable login flows.

## Decision

As amended on 2026-10-26:

- **One narrow exception is allowed:** Claude Code's own, unmodified login, run inside the user's own sandbox. PipesHub never collects, stores or relays the token.
- **Limits:** admin-gated and off by default; work plans only, with consumer Pro/Max a separate opt-in; only the signed-in user's own interactive turns. Extra controls in [ARCHITECTURE.md](../ARCHITECTURE.md) §4.6.4: a read-only tool grant by default, token-pattern refusal at the gateways, owner-only binding, a sign-in that ends when the conversation is shared.
- **The admin default stays off** until Anthropic answers the drafted terms questions (SIGNIN vendor questions).
- **Every other vendor subscription login is blocked** (ChatGPT/Codex, Gemini, others). Conformance test GA-23 checks that they are refused.
- OWD-1 and OWD-8 record the matching exceptions ([ADR-OWD-01](ADR-OWD-01-secrets-outside-sandbox.md), [ADR-OWD-08](ADR-OWD-08-model-gateway.md)).

## As built

Audited at `959c3bda72`. Decision rows: SIGNIN (vendor sign-in); the implementation plan D30 (vendor rules as signed release data); SIGNIN.

- `backend/python/harness_engines/claude_code/` sign-in; `backend/nodejs/apps/src/modules/agent-harness/application/engine-signin-service.ts`; vendor rules in `backend/python/harness_engines/vendor_rules.json`.
- Passing in the gate:
  - `backend/python/tests/unit/harness_engines/claude_code/test_signin.py` (54)
  - `backend/python/tests/unit/harness/engines/test_signin_relay.py` (47)
  - `backend/nodejs/apps/tests/modules/agent-harness/engine-signin.test.ts` "DC-4: a conversation shared ends the sign-in held in it"
- Failed for the host's Docker address-pool exhaustion, not the code: `test_signin_adapter_live` sl1-3 and `test_signin_real_binary` sp1-7 (docker.xml).

## Deviations

None against the amended decision. GA-23 landed in WP-06 SECRETS-RESIDUE: `backend/python/tests/unit/harness/security/test_ga23_vendor_logins_refused.py` and `backend/nodejs/apps/tests/modules/agent-harness/ga23-vendor-logins.test.ts` check that every ChatGPT/Codex, Gemini and other plan login is refused by the signed rules even for an org that opted into everything, that only Claude Code's adapter can sign in, that the signed sign-in hosts are Claude's only, that an engine sandbox reaches no vendor login host, and that Claude's own login is off until the admin opts in. Left: the docker sign-in lanes once Docker has free network pools.

## Owner amendments

- **2026-10-26** (owner; SIGNIN of 2026-10-18): amended as above. Recorded in [one-way-doors.md](../one-way-doors.md) under OWD-17.

## Status

**Accepted as amended** (2026-10-26). Built and unit-tested; off by default. GA-23 passes (WP-06).
