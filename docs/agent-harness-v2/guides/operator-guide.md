# Agent harness: operator guide

Checklist item C18.09. This guide is for whoever installs, sizes, upgrades and keeps PipesHub's agent harness
running, from Docker Compose on a laptop to Helm on a cluster. It gathers what an operator needs and links the
pages that hold the detail rather than repeating them. Org admins' settings (Labs, engine profiles, budgets,
approvals) are in the [admin guide](admin-guide.md).

| Page | What it covers |
|---|---|
| [sizing.md](sizing.md) | How big a host or cluster to run, from the measured numbers and the code's own limits |
| [runbooks.md](runbooks.md) | Incident response, stuck session, exhausted pool, gateway 5xx spike, harness CVE response, credential rotation, approval backlog |
| [air-gapped-install.md](air-gapped-install.md) | Installing with no route out: mirrored images, engine images, local models |
| [FLAGS.md](../FLAGS.md) | Every switch and setting, with rollback steps |
| [SLO.md](../SLO.md) | Latency and availability targets, RPO and RTO |
| [DR-DRILL.md](../DR-DRILL.md) | The disaster recovery drill |
| [SUPPLY-CHAIN.md](SUPPLY-CHAIN.md) | Locked dependencies and pinned images |
| [security-whitepaper.md](security-whitepaper.md) | Isolation, egress, credentials, audit |

## What runs

The harness adds these processes to PipesHub (the layout and ports are in [AGENTS.md](../../../AGENTS.md),
"Agent harness"; the design in [ARCHITECTURE.md](../ARCHITECTURE.md)):

| Process | Compose service | Helm | Port (internal) |
|---|---|---|---|
| Node API, relay, run sweeper | `pipeshub-ai` | the app Deployment | 3000 (public), 3002 (worker-only routes) |
| Harness worker and Tool Gateway (`app.harness_main`) | `pipeshub-ai` | the app pods | 8098 |
| Model Gateway (`app.model_gateway_main`) | `pipeshub-ai` | the app pods | 8096 |
| Sandbox broker (`app.sandbox_broker_main`) | `sandbox-broker` (profile `sandbox`) | `<release>-sandbox-broker` | 8095 |
| Egress proxy (`app.egress_proxy_main`) | `egress-proxy` (profile `sandbox`) | not in the chart | 8097 data, 8098 control |

Only port 3000 is published. Port 8098 is the Tool Gateway inside the app container and, separately, the egress
proxy's control port on its own container.

## Choosing a deployment

| Shape | Use it for | Notes |
|---|---|---|
| Compose on a laptop (`PIPESHUB_DEPLOY_TYPE=eval`) | Trying the harness | About 5.9 GB idle with the harness on ([footprint-baseline.md](../footprint-baseline.md)). Keep coding engines and the warm pool off |
| Compose on one server (`./install.sh`) | A team on one host | MongoDB is a one-member replica set; a host restart is an outage (99.5% target in SLO.md). Coding engines run here |
| Helm with `values-cloud.yaml` or `values-eks.yaml` | Production at scale | Three MongoDB data members, two or more app pods, 99.9% target. The base `values.yaml` has a MongoDB arbiter, so durable runs stay off there. The chart ships no egress proxy yet, so coding engines need Compose |

