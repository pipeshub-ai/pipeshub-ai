# Agent harness: runbooks

Checklist items C6.43 (the incident runbook) and C15.06 (OP-07), part of the [operator guide](operator-guide.md).
Each runbook has the same parts: **Symptoms** (what you see), **Check** (how to find the cause) and **Act** (what
to do). Every environment variable, log event, metric, route and command below exists in the code; the docs
check `backend/python/tests/cohesion/test_wp87_operator_docs.py` fails when one stops existing.

Commands are written for Docker Compose, run from the installation directory (`deployment/docker-compose`, or
the directory `install.sh` downloaded into). The sandbox broker and the egress proxy are in the `sandbox` Compose
profile, so their commands carry `--profile sandbox`. On Helm, read `kubectl logs` of the app pods and of the
`<release>-sandbox-broker` pod for the same lines, and set environment variables through `extraEnv` in your
values file.

Where to read logs:

| Process | Compose | Lines of interest |
|---|---|---|
| Node API, relay, sweeper | `docker compose logs pipeshub-ai` | `sweeper.*`, `audit.*`, `authority.refused`, `gateway_refusal.journaled` |
| Harness worker, Tool Gateway | same container | `slots.*`, `journal.*`, `run.*`, `engine.*`, `gateway.*` |
| Model Gateway | same container | `model_gateway.*` |
| Sandbox broker | `docker compose --profile sandbox logs sandbox-broker` | `broker.*`, `sandbox.*` |
| Egress proxy | `docker compose --profile sandbox logs egress-proxy` | `egress.*` |

Harness log lines are one event name followed by `key=value` fields; they carry ids, reason codes and counts,
never conversation content.

The monitoring overlay (`deployment/docker-compose/docker-compose.monitoring.yml`) adds Prometheus and Grafana
with the dashboard "PipesHub agent harness" and the alert rules in
`deployment/monitoring/prometheus/agent-harness.rules.yml`. The alerts named below are those rules.

## Incident response

For a suspected compromise: a sandbox reaching where it should not, a leaked credential, a gateway serving a call
outside a run's grant (`audit.violation`), or a vulnerable engine being exploited. Work in this order: contain,
preserve evidence, rotate, recover.

**Symptoms.** An `audit.violation` line from the grant auditor; `egress.exfiltration_suspected` or a burst of
`egress.refused` in the egress proxy's log; an `egress.volume_alert`; a credential seen where it should not be;
a security advisory for a component you run.

**Check.** Decide the blast radius before you act: one run, or the whole instance. The run's journal and its
records on the org's audit chain show its outside access, approvals and tool calls.

**Act.**

