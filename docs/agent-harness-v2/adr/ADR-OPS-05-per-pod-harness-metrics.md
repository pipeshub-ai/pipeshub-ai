# ADR-OPS-05: Each replica's harness metrics are scraped in its own pod

- Finding: R41SUPPLY-FU-13 (with several Node replicas behind Helm, Node's counters on the merged metrics route
  alternate between replicas per scrape).
- Related: ADR-OPS-03 (security signals, the merged route), C9.04/C9.05 (the Compose monitoring overlay), SEC-C-10
  (the scraper's own credential), ADR-OPS-04 (Helm network policy).

## Context

Node's merged route (`GET /api/v1/agent-harness/internal/metrics`) carries Node's own counters
(`pipeshub_auth_attempts_total`, the token rejections, approvals, kill switch), the gateways' and the broker's
families, and the run store's gauges. Each Node process counts on its own. Read through a Service address, every
scrape lands on some replica, so a counter's single series jumps between two replicas' values: `rate()` sees resets
and the alerts in `agent-harness.rules.yml` misfire or stay silent.

Looking for the per-pod fix showed the root of it: in Helm the route was not scrapeable at all. It lives on Node's
internal listener, which the chart binds to `127.0.0.1` (the worker-only routes, journal, leases and gateway tokens,
share it), and it takes a scrape credential Node keeps in the KV store and rotates, writing it to
`HARNESS_METRICS_CREDENTIAL_FILE` for a Prometheus that shares the volume. A ServiceMonitor or pod annotations would
need the port on the pod network and the rotating credential in a Secret the cluster's Prometheus can read: two new
exposures to fix a counting problem.

## Decision

1. **Scrape inside the pod.** With `metrics.harness.enabled`, every app pod runs a Prometheus agent sidecar
   (`prom/prometheus` by digest, agent mode, the image Compose's overlay already uses). It scrapes `127.0.0.1:3002`
   with `credentials_file`, which Node writes to a memory-backed `emptyDir` only Node and the agent mount. The agent
   runs as Node's user (the file is `0600`; `HARNESS_METRICS_CREDENTIAL_OWNER` is that uid, so Node's `chown` needs no
   capability), its root filesystem is read-only and its own web port is on loopback.
2. **Label the pod, push out.** `external_labels` carry `pod` and `namespace` from the downward API, and the agent
   remote-writes to `metrics.harness.remoteWrite.url` (required: the chart refuses the switch without it). Two
   replicas are two series; the rules already add counters with `sum` and read gauges with `max`, which is right
   both for per-pod series and for shared ones (the sandbox broker's and the run store's appear once per pod).
3. **The process metrics port is per pod too.** `metrics.node.port` (event-loop utilization, the HPA signal) gets
   `prometheus.io/scrape|port|path` pod annotations, and the README says to scrape pods or a Service's endpoints,
   never the Service address.
4. **Network policy.** `metrics.harness.remoteWrite.peers` (with `port`) adds one egress rule from the app pods to an
   in-cluster receiver; an external receiver is reached through the existing external egress rule.
5. **Off by default.** It needs a receiver the chart does not run. Compose is one replica and keeps its overlay.

### Alternatives not taken

- **Aggregate in Node** (share counters through Redis, or have one replica scrape the others): a KV write on every
  sign-in attempt, and the merged route would answer for processes it did not run.
- **A per-replica label on the merged route, still read through the Service**: each series would update only when
  the load balancer happened to pick its replica, and go stale in between.
- **Publish port 3002 to a headless Service with a ServiceMonitor**: puts the worker-only routes on the pod network
  and the rotating credential in a Secret.

## As built

R43-SUPPLY on `harness/v2-r43supply`: `templates/harness-metrics-agent.yaml` (agent config), `templates/deployment.yaml`
(sidecar, volumes, Node's credential-file env, pod annotations), `templates/networkpolicy.yaml`, `values.yaml`
(`metrics.harness`), `README.md` (Metrics). Tests: `deployment/helm/tests/test_harness_metrics.py` (HM-1..5) and the
`harness-metrics` variant plus a refusal in `check_chart.sh`.

## Deviations

- Not run on a cluster (no Kubernetes here): the agent's remote write and a real two-replica series split wait on the
  same kind run as R41SUPPLY-FU-4 (R43SUPPLY-FU-3).

## Owner amendments

None.

## Status

**Accepted** 2026-11-13: decided from first principles, long term.
