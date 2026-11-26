# ADR-OPS-03: Security decisions are counted where they are made

- Finding: SEC-D-13 (nothing detects automated attacks, and stolen keys have no runbook).
- Related: ADR-OPS-01 (operator controls: the kill switch this counts), ADR-CRED-01 (machine credential roles), ADR-KEY-01 and ADR-KEY-02 (what a leaked `SECRET_KEY` opens), C9.04/C9.05 (the merged metrics route and its alerts).

## Context

The harness's metrics were built for reliability SLOs (C9.04, C20.48): availability, leases, cost, the cache. Its security decisions were log lines only: a refused sign-in, a forged attempt token (`authority.refused`), an egress refusal (`egress.refused`), an approval asked, an operator stop. A log line tells you about one event after you look; it never says "this is happening a hundred times a minute". So the attacks that work by volume stayed invisible: credential stuffing across many accounts stays under each account's lock, an injected agent probes the allowlist one refusal at a time, an approval-spam run waits for a tired "Allow all".

The fault is shared: a security decision had nowhere to be counted, so each new check would have logged and nothing more.

## Decision

1. **Count at the decision, through one helper per language.** Each place that accepts or refuses calls one function, which maps its labels onto a fixed set and never throws (a metrics fault must not turn a refusal into an acceptance or a 500):
   - Node `recordAuthAttempt(method, outcome)` (`libs/services/telemetry/modules/auth-metrics.ts`): the sign-in step (`UserAccountController.authenticate`, every method, success or refusal) and the API bearer check (`AuthMiddleware.authenticate`: session JWT, PAT, service token, OAuth token). A request with no credential is not an attempt; an error after acceptance is not a refusal.
   - Node `countTokenRefusals` (`transport/internal-auth.ts`), the internal listener's one error handler before the error middleware: every refusal reaches the caller through `harnessUnauthorized` and `next(error)`, so counting where it leaves counts every guard, controller and service once, including refusals added later. The kind is the matched route's guard (C-26); the reason travels on a symbol on the 401, so the answer is unchanged.
   - Python `harness_core.telemetry.security_signals` (`token_rejected`, `machine_credential_refused`, `egress_decided`), with a sink the process installs (`app.telemetry.modules.security_metrics`, imported by the egress, gateway and broker metrics modules). Called by the egress proxy's `record_refusal` (its one refusal path) and `_report_allowed`, the egress control port's and the broker's auth middleware, `MetricsExposition.answer`, and the gateways' metrics adapter for a refused gateway token.
   - The approvals a commit asks (`interaction.requested`), counted by `ExecutionService.commitCommand` after the store commit and never on a replay.
   - The operator controls in force, read at scrape time from `OperatorControlService.controls()` (which includes `AGENT_HARNESS_KILL_SWITCH`) by `HarnessLocalMetrics`.
2. **Labels are fixed sets, never identities.**

   | Series | Labels |
   |---|---|
   | `pipeshub_auth_attempts_total` | `outcome` success, failure (a 4xx refusal), error (the check failed); `method` password, otp, google, microsoft, azure_ad, oauth, github, saml, session, pat, service_token, oauth_token, other, and the sign-in methods an edition adds when it loads (`addAuthAttemptMethods`: a lowercase name, checked once) |
   | `pipeshub_agent_harness_token_rejected_total` | `kind` machine_credential, attempt_token, gateway_token, other; `reason` missing, malformed, algorithm, unknown_key, signature, issuer, audience, expired, claims, superseded, credential, other. Node's `HarnessUnauthorizedReason` and the gateways' `token_*` codes are one set |
   | `pipeshub_agent_harness_egress_decisions_total` | `decision` allowed, refused; `reason` how an allowed request went out (http, intercepted, database, tunnel) or the `EgressRefusalReason` |
   | `pipeshub_agent_harness_approvals_requested_total` | `org_bucket`: the first two hex characters of the org id's SHA-256 |
   | `pipeshub_agent_harness_kill_switch` (gauge) | `scope`: every operator scope (session, agent, org, engine_version, instance, egress), 0 when none |

   Anything outside a set reads `other`, so caller text, a token or a host never becomes a series. The org label is a bucket because the merged route drops org-labelled series (the scrape leaves the deployment with no tenant in it) and an org id is unbounded: 256 buckets keep the series bounded, a flood on one org still stands out from the fleet, and an operator finds an org's bucket with `sha256sum`.
