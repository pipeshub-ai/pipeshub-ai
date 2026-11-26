# ADR-SEC-03: One flow decision for every way out, and one floor for every sandbox

- Amends: WP-63 (allowed websites) and its rule that a private read keeps read sites open; SITEUP (the upload card); FG2 (`fetch_url` is a send).
- Related: ADR-GRANT-01 (conversation grants), ADR-OWD-15 (classification extension point), ADR-OWD-03 (isolation); findings SEC-A-01..04 (R37-SECA).
- Package: R38-EGRESS, 2026-11-13.

## Context

R37-SECA found two places where one policy question had two answers, and two places where one guard was weaker than its twin:

- **SEC-A-01.** The flow rule holds `fetch_url` after a private read because a URL carries data. The egress proxy let the same GET leave the sandbox's shell, since a "read" there meant "no body". Relay hosts made it worse: Go's module proxy fetches whatever origin the module path names, so a GET to an allowed registry reached an attacker's server.
- **SEC-A-02.** The proxy's narrowing was a snapshot taken when the turn bound its sandbox. A private read in the middle of the turn changed the ledger, not the binding, so the rest of the turn could still upload.
- **SEC-A-03.** Disk was the one resource the broker bounded by polling. Pids, memory, CPU and tmpfs are kernel limits.
- **SEC-A-04.** The broker's policy says what a sandbox must have. The legacy socket proxy only said what it must not have, so it admitted a run with no pids limit, a writable root and default capabilities, from any caller on its network.

## Decision

### D1. One flow decision for every way out (SEC-A-01)

After a private, untrusted or classified read (`holds_sends`, or a level whose `egress` is `ask`), every allowed website is **held**, whatever its access. This is the same narrowing at bind time and live (`harness_core.sandbox.egress.sites.held_for_flow`):

- A held site answers a write with `site_upload_held` and any read with `site_request_held`. A GET's path and query are data, as `fetch_url`'s URL is.
- A level that forbids web egress keeps no site. A host-level tunnel, which the proxy cannot hold, is dropped, and an open one is cut when a live hold lands.
- The person decides on the existing site card (tool `pipeshub@egress/site_upload`, rule `site_upload`), which now offers "allow for this conversation" like a `fetch_url` card. A grant is per host and per sensitivity, through `ConversationGrants`, so it answers the conversation's other runs.
- A read-write site is asked about when the turn starts, because the admin chose it as an upload target. A read site is asked about the first time the proxy holds a request to it (its `gateway.refused`), so a list of docs sites raises no cards that nobody needs.
- The refusal's body tells the engine what happened and what to do: the person has been asked, the run can go on without the site, and the same request works once they allow it.

**Why not drop read sites instead?** Dropping would also stop the docs a run needs after it looks something up. Holding with a card keeps the person in control and costs them one click per site.

### D2. Registries stay open; relays are write-capable (SEC-A-01 b, c)

The classification is release data in `contracts/harness/core/policy/website-rules.json`, generated into every target:

- **`registryHosts`.** These hosts serve fixed files from their own storage and fetch nothing on a request's behalf: npm, PyPI and its file store, crates.io and its index and file store, Maven Central under both its names, and RubyGems with its compact index. Each entry says why. After a private read, a read here still goes out, but only as a path with no query string (`fixed_content_read`), so package installs keep working.
  - Residual risk: the path reaches the registry's own logs, and an attacker who publishes packages can watch their download counters. The bandwidth is low, and nothing reaches the attacker's server.
- **`relayHosts`.** These hosts fetch any address for whoever asks: Go's module proxy and checksum database, the GOPROXY mirrors, CORS proxies, `r.jina.ai`, `*.translate.goog`, `web.archive.org` and URL shorteners. Or they let anyone read the requests sent to them: webhook catchers, tunnels, `*.workers.dev` and `*.pages.dev`. Every request to one is an upload, whatever its method (`site_use`).
  - A read-only entry for a relay is dropped by the worker and refused by Node's save check (`site_relay` if one is ever bound anyway).
  - A relay that is also a read-only host is never allowed at all.
  - Go needs explicit allow. An admin lists `proxy.golang.org` read-write (with the confirmation), so every request to it is held after a private read. Or the deployment points Go at an internal mirror (`EGRESS_PRIVATE_ALLOW`, `GOPROXY` with `GOFLAGS=-mod=mod`).
- Every preset host must be a registry, and no registry may be a relay. `test_sec_a_01_every_preset_host_is_classified_and_no_relay_is_a_registry` and Node's `r38egress-relay-sites.test.ts` hold this.

**Signed or compiled?** `vendor_hosts.json` is signed because it is read at run time from a packaged file. Website rules are compiled into Python, Node and the frontend by the codegen, and `--check` holds the generated code to the JSON. A deployment cannot change them without building another image, and a signature would protect nothing more. The release key also stays out of the tree.

