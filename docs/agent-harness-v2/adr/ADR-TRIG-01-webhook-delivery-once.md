# ADR-TRIG-01: A signed webhook delivery is accepted once

- Fixes SEC-C-13 (R38-IDENT, 2026-11-13).
- Amends: TR-04 (C2.56), the signed webhook trigger.
- Related: SEC-C-13.

## Context

A signed webhook proves the sender holds the schedule's secret by an HMAC of `<timestamp>.<raw body>`. The
timestamp had to be within 300 s of Node's clock, and nothing else was remembered. Deliveries inside one coalescing
window are one firing, but a window can be as short as 60 s. So a request captured in transit or from a proxy log
fired again in each new window until the 300 s ran out: up to five extra runs on the schedule owner's budget.

## Decision

1. **Each signed delivery is accepted once.** After the signature, body cap and rate checks, Node records the
   delivery's nonce with `ScheduleStore.firstDelivery` and fires only when it is the first. The record is shared by
   every replica: the same TTL'd counter collection the rate limit uses (`agentScheduleCounters` on MongoDB,
   `harness.schedule_counters` on PostgreSQL), keyed `seen:<org>:<schedule>:<nonce>`, kept for twice the skew
   window. Past that the timestamp is refused anyway.
2. **The nonce is the digest of the signature the server computes,** not of the header the caller sent. The header
   may carry several comma-separated `v1=` values (a secret being rotated), so padding, reordering or adding values
   would otherwise make a captured request look new. The HMAC covers the timestamp's text, so a re-encoded
   timestamp no longer verifies.
3. **A repeat answers `202 {"firing": null, "ignored": "duplicate"}`,** not an error. A sender that retries after a
   lost response is told its delivery arrived, and does not retry forever. Nothing starts, and
   `schedule.webhook_replayed` is logged.
4. **The skew window is 120 s** (was 300 s). It bounds how long a captured request is worth anything and how long
   the nonce is kept. Senders on NTP are well inside it.
5. **The nonce is recorded after the rate check.** A delivery refused with 429 can be retried as is. A replay
   still counts against the per-minute rate, but it can no longer start a run.
6. **Token webhooks (`auth: token`) are unchanged.** Their caller signs nothing that tells two deliveries apart, so
   there is no nonce. They exist only for callers that cannot sign; every new webhook is signed.

## As built

- `ScheduleStore.firstDelivery` (`backend/nodejs/apps/src/modules/agent-harness/ports/schedules.ts`), on memory, MongoDB and PostgreSQL; called from `ScheduleService.webhook` after the rate check.
- `WEBHOOK_SIGNATURE_TOLERANCE_S` = 120 (`backend/nodejs/apps/src/modules/agent-harness/domain/schedules.ts`); the OpenAPI entry says 2 minutes and documents `ignored: duplicate`.

## Deviations

Considered and rejected:


- **A caller-sent delivery id** (`X-PipesHub-Delivery-Id`). The signature does not cover it, so an attacker would
  change it to make a replay new. Rejected.
- **Refusing a repeat with 409.** Safe, but a sender's ordinary retry would then look like a failure. Rejected for
  the UX reason in 3.

## Owner amendments

None.

## Status

**Accepted** (R38-IDENT, 2026-11-13).

Consequences:

- The same timestamp and body sent twice within one second is one delivery. That is the idempotency senders expect.
- Tests: `sec-c-platform-appsec.test.ts` SEC-C-13 (seven cases: replay in the next window, signature-header
  variants, re-encoded timestamp, concurrent replays, past the window, a new delivery of the same body, paused then
  resumed); store contract ST-15 and the race ST-16 on memory, MongoDB and PostgreSQL; `schedule-routes.http.test.ts`
  RT-6 (the HTTP 202 `duplicate` answer).
