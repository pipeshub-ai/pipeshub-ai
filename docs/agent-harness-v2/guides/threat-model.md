# Agent harness: threat model

Checklist item C6.41. This is the threat model of the agent harness, reviewed at every phase gate. Its threats are
the rows of [design-plan.md §9](../design-plan.md#9-security-architecture-summary); for each one it records what is
built and which test proves it, and what is open. The [security whitepaper](security-whitepaper.md) explains the
controls; the security test scenarios are [test-scenarios.md §3](../test-scenarios.md#3-security).
`backend/python/tests/cohesion/test_v2_guides.py` fails when a §9 threat has no row here, or when the review record
below has an undated line.

## Assets and trust boundaries

- **Assets:** org data (knowledge, connector content, conversations), credentials (model keys, connector tokens,
  PATs), the audit chain, and the host.
- **Untrusted:** everything a model writes, every tool result and fetched page, repository content, MCP servers and
  their descriptions, uploaded files, and the coding engines themselves.
- **Boundaries:** the sandbox (no secrets, no network but the egress proxy); the egress proxy (the only way out);
  the tool and model gateways (every call checked, approved and recorded); the worker credential (only the worker
  reaches the harness state API); the org (every route checks the caller's org server-side).

## Threats

| Threat (design-plan §9) | Built, with proof | Open |
|---|---|---|
| Sandbox escape / host compromise | Hardened create bodies refused otherwise (`backend/python/app/harness/sandbox/docker_policy.py`); escape and exhaustion probes (`backend/python/tests/unit/harness/sandbox_broker/test_sec_iso_escape_docker.py`, `backend/python/tests/unit/harness/sandbox_broker/test_sec_iso_exhaustion_docker.py`); only the broker has the Docker socket (`backend/python/tests/cohesion/test_c12_sandbox_plane.py`); engine binaries pinned by sha256 (`backend/python/harness_engines/allowlist.json`) | gVisor never run (no `runsc` on a test host); Kata and the Helm agent-sandbox not built; Docker's default seccomp, no custom profile. Deferred to the end (C6.01, C6.05, C20.39) |
| Credential theft | No secret in a sandbox, placeholders swapped at the proxy, canary scan (`backend/python/tests/cohesion/test_c13_sandbox_reaches_only_gateways.py`); vault encryption and leak tests (`backend/python/tests/unit/harness/credentials/test_vault.py`); per-org data keys destroyed on org delete (`backend/nodejs/apps/tests/modules/agent-harness/principal-erasure.test.ts`) | Rotating the proxy's interception CA (C6.17); revoking a disabled user's PATs untested (C6.24) |
| Data exfiltration (lethal trifecta) | Default-deny egress, no DNS (`backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py`); the flow guard asks before sending out after a private read (`backend/python/harness_core/effects/flow.py`, `backend/python/tests/unit/harness_core/effects/test_flow.py`); remote images click-to-load (C6.27) | Data hidden in a link's path or subdomain is not flagged; egress tests ran on runc only |
| Indirect prompt injection (Slack/Confluence/web/repo content) | Untrusted labels on tool results (`backend/python/harness_core/security/labels.py`); the Rule of Two through the flow guard; repository instructions read as lower-trust content (`backend/python/harness_core/engines/repository_instructions.py`); a quarantined summarizer (`backend/python/tests/unit/harness_core/context/test_ga28_quarantined_summarizer.py`) | No engine or model tested against planted content; the H7 red-team drivers are not written (H7); C6.29, C6.30 |
| Confused deputy / ACL bypass | Principal from the session credential, never from the client; every tenant route in the cross-tenant table (`backend/nodejs/apps/tests/modules/agent-harness/qa26-route-inventory.test.ts`) and replayed across orgs (`backend/nodejs/apps/tests/modules/agent-harness/qa26-cross-tenant.mongo.itest.ts`) | The Mongo replay must be re-run green on the gate host before the PR |
| Malicious MCP server / tool poisoning / rug pull / shadowing | Admin allowlist; manifests pinned by hash, drift rejected (C3.46, `backend/python/tests/unit/harness/mcp_hub/test_hub_wiring.py`); namespaced tool names | No signed MCP registry (C6.34) |
| Malicious skill / plugin / repo config | Skills pinned by bundle digest; engine instruction files empty and read-only; repository config never loaded as configuration ([ADR-OWD-06](../adr/ADR-OWD-06-pipeshub-contract-and-ph.md)); no repository skill offered where the engine has a switch, and a repository's context files read as lower-trust content ([ADR-ENG-01](../adr/ADR-ENG-01-repository-skills-and-context-files.md)) | No signed skills registry (C3.57); Codex 0.160.0, Gemini CLI 0.63.0 and Goose 1.57.0 still list a repository's skills (R31ENG-FU-1, upstream) |
| Cross-tenant leakage | One sandbox per session per user; per-org data keys (`backend/nodejs/apps/tests/modules/agent-harness/data-protection.test.ts`); the cross-tenant replay | As for confused deputy |
| Cost / DoS abuse | Per-run USD and token caps (`backend/python/tests/unit/harness_core/budget/test_spendcont_caps.py`); per-user and per-org active-run and rate caps (`backend/nodejs/apps/tests/modules/agent-harness/admission-quotas.test.ts`); PID, memory and disk limits | C2.22 |
| Log / transcript leakage | Each egress refusal logged once without a secret (SEC-13 in `backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py`); audit records hold digests, never content (`backend/nodejs/apps/tests/modules/agent-harness/audit-chain.test.ts`); classification-based redaction | — |
| Harness CVEs (e.g. sandbox-bypass bugs in harnesses) | Engine versions and digests allow-listed; each engine passes the conformance kit on its real binary ([adapter guide](adding-a-harness-adapter.md)); the outer sandbox does not rely on the engine's own sandbox | Per-engine kill switch untested in flight (C6.35, C11.06); the Docker lane is path-filtered (C6.42) |

### Supply chain

Not a §9 row, but an observed incident: a compromised transitive dependency (litellm 1.107.0, October 2026). Controls:
the upper-bound pin and `backend/python/tests/unit/test_dependency_security_floors.py`; hash-pinned sandbox image
installs. Open: no hashed lock file for the Python backend. See the
[whitepaper](security-whitepaper.md#supply-chain-the-litellm-incident-of-october-2026).

## Review at every gate

Each release gate includes a threat-model review: the
reviewer reads the gate's diff against the table above, updates a row when a control or gap changed, adds a row
for a new threat, and adds a line below. A gate does not pass with the step missing.

The external red team (pen test) is a people item, done before general availability.

### Review record

| Date | Gate | Commit | Reviewer | Outcome |
|---|---|---|---|---|
| 2026-11-02 | Round 13 | `9a2180e1a2` | WP-74 | First written review. Every §9 threat has a built control with a test; the open items are the deferred gVisor/Kata tier, the unsigned MCP and skill registries, model-level injection testing, and the supply-chain lock file. No new threat from round 13: R13-ROOT made the sandbox temp folders private, which closes a local-user tampering path inside the Sandbox escape row |
