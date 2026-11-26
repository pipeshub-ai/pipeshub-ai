# Agent harness: security whitepaper

Checklist item C18.08. This paper describes how PipesHub's agent harness keeps agents, and the coding engines they
drive, inside their bounds: isolation, egress, credentials, the Rule of Two and the flow guard, data
classification, audit, and what we learned from a supply-chain incident. Each claim names the code or the test
that proves it. Where a control is not built or not yet tested, the paper says so. The threats these controls
answer, and their open gaps, are in the [threat model](threat-model.md).

## Summary

- Agent code runs in a sandbox that has no secrets, no internet and no Docker socket. It reaches PipesHub only
  through two gateways, behind an egress proxy that holds the real credentials.
- A run that has read private data cannot send data out without a person's approval (the Rule of Two, enforced by
  the flow guard).
- Every audit-relevant event is hash-chained per org and anchored, so a changed or missing record shows.
- **Deferred:** the release requires gVisor for coding engines, but no test host has `runsc` yet, so every isolation
  and egress test so far ran on hardened runc. Kata and a Helm agent-sandbox are not built. Both are the last step
  of the project.

## Isolation

**One process may start sandboxes.** The sandbox broker (`backend/python/app/sandbox_broker_main.py`) starts every
sandbox, and reaches Docker only through the Docker API policy proxy, the only container with the Docker socket, which
admits only the broker's calls on its own install's sandboxes:
`backend/python/tests/cohesion/test_c12_sandbox_plane.py::test_c12_no_service_but_the_policy_proxy_mounts_the_docker_socket`,
`backend/python/tests/unit/sandbox/test_docker_proxy_broker.py`.
The worker asks the broker for a sandbox; it cannot create containers itself.

**Every create body is checked.** The broker refuses to create a container unless it drops every capability, sets
`no-new-privileges`, has a read-only root file system, is not privileged, publishes no port, has private IPC and
cgroup namespaces, and has positive PID, memory and CPU limits with no swap
(`_host_violations` in `backend/python/app/harness/sandbox/docker_policy.py`). The only runtimes it accepts are runc
and runsc, and a create request carries no OCI annotation: a caller cannot reach gVisor's or Kata's configuration.
The sandbox user is `1000:1000` (`backend/python/app/harness/sandbox/docker_broker.py`). There is no custom seccomp
profile: Docker's default profile applies under runc; under gVisor the Sentry answers every syscall.

**The checks hold against a live container.**
`backend/python/tests/unit/harness/sandbox_broker/test_sec_iso_escape_docker.py::test_sec_iso_01_07_escape_probes_end_where_the_hardening_says`
probes, on runc and on gVisor, capabilities, seccomp, the read-only root, `/proc/sys`, `/sys`, mount, unshare, ptrace, module loading,
`mknod`, chroot and the Docker socket from inside a sandbox.
`backend/python/tests/unit/harness/sandbox_broker/test_sec_iso_exhaustion_docker.py` shows a fork bomb, a memory
balloon, disk, inode and descriptor fills and a CPU spin end at their limits and touch only their own sandbox, on both
runtimes.

**Engine binaries are pinned.** Each engine version is listed with its binary's sha256 in
`backend/python/harness_engines/allowlist.json`, and the adapter verifies the digest before it starts the engine
(`verify_binary` in `backend/python/harness_core/engines/binary.py`).

**gVisor.** The release floor for coding engines is gVisor (`engineProfile.isolationMin` in
`contracts/harness/core/policy/release.json`). Without `runsc`, an engine run is refused
(`backend/python/tests/unit/harness_core/engines/test_gvisor_floor.py::test_gv1_production_without_runsc_refuses_the_engine_with_the_install_guide`).
`PIPESHUB_ENGINES_WITHOUT_GVISOR=true` lowers the floor to hardened runc for development and tests and shows admins
a warning ([GVISOR.md](../../../deployment/docker-compose/GVISOR.md)).

**What is deferred.** No test has run on runsc: every `[gvisor]` case is skipped, and the runsc mapping is proven only
against the request sent to Docker (X3). The microVM tier (Kata) and the Helm
agent-sandbox (P5.3) are not built; Helm turns coding engines off
(`backend/python/tests/unit/harness/recs_audit/test_r2_r6_isolation_and_protocols.py::test_r2_helm_has_no_agent_sandbox_or_kata_yet`).
the project tracker rows C6.01, C2.15, C1.04, C6.05 and C20.39 track them.

## Egress

**Default deny, no DNS.** Each sandbox gets its own internal Docker network with the egress proxy as its only peer.
There is no DNS server; only the names of the proxy and the gateways resolve. The proxy identifies a sandbox by the
connection's addresses, never by anything the sandbox sends (P5.4 §1.1; the process
is `backend/python/app/egress_proxy_main.py`).