### D3. The flow rule reaches the live sandbox before the engine reads the result (SEC-A-02)

The kernel's tracking endpoint calls the runtime's `egress_flow` after every gateway call, before the result is returned (`_TrackingEndpoint._narrowed`). The worker's `LiveFlowHold` compares what the conversation holds now with what the sandbox was bound under.

- When the conversation holds more, the worker tells the proxy at once: `POST /v1/flow-holds {sandboxId, runId, hold}`. The proxy applies the same `held_for_flow` to the live binding and registers it again. A durable binding table writes it through. It is not a re-bind, so tokens and site grants stay. Then it cuts any host-level tunnel the sandbox has open.
- The route only narrows. It refuses a sandbox that serves another run, and it is idempotent.
- **Fail closed.** If the proxy cannot be told, the call's result is withheld (`egress_rebind_failed`), and the engine reads that it may retry. The run does not end.
- A classification raise mid-turn goes the same way (`forbidden`).
- **Residual risk, closed (R49-SMALL, R38EGRESS-FU-1).** A proxy that restarts with unreadable sealed state gets its bindings back from the broker's copy. The hold now goes through the sandbox's broker (`POST /v1/sandboxes/{id}/flow-hold`), which narrows its copy before it tells the proxy, so the resync gives the narrowed binding back. A sandbox whose broker cannot take it (no egress proxy behind it, an older broker answering 404) is held through the proxy's control plane as before, with that residual.

### D4. The kernel caps how fast a sandbox writes the disk (SEC-A-03)

When the deployment names the block device under Docker's volumes, every broker sandbox gets `BlkioDeviceWriteBps` (cgroup `io.max`).

- `SANDBOX_DATA_DEVICE` names that device. `install.sh` finds it: a partition becomes its whole disk, which the kernel requires, and a device-mapper device is kept as it is.
- `SANDBOX_WRITE_MBPS` sets the rate, 32 MiB/s by default.
- How far a workspace can run past its quota between two measures is then the rate times the guard's interval: 320 MiB at the defaults. The broker logs the cap at start (`writeMbps`).
- Proven on runc and on gVisor: 48 MiB of fsynced writes take about 4.8 s at 8 MiB/s, against 0.47 s and 1.52 s uncapped.
- The quota guard stays the person's explanation: `workspace_quota_exceeded`, "the workspace went over its N MB limit".

**A hard size per workspace** needs a quota-capable filesystem under Docker's volumes. Docker's local driver accepts `size=` only on XFS with `pquota`, and the common ext4 setup refuses it ("quota size requested but no quota support"). A loop-mounted volume needs root on the host, or a privileged helper, which would be worse than the risk. Both need the owner (root on the host). The size-capped per-workspace volume is R38EGRESS-FU-2.

### D5. One floor for every sandbox, whoever creates it (SEC-A-04)

`app/sandbox/sandbox_floor.py` is stdlib only, and both socket holders import it:

- the broker's `HardeningPolicy`;
- the legacy proxy (`DockerApiPolicy.check_container_create`).

The floor is: pids, memory with no swap, and CPU limits; a read-only root; `CapDrop: [ALL]`; `no-new-privileges`; and a non-root user.

- **One root container.** The only root container with capabilities is the legacy egress firewall's install step. Its script is checked line by line against the grammar `egress_firewall` writes, and it must drop to one non-root user before `"$@"`. `NET_RAW` is no longer admitted.
- **Read-only root.** The legacy executors now run on a read-only root. Their writable directories (`/src`, `/output`, `/install`, `/deps`, `/node_modules`, `/home`) are anonymous volumes. The proxy refuses a named or driver-configured one, and on every `DELETE` it forces `v=1`, so the volumes go with their container. The sandbox image owns `/deps` and `/node_modules`.
- **Caller credential.** Every call but `/_ping` and `/version` carries the caller's credential: one derived from `SECRET_KEY` by HMAC, which the compose files give both the app and the proxy, so an upgrade needs no new secret (`DOCKER_PROXY_TOKEN`, set for both, overrides it). The proxy will not start without one.

### D6. The legacy mode is scheduled for removal

`SANDBOX_MODE=docker` exists only while an install moves to the broker, and `install.sh` already moves it once the broker is healthy.

- `docker-compose.sandbox-legacy.yml`, the legacy executors and the socket proxy are removed in the first release after 2027-01-31.
- They go sooner if the deployment health reports show no install still on `SANDBOX_MODE=docker`.
- Tracked as R38EGRESS-FU-3. Until then, D5 holds legacy sandboxes to the broker's floor.

## As built

