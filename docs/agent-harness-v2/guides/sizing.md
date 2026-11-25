# Agent harness: sizing guide

Checklist item C15.05 (OP-05), part of the [operator guide](operator-guide.md). This page says how big a host or
cluster the agent harness needs, from the numbers measured so far and the formulas the code uses to share a host
out. Every number names its source. Where a number is a projection rather than a measurement, it says so.

**What is not measured yet.** No concurrency ramp has been run on a reference host, so there is no measured
"maximum stable concurrent sessions on 8 vCPU / 32 GB" (C7.09, DP8-12 in the project tracker). Until that
ramp runs, the session counts below are what the code's own limits allow, not what a host was seen to sustain.
What one Node process and one worker were seen to sustain on a 32-core x86-64 machine is in the table below
(the burst rows, WP-88): a ceiling for chat runs, not a reference-host number.
Start below them, watch the signals in [Watching headroom](#watching-headroom), and grow from there.

## What uses the resources

| Part | Where it runs | What it costs |
|---|---|---|
| Node API, relay, sweeper | `pipeshub-ai` container (Compose), the app pod (Helm) | One thread per process. Every admission, claim and journal commit passes through it |
| Harness worker (`app.harness_main`) and the Tool Gateway | `pipeshub-ai` container | A query-sized Python process, plus each run's working set |
| Model Gateway (`app.model_gateway_main`) | `pipeshub-ai` container | Streams model calls; adds little latency (below) |
| Sandbox broker, egress proxy | their own containers | Small; the broker starts every sandbox |
| Sandboxes | Docker on the same host (Compose) | One per engine session or `run_code` call: CPU, memory and scratch each |
| MongoDB (replica set) | `mongodb` | The journal, the run queue, sessions: grows with runs |
| Redis | `redis` | Live frames and hints only; nothing durable |

## Measured numbers

| What | Number | Source |
|---|---|---|
| Idle memory, `eval` stack, harness off | 5,260 MiB in all; app container 3,577 MiB | [footprint-baseline.md](../footprint-baseline.md) (Apple M4, 2026-09-26) |
| Idle memory, `eval` stack, harness on | ≈ 5,880 MiB in all; app container 4,199 MiB | same |
| Idle harness worker | 330 MiB RSS (292 MiB anonymous) after start and 2 minutes idle, `run_worker` on the host, without C-17's warm-up. In the image the warmed worker is 625 MiB idle (R26-FU); the budget is 650 MB for a warmed worker (SLO #16), and the worker's run-slot formula takes it as its base. P0.9 measured 667 MiB PSS / 852 MiB RSS for the worker built on `QueryAppContainer`, before `HarnessServices` | WP-88 (the project tracker "WP-88 evidence"); SLO #16 in [SLO.md](../SLO.md) |
| Journal commit, Node thread cost | about 4 ms a commit, 9 ms an admission; one process saturates near 62 commits/s of the corpus turn | [capacity-baseline.md](../capacity-baseline.md), "One Node process (measured)" (PERF-C) |
| Warm-pool session ready (p50 / p95) | opencode 1,373 / 1,493 ms; Codex 277 / 351 ms; Claude Code 304 / 369 ms | the project tracker C7.01 (PF-01) |
| Cold session ready (p50 / p95) | opencode 4,588 / 7,956 ms; Codex 1,474 / 2,424 ms | the project tracker C7.02 (PF-02) |
| Resume from snapshot, cold (p50 / p95) | 2,814 / 3,637 ms ready | the project tracker C7.06 (PF-03) |
| Model Gateway added latency (p95) | 7.6 ms chat, 9.7 ms Messages, 9.0 ms Responses | the project tracker C7.05 (PF-07) |
| Tool Gateway overhead at 40 calls/s (p50 / p95) | 34.2 / 47.6, 31.9 / 48.3, 34.1 / 46.1 ms in three quiet-host runs; saturation near 90 calls/s, queueing from about 55–60 calls/s | the project tracker DP8-5, C7.04 (PF-05) |
| 200 new conversations in 60 s, one Node and one worker | admission p95 49–53 ms, a worker slot within 35 ms p95, the corpus turn done in 0.94 s p95; none lost | the project tracker C7.12 (PF-15), "WP-88 evidence" |
| 200 new conversations at once, one Node and one worker | all 200 admitted (p95 2.2 s: one Node admits them one after another), queued and run at 11.4 runs/s (about 80 commits/s of the corpus turn), none lost; the last done after 17.6 s | same |
| Event lag worker → browser (p95) | 42 ms | the project tracker C7.07 (PF-08) |

## Projected journal load

From [capacity-baseline.md](../capacity-baseline.md) (a model over the replay corpus, with 3 s model calls
assumed): N runs in flight at once.

| Profile | N = 1 | N = 4 | N = 16 | N = 64 |
|---|---:|---:|---:|---:|
| Corpus mean, commits/s | 0.6 | 2.5 | 10.0 | 40.1 |
| Corpus mean, journal MB/hour | 4.2 | 16.8 | 67.2 | 268.6 |
| Corpus mean, share of one Node process | 1% | 4% | 16% | 65% |
| Busiest recording, share of one Node process | 2% | 7% | 29% | 118% |
| Code mode, 5,000 tool calls, journal MB/hour | 400.9 | 1,603.5 | 6,413.9 | 25,655.7 |

Read it as: at 16 ordinary runs at once one Node process is about a sixth busy and MongoDB's journal grows about
67 MB an hour, while code-mode runs with thousands of tool calls are what fill a disk. Size MongoDB's volume for
the session retention you keep (no expiry by default; FLAGS.md, `session_retention`), not for one day.

## How the code shares a host out

These are the limits the services apply on their own; they are constants of the code, so they hold on any host.

**Worker run slots** (`run_slots` in `backend/python/app/harness/worker/claimer.py`): the number of runs one
worker drives at once is

```
min(64, (container memory MiB − 350) ÷ 64, container CPUs × 16), at least 1
```

read from the worker's cgroup. Compose gives the app container `APP_MEMORY_LIMIT` (10G by default), so a worker
on four or more CPUs has 64 slots; on Helm, the app pod's memory and CPU limits decide. A run in a slot is mostly
waiting on its model, which is why a slot is cheap. `AGENT_HARNESS_PREEMPT_OVERCOMMIT` (default 2) lets
interactive runs borrow slots from scheduled ones.

**Sandboxes per host** (the broker, `backend/python/harness_core/sandbox/pool.py`): the host holds
`floor(host memory × 0.75 ÷ the largest sandbox's memory)` sandboxes, keeping a quarter of memory for the app,
the stores and Docker, and never more than `SANDBOX_MAX_TOTAL` (50 by default; 0 or less is no ceiling). A sandbox
gets 1 CPU, 1,024 MiB of memory, 256 processes and 512 MiB of scratch (`Resources` in
`backend/python/harness_core/sandbox/types.py`), except that opencode sandboxes get 2 CPUs (its `cpus` in
`backend/python/harness_engines/allowlist.json`) and Qwen Code sandboxes at least
3,072 MiB (its `minMemoryMb`: V8's tier-up of its shell parser peaks near 1.9 GB, R23PERF-FU-2). The broker counts
each live sandbox by its own memory, so a Qwen Code sandbox takes 3 GiB and leaves the 1 GiB engines their count
(R23FU-FU-1). A host that is out of slots refuses
the next sandbox rather than overcommitting memory ([runbooks: exhausted pool](runbooks.md#exhausted-pool)).

**Warm pool** (`SANDBOX_WARM_POOL_MAX`, 0 by default: off). Warm sandboxes cut a session's start from the cold
figures to the warm ones above, at the cost of holding their memory while idle. The pool fills at most a quarter
of the memory sandboxes may use (never what runs hold), warms only the images of engines installed here, and
leaves an org at most half of it while another org is waiting.

## Worked examples (projections, not measurements)

| Host | Harness processes | Sandboxes the broker allows | What to expect |
|---|---|---|---|
| Laptop, 16 GB Docker VM, `eval` profile | Harness on costs about 0.6 GB idle over harness off | `floor(16,384 × 0.75 ÷ 1,024)` = 12, but the stack already uses about 6 GB | One or two engine sessions at a time. Keep the warm pool off. The release keeps coding engines off by default (`HARNESS_ENGINE_RUNS`) |
| One Compose host, 8 vCPU / 32 GB | One Node, one worker (64 slots), Model Gateway | 24 by memory | Chat runs are bounded by the model provider and Node, not by slots. Engine sessions are CPU-bound first: eight 1-CPU sandboxes (or four opencode ones) fill the CPUs before memory runs out. Start with `SANDBOX_MAX_TOTAL` near the CPU count and raise it while watching CPU |
| One Compose host, 32 vCPU / 128 GB | as above | 96 by memory, 50 by `SANDBOX_MAX_TOTAL` | Raise `SANDBOX_MAX_TOTAL` only after watching Node: one Node process is the first shared bottleneck near 62 commits/s |
| Helm, several app pods | One Node and one worker per pod; the HPA scales on Node's event-loop use (`deployment/helm/pipeshub-ai/templates/hpa.yaml`) | per sandbox node | Add app pods for commit rate; use a three-member MongoDB set (`values-cloud.yaml`, `values-eks.yaml`). The sandbox broker is one replica by design |

For the knowledge side (vector store and indexing), see
[vector-db-sizing.md](../../deployment/vector-db-sizing.md) and
[qdrant-search-tuning.md](../../deployment/qdrant-search-tuning.md).

## Watching headroom

The monitoring overlay (`deployment/docker-compose/docker-compose.monitoring.yml`, rules in
`deployment/monitoring/prometheus/agent-harness.rules.yml`) carries the signals to size by:

- `HarnessClaimLatencyHigh` (`pipeshub_agent_harness_run_claim_wait_seconds`, p95 over 5 s): runs wait for a worker. Add workers
  (Helm app pods) or look at Node.
- `HarnessWarmPoolEmptyClaimsCold`: sessions start cold. Raise `SANDBOX_WARM_POOL_MAX` if memory allows.
- `HarnessGatewayErrorRatioHigh`: the gateways fail calls. See [runbooks: gateway 5xx](runbooks.md#gateway-5xx-spike).
- CPU of the Docker host while engine sessions run, and MongoDB's disk.

## Backup and restore

Backups and restores (OP-06) are in the operator guide, [Backup and restore](operator-guide.md#backup-and-restore).