**Only the gateways.** The proxy forwards to an exact host-and-port list, refuses private, link-local, CGNAT and ULA
addresses, and refuses a request whose placeholder is missing, belongs to another sandbox or names the wrong
destination (`backend/python/harness_core/sandbox/egress/policy.py`;
`backend/python/tests/unit/harness_core/sandbox/egress/test_policy.py`). Against real Docker,
`backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py` shows a sandbox cannot reach the
internet by address or name, the cloud metadata address, other containers, the host, another sandbox, the control
plane or any internal store, and has no UDP, ICMP, raw socket, IPv6, DNS-over-HTTPS or DNS-over-TLS way out.
`backend/python/tests/cohesion/test_c13_sandbox_reaches_only_gateways.py::test_c13_a_sandbox_reaches_tools_and_models_only_through_the_gateways_and_holds_no_secret`
runs the whole path. These ran on runc; their gVisor run (SEC-14) has not.

**Doors an admin opens.** Allowed websites are enforced by the proxy
(`backend/python/harness_core/sandbox/egress/sites.py`). Host-level HTTPS tunnels exist only with the
`docker-compose.egress-tunnels.yml` overlay; they refuse unlisted hosts, IP literals and names that resolve to
private addresses (`backend/python/tests/unit/harness/sandbox_broker/test_egress_tunnel_docker.py`). Git hosts are
never reachable from a sandbox: the worker clones outside it after a person approves
(`backend/python/tests/unit/harness/test_gitf_repository_fetch.py`). A clone from a public address with no credential
is public content to the flow rule, as a fetched web page is; a mirror the deployment opened with
`EGRESS_PRIVATE_ALLOW` is content of unknown origin, and a private read after either still holds every send
(`backend/python/tests/unit/harness/test_wp83_public_clones.py`).
A public address is not enough on its own: a host that answers only the organization's own callers (an
IP-allowlisted intranet, a self-hosted git server) serves private material. PipesHub never probes an address from
outside to find out, because the probe would send every URL a run reads, tokens included, to a third party. The admin
declares instead: **addresses only your organization can reach** (`websites.orgOnlyAddresses`: names, `*.` domains,
addresses and CIDR ranges) make a page or clone from them unknown, and a git host added by hand is not public until
the admin switches on **anyone can read its public repositories** (`publicReadable`; a preset's host is). One
classifier answers for `fetch_url` and `repository_fetch` (`backend/python/harness_core/security/reachability.py`),
and anything it cannot clear stays unknown, so a later send outside asks
(`backend/python/tests/unit/harness/test_wp83_fu3_org_only.py`, vector `contracts/harness/vectors/org-only-addresses.json`).

**One exception to "no interception".** For package registries and databases that need a credential, the proxy
ends TLS with its own per-deployment CA and injects a credential sealed with AES-256-GCM
(P5.4 §10; `backend/python/tests/unit/harness_core/sandbox/egress/test_wp06_injection.py`).
The sandbox therefore holds that CA's certificate, which is public. It never holds the credential.

## Credentials

**No secret in a sandbox.** Sandboxes are created with no environment of PipesHub's. Each attempt gets HMAC
placeholders that the proxy swaps for a run-bound token on the way to a gateway
(recs audit, rec 1). A canary scan of the sandbox's files and environment
finds nothing: `test_c13_a_sandbox_reaches_tools_and_models_only_through_the_gateways_and_holds_no_secret` and
`backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py` (SEC-11: the gateway gets the
token, the sandbox never holds it).

**Stored credentials.** Connector and tool secrets are encrypted with AES-256-GCM under versioned data keys, and the
keyring is encrypted again under the deployment's secret (`backend/python/app/harness/credentials/vault.py`;
`backend/python/tests/unit/harness/credentials/test_vault.py`, and `test_leak.py` for leaks into logs). Replacing a
key rotates it; removing one revokes it at the next call (P2.4b).

**Per-org encryption of run content.** Journal content is envelope-encrypted with a data key per org, wrapped by a
key from the platform secret (self-hosted) or a KMS (managed)
(`backend/nodejs/apps/src/modules/agent-harness/ports/data-keys.ts`;
`backend/nodejs/apps/tests/modules/agent-harness/data-protection.test.ts`). Deleting an org destroys its data keys,
so its content can no longer be read (`backend/nodejs/apps/tests/modules/agent-harness/principal-erasure.test.ts`,
CLEANUP-G1).

**Personal access tokens** carry the user and org, are checked per route, and are never logged. **Gap:** revoking a
disabled user's tokens has no test yet (the project tracker row C6.24).

## The Rule of Two and the flow guard

An agent that reads untrusted content, holds private data and can act outside is the "lethal trifecta". The Rule of
Two says a run may hold at most two of the three without a person in the loop. The flow guard enforces a stricter
form: after a run has read anything above the threshold (by default anything not public), a call that sends data
out waits for a person, or is refused (`FlowRule.decide` in `backend/python/harness_core/effects/flow.py`). Every
tool result counts as read, and a read that sends data somewhere (an open-world MCP read) counts as sending out
(FG2). PipesHub's own web search is not held: its query goes only to the configured search provider. Its page fetch is
not held either, but once a send would be, it opens only an address the run was given (a search result, a page the
session recorded or a link in one, a record's link, the person's message or attached file), matched exactly up to scheme and host case,
a default port, a fragment and one trailing slash; an address only the model wrote is refused with no card, so it
cannot carry what the run read to a host of the model's choosing (`backend/python/app/web/addresses.py`,
`backend/python/tests/unit/harness/test_web_addresses.py`).