R38-EGRESS on `harness/v2-r38egress`: `harness_core/sandbox/egress/sites.py` (`held_for_flow`, `fixed_content_read`, `relay_host`), `app/harness/websites.py` (`RunSites.facts`, `LiveFlowHold`), `app/harness/site_uploads.py` (cards on demand, conversation grants), the proxy's `POST /v1/flow-holds`, the kernel's `_TrackingEndpoint._narrowed`, `WriteBound` in the broker, `app/sandbox/sandbox_floor.py`, and the legacy proxy and executors. Tests: `test_sec_a_sandbox_egress.py`, `test_siteup_upload_card.py`, `test_siteup_upload_docker.py` (real proxy, gVisor), `test_sec_iso_exhaustion_docker.py` (write cap, runc and gVisor), `test_docker_proxy*.py`, Node `r38egress-relay-sites.test.ts`.

Consequences:

- A conversation that read private data asks once per site before the sandbox reaches it. Package installs from the release's registries do not ask.
- A relay can no longer be a read-only site. Existing read-only entries for relay hosts are dropped (logged `websites.site_dropped`) and refused at the next save, with a reason that says what to do.
- Every gateway call of a run with websites may make one control-plane call: once per sandbox and hold level, not per call.
- Legacy installs need the sandbox image of this release, which has `/deps` and `/node_modules`, and both containers need the same `SECRET_KEY`, which the compose files pass to both.

## Deviations

- SEC-A-03's hard per-workspace size is not built: it needs a quota-capable filesystem under Docker's volumes (D4).
- The journey runner has no engine shell step yet, so `sites.flow_hold` cells are tracked as gap R38EGRESS-FU-4. The proxy half runs on the real lane.
- D5's caller credential is no longer derived from `SECRET_KEY`: the proxy beside the Docker socket holds no root key (ADR-KEY-02 D6, R38-IDENT). `DOCKER_PROXY_TOKEN` is generated by `install.sh` and required by Compose for the app and the proxy.

## Owner amendments

- 2026-11-19: PipesHub's own `web_search` and `fetch_url` always run; the flow rule no longer holds them
  (`flow=platform_web`, `harness_core.effects.flow.flow_holds`; `docs/agent-harness-v2/impl/tool-effects.md`). The
  rest of D1 stands: a held website in the sandbox, an open-world MCP read and every external write are still held
  after a private read, and an approval layer's own rule for a web tool still asks.
- 2026-11-19, the same day: a page fetch's address is the model's choice and could carry what the run read to any
  public host, so the exemption is narrowed for `fetch_url`. Web search stays free: its query reaches only the
  configured search provider. A fetch (`flow=opens_address`) runs for an address the run was given: a web search
  result or page the session's journal recorded (the provenance ledger's web pages, so a resume or a later turn keeps
  them), a link the citation ref mapper holds (a record's or item's own link), or the person's own words (the message,
  their earlier messages, a message sent while the run worked, an attached file's text; never a child's goal, which
  its parent model wrote). Once any flow rule would hold a send (`FlowRule.holds`), a fetch to any other address is
  refused, never asked: `tool.denied{rule: web_address}`, a plain reason the model reads, and the activity says so.
  Nothing that cannot be checked is vouched for (`unvouched_address`). Matching is exact after a normalisation that
  leaves the model nothing to choose (scheme and host case, default port, fragment, one trailing slash); links inside
  a record, an item or a fetched page do not count, since a run can write such content and read it back. A redirect
  needs no check: the address given was exact, so the hop's target is the server's. An approval layer's ask for
  `fetch_url` never turns that refusal into a card: layers only narrow. Implemented in
  `backend/python/app/web/addresses.py`, `harness_core.effects.flow` and today's loop's flow guard; proven by
  `backend/python/tests/unit/harness/test_web_addresses.py`, `test_web_always_allowed.py` and the policy table
  (PT-708..PT-723).
- 2026-11-19, later: a link found in a page the run opened, or in a web search result's snippet, counts as given too.
  A page's links are its author's, fixed before the run read anything private, so following one exactly carries
  nothing the run read. The links are noted as each result arrives (at most 200 per page or search, 200 being a page's
  navigation and references, and 5,000 per session, first seen first) and journaled beside the recorded web pages
  (`x.pipeshub.citations.recorded` `webLinks`), so a resumed attempt and a later turn keep them. Links inside records
  and connector items still count only through the item's own link. The one refusal left is an address the model
  wrote itself after a private or unknown read, still with no card. And no rule layer here asks about web search or
  page opening or refuses them, the deployment's own rules and a class rule for reads included
  (`app.harness.approvals.PlatformWebRuns`, the default of `edition_config.layer_ask_answers`): search always runs.
  Proven by `test_web_addresses.py` (followed links on the native loop, after a resume, the bounds) and the policy
  table (PT-704..PT-707, PT-724..PT-731).

## Status

**Accepted**, 2026-11-13 (R38-EGRESS). SEC-A-01, 02 and 04 are done. SEC-A-03 is partial until R38EGRESS-FU-2.