Durable runs need a MongoDB replica set with a `majority` journal, the relay and the worker; Compose ships all
three on. The full list, and how the app reports the first one missing, is in the admin guide's
[Turning the harness on](admin-guide.md#turning-the-harness-on). Coding engines also need
`HARNESS_ENGINE_RUNS=true` and, in production, gVisor (`./install.sh --install-gvisor`,
[GVISOR.md](../../../deployment/docker-compose/GVISOR.md)).

Size the host with [sizing.md](sizing.md); for an install with no internet, follow
[air-gapped-install.md](air-gapped-install.md).

## Monitoring

Start the opt-in overlay with the stack:
`docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d` (files in
`deployment/docker-compose/`). It runs Prometheus and Grafana bound to the host's loopback, scrapes every harness
service through Node's worker-only route `GET /api/v1/agent-harness/internal/metrics` with the worker credential
(which Node writes to `HARNESS_METRICS_CREDENTIAL_FILE`), and loads the dashboard "PipesHub agent harness" and the
alert rules in `deployment/monitoring/prometheus/agent-harness.rules.yml`. Each alert has a runbook:

| Alert | Runbook |
|---|---|
| `HarnessRunLeaseExpiredStuck`, `HarnessOldestRunningRunTooOld`, `HarnessClaimLatencyHigh`, `HarnessRunsStranded`, `HarnessRunsQuarantined`, `HarnessRunsQuarantinedBurst` | [Stuck session](runbooks.md#stuck-session) |
| `HarnessWarmPoolEmptyClaimsCold` | [Exhausted pool](runbooks.md#exhausted-pool) |
| `HarnessGatewayErrorRatioHigh`, `HarnessMetricsScrapeDown` | [Gateway 5xx spike](runbooks.md#gateway-5xx-spike) |
| `HarnessModelCostSpike` | The org's budgets (admin guide, [Budgets](admin-guide.md#budgets)); if unexpected, [Incident response](runbooks.md#incident-response) |

**Stopping work.** A person stops their own run from the chat
(`POST /api/v1/conversations/{sessionId}/runs/{runId}/cancel`). `AGENT_HARNESS_KILL_SWITCH` (Compose `.env`, Helm
`agentHarness.killSwitch`) stops all agent work on the instance: after the restart every new turn goes to the classic
chat and Node cancels the harness runs in flight within 60 s.
[Incident response](runbooks.md#incident-response) says which to pick.

Beside them: `GET /api/v1/health` reports `agentHarness` (`off`, `available` or `unavailable` with a reason)
without failing the product's own health, and the canary (`scripts/engines/canary.py`, every 15 minutes) runs
one real engine run end to end ([Canary](#canary)). For a security audit trail, run the
[grant auditor](#grant-auditor) beside the worker.

## Backup and restore

What to back up (RPO and RTO targets in SLO.md, "RPO and RTO"):

| What | Compose | Helm |
|---|---|---|
| MongoDB (journal, run queue, sessions, wrapped data keys) | `mongodump --archive --gzip --oplog` against the `mongodb` service, daily ([ADVANCED_DEPLOYMENT.md](../../../deployment/docker-compose/ADVANCED_DEPLOYMENT.md), "Backup") | the chart README's `mongodump` and `mongorestore`; on EKS the AWS Backup plan snapshots tagged volumes daily |
| Blob storage (artifacts, journal payloads, workspace files) | the `pipeshub_root_local` volume, or your S3 or Azure container | the object store (bucket versioning on) |
| `.env`, holding `SECRET_KEY` and `MONGO_PASSWORD` | copy it to your secret store | the chart's secret |
| The replica set key | the `mongodb_replset_key` volume, or keep `MONGO_REPLICA_SET_KEY` | the chart's secret |

`SECRET_KEY` is the root of the key that wraps every org's data keys: a backup restored under another
`SECRET_KEY` cannot be read. Keep it with the backups. When you rotate it
([Rotating `SECRET_KEY`](runbooks.md#rotating-secret_key)), keep the old key with every backup taken before the
rotation: restore those under the old key (as `SECRET_KEY`), then rotate again. Qdrant and Neo4j hold indexed knowledge that re-indexing
rebuilds; they are not part of the harness backup.

**Restore the whole deployment** by the drill in [DR-DRILL.md](../DR-DRILL.md): restore MongoDB and the blobs
together, start the stack with the same `SECRET_KEY`, and runs that were in flight resume from their last
checkpoint once the sweeper finds their lapsed leases; their outside effects run at most once. Sandbox snapshots
come back with a whole-deployment restore.

**Move or recover one conversation** with the session backup CLI in the app image:
`node dist/modules/agent-harness/ops/session-backup-cli.js export …` and `… restore …`, steps and exit codes in
[Session backup and restore](#session-backup-and-restore). It copies the conversation's MongoDB documents, not its blobs (copy the
org's object prefix as well), and not its sandbox snapshots: a restored session's next engine turn starts cold.
The drill `backend/python/tests/unit/harness/engines/test_p9_backup_drill.py` runs these steps around a crashed
engine run.

## Upgrades

1. **Read the release notes** ([CHANGELOG.md](../../../CHANGELOG.md), and FLAGS.md "Release notes") for the
   harness and engine versions and any upgrade step.
2. **Back up** MongoDB and `.env` first.
3. **Compose:** `./install.sh --upgrade` keeps `.env` and the data volumes and recreates the containers. Runs in
   flight survive: the worker drains its runs on stop and the next worker resumes them from their checkpoints.
   An install upgraded under the compatibility profile keeps `SANDBOX_MODE=docker` and starts no sandbox broker, so
   coding engines are unavailable there; to use them, opt in with `PIPESHUB_AGENT_HARNESS=on ./install.sh --upgrade`
   (or `PIPESHUB_HARDENED=true ./install.sh --upgrade`), which moves code execution to the broker once it is healthy
   ([HARNESS-OFF.md](HARNESS-OFF.md)).
4. **Order on Helm or a split deployment:** migrations (Node runs them at start, before admitting), then workers,
   then Node. If Node goes first anyway, a run that needs a newer
   worker waits for it rather than running on an old one.
5. **Watch** `pipeshub_agent_chat_turns_total{path="durable"}` against `{path="legacy"}` and the alerts above.
6. **Roll back** with the kill switch (or Labs), then the previous release. Two rollbacks need steps first, both in
   FLAGS.md, "Rolling back (mid-flight)": to a release before history by reference
   (`HARNESS_RUN_HISTORY_BY_REFERENCE=false` first) and to a release before the control-plane pin
   (`CONTROL_PLANE_PIN=shadow` first).

**Engine versions** move with the release: `backend/python/harness_engines/allowlist.json` pins each engine's
version and image, a conversation keeps the version it started on while that version stays listed, and new
conversations start on the first one listed. Rebuild the engine images after an upgrade that changes them
(`deployment/sandbox-images/engine-build.sh KIND`). A fixed version for a vulnerable engine goes through the
candidate gate: [Harness CVE response](runbooks.md#harness-cve-response). The upgrade pipeline (ADR-OPS-01 D7):
a scheduled candidate job gates each engine's latest release every night and uploads a catalog entry and a
draft allowlist change; a reviewed release adds the version beside the default, an org opts in by naming it in
its engine profile ceiling, and a later release makes it the default. A version an admin stopped continues its
chats on a listed version of the same engine.

## Operator procedures

### Session backup and restore

1. **Back up** the session from the running deployment (the CLI only reads the source):
   `node dist/modules/agent-harness/ops/session-backup-cli.js export --mongo-uri "$MONGO_URI" --db "$MONGO_DB_NAME" --org <orgId> --session <sessionId> --out /backups/<sessionId>`.
   It prints one JSON line with the document counts per collection and the archive digest; keep it with the
   archive. Under `DOC_STORE=postgres` the CLI connects as the application and takes no `--mongo-uri` or `--db`.
2. **Back up the objects**: copy the org's object partition from the blob store (local: the org's storage
   directory; S3 and Azure: the org's prefix, or rely on bucket versioning). Objects are content-addressed and
   sealed per session; another session's objects in the copy cannot be read without its key, which the archive
   does not hold.
3. **The KEK** (`SECRET_KEY`, or the KMS key) of the target must be the source's: the archive holds the org's data
   keys wrapped with it, never in clear. The worker credential and signing keys stay the target's own.
4. **Restore** into the target deployment's database, from the target's app image so its own `MONGO_URI` and
   `MONGO_DB_NAME` apply (its Node may be running; its migrations create the indexes):
   `… session-backup-cli.js restore --mongo-uri "$MONGO_URI" --db "$MONGO_DB_NAME" --in /backups/<sessionId>`.
   Exit 1 with `session_exists` means the target already holds the session (nothing was written);
   `archive_corrupt` means a file changed since the export (nothing was written). Copy the objects into the
   target's blob store.
5. **Verify**: open the conversation; an interrupted run is requeued by the sweeper once its old lease has lapsed
   and resumes from its last checkpoint and workspace head. Its effects run at most once: the effect log came with
   the journal.
6. **Erasure** works on the restored copy as on any session (delete the conversation). Erasing the source does not
   reach the copy, or the other way round: erase both where both must go.

### Canary

1. Create an agent whose engine profile runs on the deployment's cheapest model, and an API token with
   `agent:execute` and `agent:read` for a service user who may run that agent.
2. Run `PIPESHUB_CANARY_TOKEN=<token> python3 scripts/engines/canary.py --base-url https://<host> --agent-key <key>
   --report /var/log/pipeshub/canary.json` every 15 minutes (cron) or after each deploy (CI). Exit 0 pass, 1 fail
   (`reason`: the run's error type, `timeout`, `answer_mismatch`, `not_admitted`), 2 the deployment could not be
   reached or refused the token. Alert on two failures in a row; the report's `timings` (admit, first progress,
   terminal, total, in ms) are the latency series.
3. Each canary run is a real conversation of the service user; the org's retention setting erases them.

### Grant auditor

Run beside the worker and the model gateway, reading their logs:
`python -m app.harness.audit --follow --interval 60 --state /var/lib/pipeshub/audit-cursor.json --log /var/log/pipeshub/worker.log --log /var/log/pipeshub/model-gateway.log`
(from the app image; Node's address and the worker credential come from the deployment's configuration, as the
worker's do). Alert on `audit.violation` (error): a gateway served a call outside the run's pinned grant, or the
effect log journaled an intent of an ungranted tool. Treat it as a security incident. For a one-off check,
`--once` exits 1 on a violation and 2 when Node cannot answer.

### Tracing

Set `HARNESS_OTEL=true` and `OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector>:4318` on Node, the worker and the model
gateway. One run is one trace: its trace id is `sha256("pipeshub.run/" + runId)[:32]` (hex), so a log line's
`runId` finds it. Leave `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` off unless the trace store may hold
conversation content.

### Node stops, crashes and starts

**Stopping a Node (a rolling deploy, `docker compose stop`, a pod eviction).** On SIGTERM Node (1) stops its
background loops and releases their leader leases, the run sweeper's first; (2) stops accepting connections and
answers every request it already accepted, for at most 10 s; open event streams are then closed and their clients
reconnect to another replica with `Last-Event-ID`; (3) closes its stores and exits 0. Expect
`sweeper.leader_released` from the stopping leader and `sweeper.leader_acquired` from another replica within one
sweep interval (5 s). `sweeper.lease_release_failed` or `audit.anchor_lease_release_failed` at a stop means the
store was closed or unreachable first: the next leader then waits out the lease (15 s for the run sweeper), as
after a crash. The image's process monitor signals Node only after the workers have drained (up to 40 s of the
container's 60 s grace in Compose; `terminationGracePeriodSeconds` is 300 in the chart), so Node's 10 s fit.

**A Node that crashed.** Runs it had answered claims for are unaffected (their workers renew through any Node).
A claim it committed and never answered is recovered within `max(lease, takeover) + 5 s` (SLO #6): the takeover
is at most the sweeper's 15 s leader lease, or the Node's restart with a single replica. `sweeper.leader_lost` on
a live Node means its lease lapsed under it (a stall longer than 15 s) and another replica leads; that is safe
(two sweepers expire a run once) and worth a look at what stalled it.

**A Node that starts while MongoDB is away.** Unreachable: the process exits 1 (`Failed to connect to MongoDB`)
and its supervisor starts it again: the image's process monitor in Compose and Helm, where the container then
restarts by `restart: always` or the kubelet; a bare `node dist/index.js` needs your own supervisor. Reachable
but without a primary: the process stays up, listens on nothing, and logs `Agent harness store unreachable; not
serving until it answers` with the error and the attempt, at most 8 s apart; it is ready within 8 s of a primary
answering. Every probe (Compose's `healthcheck`, the chart's probes, the process monitor) is an HTTP request to
Node's port, so each reports it not ready meanwhile.

**MongoDB answers but the agent harness cannot prepare** (a standalone server, a set with an arbiter, no
majority, a user without the privileges to build indexes, an index build refused). PipesHub starts and serves
everything; chat runs on today's loop. The log says `Agent harness unavailable: its store preparation failed;
serving everything else and trying again` with the error, `GET /api/v1/health` shows `agentHarness: {status:
'unavailable', reason}` (its own `status` is unaffected), and `pipeshub_agent_harness_store_prepared` is 0. Node
tries again every 8 s; when it succeeds it logs `Agent harness on: its store preparation succeeded`, the gauge
goes to 1, and durable runs start with no restart. Fix what the reason and the error name; to run without durable
runs on purpose, set `AGENT_HARNESS_RELAY_ENABLED=false`.

## Runbooks

[runbooks.md](runbooks.md) holds the incident runbook and the six operational ones. The canary, the grant auditor,
tracing and Node stops are under [Operator procedures](#operator-procedures); the erasure backfill is in
[FLAGS.md](../FLAGS.md), `session_erasure`.