It is on by default (`HARNESS_FLOW_GUARD`, `backend/python/tests/unit/harness/test_ux1_flow_default.py`). An org rule
applies even when the deployment switch is off, and the stricter of the two wins. Proven by
`backend/python/tests/unit/harness_core/effects/test_flow.py`, `backend/python/tests/unit/harness/test_fg2_web_sinks.py`
and the policy table `backend/python/tests/unit/harness/policy/policy_table.yaml`. Code in the run's own sandbox, which
has no network, runs without asking; anything that can reach beyond it still asks
(`backend/python/tests/unit/harness_core/effects/test_codeauto_own_sandbox.py::test_ca4_after_a_private_read_an_external_send_still_asks_and_own_sandbox_code_does_not`).

## Data classification

Data classification is an extension point with no default in this build (`ClassificationPort`,
`backend/python/app/harness/classification.py`; `HarnessEdition.classification` in Node). With none, nothing is
classified: every model call is passed through, no level is recorded, the model gateway asks nothing, and the usual
approval rule decides every ask (`backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts`). A
run carries a classification only as an opaque value and asks whether one is configured
(`backend/python/harness_core/security/classification.py`;
`backend/python/tests/unit/harness/policy/test_classification_ports.py`).

## Audit

Every audit-relevant event (approvals and grants, tool calls by intent and outcome, downloads, configuration
changes) is chained per org with SHA-256; anchors are written every 15 minutes, to MongoDB, or wherever the
deployment's `HarnessEdition.auditAnchors` writes them (`backend/nodejs/apps/src/modules/agent-harness/domain/audit.ts`). A tool call is chained as a digest, never
its arguments, and no record holds content (`backend/nodejs/apps/tests/modules/agent-harness/audit-chain.test.ts`).
A verifier walks the chain and reports a changed record
(`backend/nodejs/apps/src/modules/agent-harness/application/audit-anchoring.ts`;
`backend/nodejs/apps/tests/modules/agent-harness/audit-chain.test.ts`). **Gap:** the app has no page or API that
searches or exports the chain.

## Tenant isolation

Every tenant-facing route has a row in `backend/nodejs/apps/tests/modules/agent-harness/support/cross-tenant-routes.ts`,
and a test fails when a route has no row
(`backend/nodejs/apps/tests/modules/agent-harness/qa26-route-inventory.test.ts`). The Mongo replay
(`backend/nodejs/apps/tests/modules/agent-harness/qa26-cross-tenant.mongo.itest.ts`) has org B replay every request
against org A's objects and checks each is refused, names nothing of org A and changes nothing.

## Supply chain: the litellm incident of October 2026

**What happened.** litellm 1.107.0 on PyPI carried a credential stealer: a `.pth` file that runs at every Python
start and posts the environment to an outside host. PipesHub does not depend on litellm directly; it came in
through `opik`, whose range admitted 1.107.0, so any fresh resolve after 2026-10-21 could install it. The version
was later removed from PyPI (release note).

**What we changed.**

- **Pin with an upper bound.** `litellm>=1.79.2,<1.107` in `backend/python/pyproject.toml` and
  `integration-tests/pyproject.toml`. An upper bound, rather than `!=1.107.0`, keeps the next release out until
  someone has looked at it.
- **Refuse compromised releases in tests.** `backend/python/tests/unit/test_dependency_security_floors.py` lists
  known-compromised releases and fails when one is installed, when a manifest admits it, when the two manifests
  disagree, or when the stealer's files are in `site-packages`.
- **Quarantine, don't clean.** An environment that may have run the bad release is set aside and never reused; a
  fresh one is built from the pinned manifests, and the credentials it could read are rotated. This is a
  practice, not a check in this repository: no test or script here enforces it, and the rotation is tracked
  outside the repository.
- **Hash-pin what runs in sandboxes.** The engine and skill images install from lock files with
  `--require-hashes` (for example `deployment/sandbox-images/codex/Dockerfile`).

**Lessons.** A transitive dependency is as trusted as a direct one, and a package's code can run at every Python
start whether or not anything calls it: the stealer needed only to be installed. So every resolve is pinned and
checked by a test, not by a person's memory. **Gap:** the Python backend has no lock file with hashes yet; its pin
is a version range.

## What is not claimed

- No external red team or penetration test has been done; the reviews so far are internal
  (H7, CPS-security).
- No engine or model has been tested against planted prompt-injection content; H7 measured the controls, not a
  model's behaviour.
- MCP server and skill registries are pinned by hash but not signed (the project tracker rows C6.34, C3.57).