3. **The merged route carries them.** The egress proxy, gateways and broker were already scraped by Node's merged route (`/api/v1/agent-harness/internal/metrics`), so no new scrape job or network is needed. Node's own families on that route grow by `pipeshub_auth_attempts_`, whose labels hold no org.
4. **A forged-token series starts at 0.** `increase()` sees nothing for a counter whose first sample is 1, so the first forgery would not alert. Each process creates the forged reasons of every kind at 0.
5. **Five alerts, thresholds from what ordinary use cannot reach** (`deployment/monitoring/prometheus/agent-harness.rules.yml`, group `pipeshub-agent-harness-security`; each links its runbook section):

   | Alert | Expression | Why this threshold |
   |---|---|---|
   | `PipesHubAuthFailureBurst` | sign-in failures (not tokens, not errors) `> 60` in 10 min, for 5 min | People mistype a few times an hour per org; 6 a minute sustained is a script. Expired session tokens and a users-service outage are excluded |
   | `HarnessEgressRefusalBurst` | refusals of one reason `> 30` in 10 min, for 5 min | An agent meets a refusal now and then; 3 a minute of one reason is probing |
   | `HarnessGatewayTokenRejected` | a forged-shaped refusal (`malformed`, `algorithm`, `unknown_key`, `signature`, `issuer`, `audience`, `credential`) `> 0` in 10 min, critical | These listeners are reachable only inside the deployment; any forgery there is a compromise until a rotation explains it. Expired and superseded tokens are ordinary and excluded |
   | `HarnessApprovalRequestFlood` | asks in one org bucket `> 60` in 15 min, for 5 min | A person answers a few asks a minute at most; 4 a minute sustained in one org wears approvers down |
   | `HarnessKillSwitchEngaged` | an instance, org, engine version or egress stop in force | A stop is always worth knowing; session and agent stops are routine admin actions and do not page |

   Thresholds are starting points an operator tunes, like the other alerts'.
6. **Runbooks** for each alert and for the three missing playbooks: a stolen release key or registry token, a leaked env file or `SECRET_KEY` (what the holder could read), a compromised host account.

## As built

- Node: `src/libs/services/telemetry/modules/auth-metrics.ts`, `src/modules/agent-harness/adapters/telemetry/token-rejection-metrics.ts`, the approvals counter and `orgBucket` in `adapters/telemetry/chat-path-metrics.ts`, `KILL_SWITCH_METRIC` and `renderKillSwitch` in `application/harness-local-metrics.ts`.
- Python: `harness_core/telemetry/security_signals.py`, `app/telemetry/modules/security_metrics.py`.
- Tests: `tests/unit/harness/security/test_sec_d_13_security_signals.py` (Python decisions, bounded labels, a throwing sink and logger), `tests/cohesion/test_ob04_monitoring.py` (each rule reads an exported metric, links an existing runbook section, and fires on a synthetic attack and not on ordinary use, by a small evaluator of these expression shapes since no `promtool` is installed), Node `tests/modules/agent-harness/sec-d-13-security-signals.test.ts`, the sign-in cases in `tests/modules/auth/controller/userAccount.signInFlow.test.ts` and the approvals case in the execution-store contract.

## Deviations

- No separate Prometheus job for the egress proxy: it was already read through Node's merged route on its control port.
- The refresh-token exchange and scoped service tokens are not counted: neither can be guessed (both are signed), and the sign-in and bearer checks are where volume attacks land.

## Owner amendments

None.

## Status

**Accepted** 2026-11-13: decided from first principles, long term; built by R41-SUPPLY.
