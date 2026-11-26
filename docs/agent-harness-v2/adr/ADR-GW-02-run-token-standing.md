# ADR-GW-02: One rule decides what a run's token may still do on its run

- Extends: ADR-OWD-08 (model gateway), E2/E3 (gateway tokens, the model grant), P6.5 (the budget ledger).
- Related: ADR-SEC-02 (a run token binds its run's org), JRN-22 (nothing journals after a run's terminal event).
- Found by: SEC-C-11.

## Context

The internal router has eleven `run-token` routes: the model grant, five gateway reports (`/refusals`, `/egress`,
`/egress-volume`, `/model-fallback`, `/secret-hidden`) and five budget routes. Each route's service checked the
token in its own way:

| Route | What it checked before |
|---|---|
| `GET /model-grant` | Attempt, fence, lease and status (`leasedPin`) |
| `POST /budget/reservations` | The same, as a separate copy (`leased`) |
| The five reports | Signature, run id and that the run exists. Any attempt, any time within the token's 10 minutes |
| `GET /budget` | Signature and run id only |
| Budget settlements | Signature and run id. A token past its expiry was accepted within the longest reservation TTL |

A superseded attempt, or one whose run had ended, could therefore append journal and audit records to the run.
After the end, those records broke the journal's own rule (JRN-22: nothing after the terminal event).

## Decision

**D1. One rule.** `domain/run-token-standing.ts` `runTokenRefusal(run, holder, use, now)` decides, from Node's record
of the run and the token's own binding (its attempt id and fence), whether the token may do `use` now. Every
`run-token` route goes through it: `GatewayTokenService.asRunToken`, which `modelGrant` and every report use, and
`BudgetLedgerService.standing`. There are no per-route copies. The worker-credential route that issues a token
(`POST /gateway-token`) uses the same rule at the run's fence.

**D2. Three uses.**

- `act` (model grant, budget reservation): the token's attempt holds the lease of a `running` run.
- `report` (the five gateway reports, `GET /budget`): the token's attempt holds the lease while the run is `running`
  or `cancelling`.
- `settle` (budget commit, reconcile, release): any attempt of this run. The reservation was made under a live
  lease, and the spend happened. Refusing a superseded attempt's settlement would lose real spend, and an
  unreleased hold would starve the new attempt for up to an hour (`MAX_RESERVATION_TTL_MS`).

A token from another fence is refused as superseded, since every claim raises the fence. Every refusal answers 409
`RUN_NOT_LEASED`, so the gateway stops reporting for that attempt (Python `NodeRefusalSink` treats 409 as not
accepted, not as something to retry). The log line names the reason (`attempt_superseded` or `run_not_leased`).

**D3. No grace after the run lets go.** We considered a short window in which the attempt that ended the run could
still report, so that a report landing just after the terminal commit is not lost. It is rejected: JRN-22 forbids any
journal event after the terminal event, so such a report could only corrupt the journal. A suspended or yielded run
is treated the same way: its next claim is a new attempt. A report the gateway could not record stays in the
gateway's own log.

**D4. The attempt comes from the token only.** A report's body schema is strict and names no attempt or fence; a
body that does is refused (400). The journal records `claims.attemptId`. The budget routes' optional
`attemptId`/`fence` echo is never read.

**D5. A reservation belongs to the attempt that made it (SEC-C-20, amendment 2026-11-13).** `settle` lets any attempt
of the run through the standing rule, so on its own it let a superseded attempt's token settle, release or reconcile
a *later* attempt's reservation by naming its key. Each reservation now records the reserving attempt's fence
(`BudgetReservation.fence`, set from the verified token, never the body). Every settlement passes the caller's fence
and the store refuses one from another fence as `BUDGET_RESERVATION_MISSING`, inside the same transaction that would
settle it, through one domain check (`assertReservationOf`) that the in-memory, Mongo and PG stores call. The same
key reserved again by another attempt is refused the same way, so a superseded attempt cannot take over a live
attempt's key either. The answer reads as a missing reservation, so a caller learns nothing about another attempt's
keys. The superseded attempt still settles its own reservations, so spend that happened is never lost; a
reservation it never settles lapses at its own TTL. A hold copies its commit's fence. A reservation written before
this binding has no fence and is settled by any attempt of its run until it lapses, at most an hour
(`MAX_RESERVATION_TTL_MS`) after the upgrade. Durable ids are attempt-unique (`_Ids`), so a later attempt never
needs an earlier attempt's key.

## As built

- `domain/run-token-standing.ts` (`runTokenRefusal`, uses act / report / settle), `GatewayTokenService.asRunToken`, `BudgetLedgerService.standing`; Python `app/harness/access/refusals.py` reads 409 as not accepted.
- `tests/modules/agent-harness/sec-c11-run-token-standing.test.ts` reads the `run-token` routes from the router.
  A new route fails there until it has a sample request, or is listed as a settlement. The test asserts, on each
  route, that the following are refused: a superseded attempt, a lapsed lease, the attempt that ended the run, and
  another run's token. Settlements are asserted to be accepted for a superseded attempt.
- D5: `domain/budget-ledger.ts` `assertReservationOf`; the fence is threaded through `BudgetLedgerStore.reserve`,
  `commit`, `reconcile` and `release` and stored on the reservation (Mongo schema field `fence`; PG keeps it in the
  JSON document, so no migration). Tests: the store contract (`support/budget-ledger-contract.ts`, run on in-memory,
  Mongo and PG) `SEC-C-20: …`, and the HTTP routes in `sec-c11-run-token-standing.test.ts` `SEC-C-20: …`: each
  settling route refused across fences in both directions, with the key percent-encoded, a body naming another
  fence, and a commit replayed by another attempt.

## Deviations

- Closed (SEC-C-20, D5): a superseded attempt's gateway token could settle, release or reconcile any of its run's
  reservations by key. Each reservation is now bound to the fence that made it.
- A reservation written before D5 carries no fence and stays settleable by any attempt of its run until it lapses
  (at most an hour after the upgrade). Refusing it would lose real spend, and it closes itself.

## Owner amendments

None. Decided by R38-AUTHZ (2026-11-13). D5 was added by R42-SMALL (2026-11-13), closing SEC-C-20.

## Status

**Accepted** (2026-11-13), R38-AUTHZ, closing SEC-C-11.