1. **Contain.** Pick the narrowest stop that covers the blast radius:

   | Scope | Stop | Effect |
   |---|---|---|
   | One run | its person stops it from the chat (`POST /api/v1/conversations/{sessionId}/runs/{runId}/cancel`) | The run is cancelled; its gateway tokens are refused |
   | The whole instance | `AGENT_HARNESS_KILL_SWITCH=true` and a restart | Every harness run is cancelled; new turns go to the classic chat |

   The instance stop takes effect within 60 s of the restart: Node's sweeper cancels the open runs, a cancelled
   run's gateway tokens are refused, and the egress proxy binds no sandbox serving a harness run. Watch
   `operator.runs_stopped` in Node's log.

   `AGENT_HARNESS_KILL_SWITCH=true` (Compose `.env`, Helm `agentHarness.killSwitch`) is set on Node and the egress
   proxy: set it and recreate `pipeshub-ai` and `egress-proxy`. It can only be cleared by unsetting it. The proxy
   logs `egress.kill_switch_on` when it starts with it on. The effects, and what to watch
   (`pipeshub_agent_chat_turns_total` moving to `path="legacy"`), are in FLAGS.md, "Rolling back (mid-flight)"
   ([FLAGS.md](../FLAGS.md#rolling-back-mid-flight)).

2. **Preserve evidence** before anything restarts or retention erases it:
   - **Journal and audit records** are kept in the harness store, the audit chain append-only and anchored:
     copy the conversation (below) before retention can erase it. The chain records `gateway.refused` (a gateway
     refused a run's request: the gateway, the reason, the host), `egress.allowed`, `egress.volume_alert` and
     `tool.denied`. The grant auditor below checks the saved logs against the chain.
   - **A whole conversation**, journal included (sealed, as it is stored):
     `node dist/modules/agent-harness/ops/session-backup-cli.js export --mongo-uri "$MONGO_URI" --db "$MONGO_DB_NAME" --org <orgId> --session <sessionId> --out /backups/<sessionId>`
     (operator-guide.md, "Session backup and restore"). Keep the JSON line it prints with the archive.
   - **Egress proxy logs.** They are the container's output, not a file, and Docker's `local` log driver rotates
     them, so copy them now:
     `docker compose --profile sandbox logs --no-color --timestamps egress-proxy > egress-proxy.log`. Look for
     `egress.refused` (fields `sandboxId`, `reason`, `status`, `host`), `egress.exfiltration_suspected`,
     `egress.bound`, `egress.unbound` and `egress.revoked`.
   - **Gateway and worker logs**: `docker compose logs --no-color --timestamps pipeshub-ai > app.log`
     (`model_gateway.refused`, `gateway.refused`, `gateway.call`, `run.*`).
   - **The grant auditor** over the saved logs, run inside the app container with the log copied in:
     `python -m app.harness.audit --once --log app.log` exits 1 on a violation and 2 when Node cannot answer
     (operator-guide.md, "Grant auditor").

3. **Rotate** what may have leaked: [Credential rotation](#credential-rotation).

4. **Recover.** Fix the cause (for a vulnerable engine: [Harness CVE response](#harness-cve-response)), then
   unset `AGENT_HARNESS_KILL_SWITCH` if you set it. Resuming does
   not restart cancelled runs: their people send their message again. A revoked sandbox never binds again;
   its run's next turn starts a new sandbox. Add the incident's trace as a regression case before closing it.

## Stuck session

**Symptoms.** A run shows as working and nothing moves; `HarnessRunLeaseExpiredStuck` (a running run whose lease
expired was not requeued for 10 minutes), `HarnessOldestRunningRunTooOld` (a run running for more than 6 hours),
`HarnessClaimLatencyHigh` (new runs wait more than 5 s for a worker), `HarnessRunsStranded`.

**Check.**

1. `GET /api/v1/health`: `agentHarness.status` must be `available`. `unavailable` with a reason means Node
   cannot prepare the harness store: follow operator-guide.md, "Node stops, crashes and starts".
2. `GET /api/v1/agent-harness/admin/dependencies` (admin): MongoDB, the KV store, the broker and blob storage,
   each `ok` or `degraded` over the last 60 s.
3. The run itself: `GET /api/v1/conversations/{sessionId}/runs/{runId}` (status) and
   `GET /api/v1/conversations/{sessionId}/runs/{runId}/timeline`. A run in `waiting_approval` is waiting for a
   person, not stuck: see [Approval backlog](#approval-backlog).
4. In MongoDB, runs whose lease lapsed: `db.agentRuns.find({status: "running", leaseExpiresAt: {$lt: new Date()}})`.
5. Logs: a sweeper leader (`sweeper.leader_acquired`; `sweeper.tick_failed` means it cannot sweep), runs it
   requeued or ended (`sweeper.expired`); the worker's `journal.unreachable`, `journal.ownership_lost`,
   `slots.claim_failed`, `slots.run_crashed`, `run.lease_lost`, and `run.quarantine` / `engine.kernel.quarantine`
   (a run that crashed its attempt three times is failed, not retried).

**What recovers by itself.** A worker renews its run's 60 s lease every 20 s. The sweeper (one leader across Node
replicas, leader lease 15 s) runs every 5 s: a running run whose lease lapsed is requeued and resumes from its
last checkpoint, within `max(60, 15) + 5` = 65 s of the worker's loss (SLO #6 in [SLO.md](../SLO.md)). A run past
its deadline (`absoluteDeadlineAt`, one hour by default from `runs.deadlineMs` in
`contracts/harness/core/policy/release.json`) is failed `deadline_exceeded`. An interactive run no worker claims
within 90 s fails `not_started`. None of these intervals is an environment setting.

**Act.**

- No sweeper leader, or `sweeper.tick_failed`: MongoDB is unreachable or has no primary; fix MongoDB, then the
  sweeper resumes on its own.
- No worker claims (`HarnessClaimLatencyHigh`, and no `worker.started` in the log since the app started): the
  worker is down or off. `AGENT_HARNESS_WORKER_ENABLED` must not be `false`; the image's process monitor restarts a worker that
  exits. `docker compose restart pipeshub-ai` restarts the whole app; runs in flight resume from their
  checkpoints.
- Engine runs only: `engine.worker.unavailable` (`broker_unavailable`) means the worker cannot reach the broker;
  see [Exhausted pool](#exhausted-pool).
- A run that must end now: its owner presses Stop, or calls
  `POST /api/v1/conversations/{sessionId}/runs/{runId}/cancel` (agent conversations:
  `POST /api/v1/agents/{agentKey}/conversations/{sessionId}/runs/{runId}/cancel`) with an `idempotencyKey`. A
  run whose worker is gone ends `cancelled` once its lease lapses.

## Exhausted pool

"Pool" here is one of three: the sandboxes one Docker host may hold, the warm pool, or the model provider slots.

**Symptoms.** Engine runs wait and then fail `sandbox_unavailable` (the run retries the sandbox at 15, 30, 60, 120
and 240 s, about 8 minutes in all); `sandbox.capacity_reached` (`sandbox_capacity`) in the broker's log;
`HarnessWarmPoolEmptyClaimsCold`; model calls queue (`model_gateway.slot_waited`).

**Check.**

1. The broker's log: `broker.started` shows its settings; `sandbox.capacity_reached` means the host is at its
   limit; `broker.provider_unavailable` means the broker cannot reach Docker; "the egress subnet pool is
   exhausted" means it ran out of sandbox networks.
2. How many sandboxes exist: `docker ps --filter label=io.pipeshub.sandbox-broker.managed=1`; and their
   networks: `docker network ls --filter label=io.pipeshub.sandbox-broker.managed=1`.
3. The host's memory and CPU against the limits in [sizing.md](sizing.md#how-the-code-shares-a-host-out): the
   broker holds at most `floor(memory × 0.75 ÷ 1,024 MiB)` sandboxes and never more than `SANDBOX_MAX_TOTAL`.
4. Warm pool: the dashboard's "Pool depth (warm pool)" and "Warm sandbox claims" panels
   (`pipeshub_harness_sandbox_warm_pool_depth`, `pipeshub_harness_sandbox_warm_claims_total{outcome}`; outcome
   `org_capped` means one org used its share while another waited).

**Act.**

- At `SANDBOX_MAX_TOTAL` with memory and CPU to spare: raise it and recreate the broker
  (`docker compose --profile sandbox up -d sandbox-broker`). At the memory limit: add memory or a host; do not
  raise the limit past what the host holds.
- Sandboxes left over from a crash: restarting the broker reconciles what it owns, removing orphan sandboxes and
  networks (`broker.reconciled`, with `orphanNetworks`):
  `docker compose --profile sandbox restart sandbox-broker`.
- Sandbox networks exhausted: each sandbox with an egress allowlist gets a /29 from `SANDBOX_EGRESS_SUBNETS`
  (default `10.213.0.0/16`); widen it if your network allows, after checking it overlaps nothing on the host. The
  broker does not draw on Docker's default address pools.
- Sessions start cold: set `SANDBOX_WARM_POOL_MAX` (0, off, by default) if memory allows; warm sandboxes hold
  memory while idle.
- Model slots: the Model Gateway admits at most `MODEL_GATEWAY_PROVIDER_CONCURRENCY` calls to one provider at
  once (64 by default; 0 is no limit). Raise it only within the provider's own rate limit.

## Gateway 5xx spike

**Symptoms.** `HarnessGatewayErrorRatioHigh`: more than 5% of one gateway's requests failed for 10 minutes;
engine runs end or pause on `provider_unavailable`; users see that the model provider could not answer.

**Check.**

1. Which gateway and route: the dashboard's "Gateway errors" panel, or
   `pipeshub_harness_gateway_requests_total{outcome="error"}` by `gateway` (`model` or `tool`) and `route`, and
   `pipeshub_harness_gateway_refusals_total` by `reason`.
2. Is the gateway up: from the app container, `docker compose exec -T pipeshub-ai curl -s http://localhost:8096/healthz`
   (Model Gateway) and `docker compose exec -T pipeshub-ai curl -s http://localhost:8098/healthz` (Tool Gateway);
   each answers `{"ok": true}`. `HarnessMetricsScrapeDown` means a service's metrics could not be read.
3. Model Gateway: `model_gateway.provider_failed` lines carry `upstreamStatus`, `upstreamCode` and `errorType`.
   The gateway maps a provider failure to its own answer:

   | Provider answered | Gateway answers | Code |
   |---|---|---|
   | 5xx, a dropped connection, a timeout | 502 | `provider_unavailable` |
   | 401 or 403 | 502 | `provider_auth_failed` |
   | 429 | 429 | `rate_limited` |
   | 402 | 402 | `provider_quota` |
   | another 4xx | 400 | `provider_rejected` |

   The worker retries a `provider_unavailable` or `rate_limited` call (`model.retry`, `model.backoff`), waiting
   15, 30, 60 and then 120 s with the run's checkpoint kept, before it ends the run.
4. Tool Gateway: `gateway.refused` (a refused call, with its reason) and `gateway.call` lines in the app's log.

**Act.**

- `provider_unavailable` from one provider: the provider is down or slow. Nothing is lost while the worker backs
  off; if it lasts, admins switch the org's model (Workspace → **AI models**) or wait.
- `provider_auth_failed`: the provider refused the key. Rotate it ([Credential rotation](#credential-rotation)).
- `rate_limited` or `provider_quota`: lower `MODEL_GATEWAY_PROVIDER_CONCURRENCY`, or raise the provider's quota.
- The gateway itself fails (`/healthz` does not answer): the image's process monitor restarts a gateway process
  that exits; `docker compose restart pipeshub-ai` restarts both. A slow client is cut by
  `HARNESS_HTTP_HEADER_TIMEOUT_S` (15 s) and `HARNESS_HTTP_REQUEST_TIMEOUT_S` (60 s).

## Harness CVE response

For an advisory against an engine (opencode, Codex, Claude Code, Gemini CLI, Goose), an engine image's base, or a
Python or Node dependency.

**Symptoms.** A published advisory, or a finding from the weekly supply-chain job (`supply-chain-weekly.yml`).

**Check.** Which version you run: the engine versions and images are in
`backend/python/harness_engines/allowlist.json` (the release's list; [CHANGELOG.md](../../../CHANGELOG.md) names
them too); dependencies are in the hashed locks ([SUPPLY-CHAIN.md](SUPPLY-CHAIN.md)).

**Act.**

1. **Contain.** Stop the engines: set `HARNESS_ENGINE_RUNS=false` on the worker (chat then answers without the
   coding engine), or `AGENT_HARNESS_KILL_SWITCH=true` to stop all agent work. If the flaw is being exploited, follow
   [Incident response](#incident-response).
2. **Upgrade the engine.** Build the fixed version as a candidate,
   `deployment/sandbox-images/engine-build.sh KIND --candidate VERSION --digest sha256:HEX --asset PATH`, run the
   real-binary conformance suite against it, then pin it in `allowlist.json` by a reviewed change and build the
   run image (ADR-OPS-01 D7 describes the gate). Nothing runs a candidate tag in production.
   The nightly candidate job runs the same gate against each engine's latest
   release and uploads a catalog entry and a draft allowlist change when it passes.
3. **Upgrade a dependency.** Add the bad release to `contracts/supply-chain/denylist.json`, update and relock as
   [SUPPLY-CHAIN.md](SUPPLY-CHAIN.md) describes, and confirm with `python3 scripts/supply_chain.py check`.
4. **Roll out** in the upgrade order of the [operator guide](operator-guide.md#upgrades). A fixed release lists
   the new version with `resumes` naming the vulnerable one, so tasks paused by a retire continue on it; then
   **Resume** the version's control if it is still shipped.

## Credential rotation

**Symptoms.** A credential may have leaked; a scheduled rotation; `provider_auth_failed` from the Model Gateway.

**Check.** Which credential. The harness has these:

| Credential | Where it lives | How to rotate |
|---|---|---|
| Worker credential (workers, broker, egress proxy, gateways prove themselves to Node and to each other) | KV store, encrypted, generated by Node | `ROTATE_SIGNING_SECRETS` (below). The previous one is accepted for 15 minutes, and the Python services re-read it, so no restart is needed for them |
| Attempt-token and gateway-token signing keys (Ed25519) | KV store | `ROTATE_SIGNING_SECRETS`. Retired keys still verify for 15 minutes; the keys also rotate on their own every 30 days |
| Platform JWT, scoped JWT and cookie secrets | KV store | `ROTATE_SIGNING_SECRETS`: **every signed-in user is logged out** |
| An org's model provider key | AI models settings | Rotate at the provider, then save the new key in Workspace → **AI models** |
| Personal access tokens | users' settings | The user revokes the token and makes a new one |
| `SECRET_KEY` (the platform cipher of every configuration secret and OAuth client secret, the root of the key-encryption key, the egress proxy's sealed state, run-key digests) | `.env` / the chart's secret | Two restarts and one command, below: [Rotating `SECRET_KEY`](#rotating-secret_key) (ADR-KEY-01) |
| The release signing key | the owner's | Re-sign `vendor_rules.json` and `vendor_hosts.json` with `scripts/engines/sign_vendor_rules.py`, then rebuild the release (the release build signs `acp_agents.json` with `--acp-out`, ADR-SEC-01 D7) (owner item OB-1 in the project tracker) |

**Act.**

- **Compose:** `./install.sh --rotate-signing-secrets` writes a new `ROTATE_SIGNING_SECRETS` id to `.env` (type
  `ROTATE` to confirm, or pass `--yes`) and recreates the app. On its next start Node rotates the platform
  secrets, the worker credential and both signing keyrings once, and logs `Agent harness authority provisioned`;
  `Agent harness authority provisioning failed` means it did not, and every claim is refused until it does.
- **Helm:** set `ROTATE_SIGNING_SECRETS` to a new value through `extraEnv`, then roll the app pods. A value Node
  has already applied changes nothing, so a rotation never repeats by accident.
- After rotating, `authority.refused` in Node's log names callers still holding the old credential once the 15
  minutes have passed; restart them.

### Rotating `SECRET_KEY`

The deployment holds two keys while it moves: `SECRET_KEY` writes, and one other key is only read
(`SECRET_KEY_NEXT` before the switch, `SECRET_KEY_PREVIOUS` after it, never both). One command,
`rotate-secret-key-cli.js`, then moves what was sealed under the old key; it can be stopped and run again at any
point. Journal content does not move: only the wraps of the org data keys change. Design and failure behaviour:
[ADR-KEY-01](../adr/ADR-KEY-01-secret-key-rotation.md).

Before you start: take a backup and keep the **old** key with it (a backup taken before the rotation restores only
under the old key). Every service must run a release with the key ring; an older build reads only `SECRET_KEY`.
Make the new key with `openssl rand -hex 32`.

1. **Introduce the new key.** Every service reads it; nothing is written under it yet.
   - Compose: in `.env`, keep `SECRET_KEY` and set `SECRET_KEY_NEXT=<new key>`, then
     `docker compose --profile sandbox up -d`.
   - Helm: `helm upgrade <release> <chart> --reuse-values --set secretKeyNext=<new key>`, then
     `kubectl rollout status deployment/<release>-pipeshub-ai`. With `existingSecrets` or `externalSecrets`, add
     the key `secret-key-next` to the app secret instead (`secretManagement.externalSecrets.remoteRefs.secretKeyNext`).
2. **Switch.** The new key writes; the old one is still read.
   - Compose: set `SECRET_KEY=<new key>` and `SECRET_KEY_PREVIOUS=<old key>`, empty `SECRET_KEY_NEXT`, then
     `docker compose --profile sandbox up -d`. On one host you may join steps 1 and 2.
   - Helm: `helm upgrade <release> <chart> --reuse-values --set secretKey=<new key> --set secretKeyPrevious=<old key> --set secretKeyNext=`
     and wait for the rollout. Do not skip step 1 with more than one replica: a replica still on the old key cannot
     read what a switched one wrote.
3. **Move the data.** First count, then move, then check, all from the app's container with its environment:
   - Compose:
     `docker compose exec -w /app/backend pipeshub-ai sh -c 'node dist/modules/agent-harness/ops/rotate-secret-key-cli.js dry-run --mongo-uri "$MONGO_URI" --db "$MONGO_DB_NAME"'`,
     then the same with `rotate`, then with `verify`.
   - Helm: `kubectl exec deploy/<release>-pipeshub-ai -- sh -c 'cd /app/backend && node dist/modules/agent-harness/ops/rotate-secret-key-cli.js rotate --mongo-uri "$MONGO_URI" --db "$MONGO_DB_NAME"'`
     (`dry-run` and `verify` the same way). On `DOC_STORE=postgres` leave out `--mongo-uri` and `--db`.

   `rotate` takes a lock (a second run is refused while the first holds it), rewraps every org data key, reseals the
   egress proxy's state (`python -m app.harness.sandbox.egress_rekey`, run from `/app/python`; `--python` and
   `--python-dir` name another interpreter or directory), reseals connector sync points' delta links in the graph
   (`python -m app.connectors.core.base.sync_point.rekey`, the same way), re-encrypts the configuration store and
   every OAuth app's client secret, and counts again. It prints a JSON line per step and a result line, counts only. Exit 0: done.
   Exit 1 with "open under neither SECRET_KEY nor SECRET_KEY_PREVIOUS": `SECRET_KEY_PREVIOUS` is not the key the
   data was written under; fix it and run again. If those values are known to be unreadable (written by another
   deployment), `--accept-unreadable <the count it printed>` lets the run go on. Stopped or failed half-way: run
   `rotate` again.
4. **Wait for what drains** (the grace window): running sandboxes whose routes hold a credential sealed under the
   old key (`draining` in the egress step; they end with their runs, one hour by default). A sync point a connector
   rewrote while `rotate` ran is counted `previous` in the sync-points step; run `rotate` again.
5. **Retire the old key.** `retire` (as in step 3) exits 0 only when nothing needs the old key any more, and says
   so; otherwise it names what still does. Then remove `SECRET_KEY_PREVIOUS`
   (Compose: empty it in `.env` and `docker compose --profile sandbox up -d`; Helm:
   `helm upgrade <release> <chart> --reuse-values --set secretKeyPrevious=`) and run `verify` once more.

A wrong key fails closed throughout: a service refuses to start when `SECRET_KEY_PREVIOUS` or `SECRET_KEY_NEXT`
equals `SECRET_KEY` or both are set, and a value that opens under neither key is reported, never overwritten.

On a test stack of `deployment/e2e/run.sh` (the human-test stack included) the commands above run as written: its app container has
`node` on `PATH` and a `python` that sees the code and its packages, as the image does. `run.sh rotate-secret-key
<run> [step...]` (and `scripts/human-test-stack.sh rotate-secret-key [step...]` for the human-test stack) runs the steps by name:
`introduce`, `switch` and `drop-previous` edit the stack's env file and bring it up on it; `dry-run`, `rotate`,
`verify` and `retire` are the command of step 3; with no step it runs the four data steps. `run.sh keyring` rotates
a fresh stack end to end and serves a scripted turn on the new key alone (KEYRING-FU-1).

## Approval backlog

**Symptoms.** The longest wait (`pipeshub_agent_harness_approval_oldest_wait_seconds`) is past 30 minutes. Many
runs waiting
(`pipeshub_agent_harness_approvals_waiting`); people say their agent is waiting; runs end `deadline_exceeded`
without having run.

**Check.**

1. The **Inbox** lists what waits for the signed-in person: the approvals of their own runs and the questions their
   runs asked them (`GET /api/v1/agent-harness/inbox`); the conversation's card shows the same request.
2. Why runs ask: the built-in rules, the operator's rule in `/services/agentHarness/approvals` and the flow
   guard (`HARNESS_FLOW_GUARD`). The admin guide's [Approvals](admin-guide.md#approvals) section lists what asks
   by default.

**What happens if nobody answers.** A run waits for a person for `HARNESS_WAIT_FOR_PERSON_DAYS` (7 days by
default, at most 365; an org's policy can only shorten it). While it waits, its deadline moves to the end of that
window and its remaining working time is kept. When the window ends the sweeper fails it `deadline_exceeded`,
and nothing it asked about was done.

**Act.**

- People decide several calls of one run at once with **Allow all** on its card.
- If the same harmless call asks again and again, set that tool to **Pre-approved** in its server's or toolset's
  **Tool approvals** rather than answering it each time; never turn the flow guard off to clear a queue.

## Security alerts

The five alerts below read counters each service increments where it makes a security decision (SEC-D-13,
[ADR-OPS-03](../adr/ADR-OPS-03-security-signals.md)). Their labels are fixed sets: no series names an account, a
token, a host or an org id, so the alert tells you what kind of attack it is, and the logs tell you who.

### Auth failure burst

**Symptoms.** `PipesHubAuthFailureBurst`: more than 60 sign-in attempts failed in 10 minutes across all accounts
(`pipeshub_auth_attempts_total`, `outcome="failure"`, a sign-in method). Credential stuffing tries a few passwords
on each of many accounts, so each account stays under its own lock and only the total rises.

**Check.**

1. Which method: the series' `method` label (`password`, `otp`, `other` for a method the server does not know, which
   is a script, not a browser). `outcome="error"` is the users service failing, not guesses: see
   [Gateway 5xx spike](#gateway-5xx-spike) instead.
2. Where from: the account activity records and the reverse proxy's access log for `/api/v1/userAccount/authenticate`,
   grouped by client address.

**Act.** Block the sources at the reverse proxy or the tunnel in front of the app. Turn on the sign-in challenge
(`TURNSTILE_SECRET_KEY`) if it is off. Accounts the run guessed right show up as a success from the same sources:
sign those people out ([Credential rotation](#credential-rotation)) and ask them to change their password.

### Egress refusal burst

**Symptoms.** `HarnessEgressRefusalBurst`: more than 30 sandbox requests refused for one reason in 10 minutes
(`pipeshub_agent_harness_egress_decisions_total`, `decision="refused"`). An agent is probing the allowlist or
trying to send data out, usually because it read injected text.

**Check.** The alert's `reason` says how: `destination_not_allowed` is probing; `placeholder_foreign`,
`caller_authorization`, `forbidden_pattern` and `body_encoded` are a sandbox trying to move a credential or encoded
data; `control_from_sandbox` and `source_mismatch` are a sandbox attacking the proxy itself. Read `egress.refused`
and `egress.exfiltration_suspected` in the egress proxy's log for the `sandboxId` and the run labels.

**Act.** Cancel the run ([Incident response](#incident-response), step 1). If more than one run of an org refuses
this way, stop the org's sandbox internet access (scope `egress`) while you find the shared source: a document, a
web page or a connector record the runs all read.

### Gateway token rejected

**Symptoms.** `HarnessGatewayTokenRejected`: a machine credential, an attempt token or a gateway token was refused
as forged or foreign (`pipeshub_agent_harness_token_rejected_total`, a `reason` such as `signature`,
`unknown_key`, `algorithm` or `credential`). These listeners are reached only from inside the deployment, so
something there holds a token it should not, or is making them up.

**Check.**

1. Was there a rotation in the last 15 minutes? A `credential` refusal right after `ROTATE_SIGNING_SECRETS` is a
   caller still holding the old credential: `authority.refused` in Node's log names its route. Restart it.
2. Otherwise, which listener: the series' `harness_service` (`node`, `egress_proxy`, `model_gateway`,
   `tool_gateway`, `sandbox_broker`), then its log line: `authority.refused` (Node),
   `egress.control.unauthorized` (egress proxy), `broker.http.unauthorized` (broker), `gateway.refused` and
   `model_gateway.refused` (gateways).

**Act.** Treat it as a compromise of the sandbox plane until the check explains it: contain and preserve evidence
([Incident response](#incident-response)), then rotate the worker credential and the signing keys
([Credential rotation](#credential-rotation)).

### Approval request flood

**Symptoms.** `HarnessApprovalRequestFlood`: more than 60 approvals or questions asked in 15 minutes in one org
bucket (`pipeshub_agent_harness_approvals_requested_total`). A run that asks again and again is waiting for a tired
**Allow all**.

**Check.** The label is `org_bucket`: the first two hex characters of the SHA-256 of the org id, never the id. Find
the org with `printf %s <orgId> | sha256sum | cut -c1-2`, then its runs in **Waiting for you** and the
**Inbox**. One run asking many times is the attack shape; many people each asking a few times is ordinary use, and
the threshold wants raising for this install.

**Act.** Cancel the run and tell its people not to approve its pending asks. If its asks follow text it read, treat
that source as injected ([Egress refusal burst](#egress-refusal-burst)). The [Approval backlog](#approval-backlog)
runbook covers a slow queue that is not an attack.

### Kill switch engaged

**Symptoms.** `HarnessKillSwitchEngaged`: an operator stop is in force (`pipeshub_agent_harness_kill_switch`, by
`scope`); here, the instance stop of `AGENT_HARNESS_KILL_SWITCH`. Agent work is stopped for everyone.

**Check.** The deployment's environment (Compose `.env`, Helm `agentHarness.killSwitch`) on Node and the egress
proxy; the proxy logs `egress.kill_switch_on` when it starts with it on.

**Act.** If it is intended, acknowledge the alert and keep the incident open; it clears when the switch is unset
and Node restarts. If no one admits to it, treat the deployment's configuration as changed without review: find
who changed it and what else changed with it.

## Release key or registry token stolen

**Symptoms.** A release, image or tag you did not publish; a signature that verifies over an artefact your build did
not make; a warning from the registry or from secret scanning; a host that held the key is compromised
([Host account compromised](#host-account-compromised)).

**Check.** Which key: the release signing key (it signs the engine rules, the vendor hosts and the ACP agents list,
[Credential rotation](#credential-rotation)) or the registry token (it pushes images). List the registry's tags
pushed since the key could have leaked and compare each digest with your own build records.

**Act.**

1. **Revoke the registry token** at the registry first: it is the faster path to running code. Make a new one
   scoped to push only, and store it only in the release job's secrets.
2. **Delete or untag** every image the check could not match to a build; a deployment pinned by digest keeps
   running what it verified.
3. **Retire the release key.** Make a new key, re-sign with it as [Credential rotation](#credential-rotation)
   says, and ship a release that removes the stolen key from the trust roots. Until that release is installed, an
   install still trusts the stolen key, so tell operators to pin the last good digests.
4. Keep the new key in the vault, never on a build or test host (RELEASE-CHECKLIST.md).

## Env file or SECRET_KEY leaked

**Symptoms.** The deployment's `.env`, the chart's secret or a backup holding them was readable by someone who
should not have it: a shared host, a leaked backup, a paste, a support bundle.

**Check: what could the holder read?** `SECRET_KEY` opens every configuration secret in the KV store (the platform
JWT secrets, the worker credential, the signing keyrings, each org's model provider keys, connector OAuth client
secrets and tokens), the egress proxy's sealed state and the sealed run inputs. The env file also holds the store
passwords (`MONGO_URI`, `REDIS_PASSWORD`, `QDRANT_API_KEY`). With network access to the stores, the holder could
read them directly; without it, the key alone opens only what they also copied.

1. Was a store reachable from where the file went? The stores publish no port in Compose; check your own network
   rules and any tunnels.
2. Read the stores' own access logs, and the cloud provider's, for clients you do not know since the leak.

**Act.** Rotate in this order: the store passwords, then [Rotating `SECRET_KEY`](#rotating-secret_key), then
`ROTATE_SIGNING_SECRETS` (everyone is signed out), then every org's model provider keys and the connector
credentials at their providers, since the key could open them. Lock the env file to the owner (mode 600) and
remove the copies.

## Host account compromised

**Symptoms.** An unknown login, an SSH key or a process on the host that you did not put there; an account in the
`docker` group you did not add. Every member of the `docker` group can read every container's environment and
volumes: treat it as root on the deployment.

**Check.** `getent group docker` and `sudo` membership; the authentication log for logins since the account was
created or last trusted; which containers the account could reach.

**Act.**

1. **Contain.** Lock the account, remove its keys and its group memberships, and end its processes. If you cannot
   tell what it ran, move the deployment to a clean host and restore from a backup taken before the compromise.
2. **Assume it read everything on the host:** follow [Env file or SECRET_KEY leaked](#env-file-or-secret_key-leaked),
   and [Release key or registry token stolen](#release-key-or-registry-token-stolen) if a key or token was on it.
3. **Preserve evidence** before you rebuild: the auth log, the account's home directory, and the app's and the egress
   proxy's logs ([Incident response](#incident-response), step 2).
