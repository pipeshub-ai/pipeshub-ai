# Agent Harness v2 — design and operation

The agent harness is in beta: its server setting is on by default and an org admin turns it on in Labs, where it is off
by default ([guides/HARNESS-OFF.md](guides/HARNESS-OFF.md)).

The agent harness's reference documents: how it is built, why, how to run it and how to test it. Project
tracking (status boards, work logs, evidence captures, research and review notes) is kept outside this repository.

## Design

| Document | What it is |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **Architecture:** verified current state, target HLD, module map, interfaces (LLD), data flows, lifecycle, deployment views |
| [ENGINES-PLAN.md](ENGINES-PLAN.md) | Why and how external agent engines (Codex, opencode, Claude Code) run in PipesHub sandboxes |
| [design-plan.md](design-plan.md) | The runtime's design plan: personas, features, phases, security model |
| [one-way-doors.md](one-way-doors.md) | Decisions that are expensive to reverse, each with its ADR |
| [adr/](adr/) | Architecture decision records |
| [gaps-and-additions.md](gaps-and-additions.md) | Second-pass review: gaps found and the decisions they added |
| [checklist.md](checklist.md) | Readiness checklist: each item verifiable, with the test that proves it |
| [impl/tool-effects.md](impl/tool-effects.md) | Why each tool is classified as a read or a write |

## Operation

| Document | What it is |
|---|---|
| [FLAGS.md](FLAGS.md) | Every switch that turns harness behaviour on or off, who may change it, its default |
| [SLO.md](SLO.md) | Latency, throughput and footprint SLOs; availability SLO, error budget, RPO and RTO |
| [DR-DRILL.md](DR-DRILL.md) | Disaster recovery drill procedure and its record |
| [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) | What to check before the harness ships to production |
| [capacity-baseline.md](capacity-baseline.md), [footprint-baseline.md](footprint-baseline.md) | Measured capacity and memory baselines |
| [guides/user-guide.md](guides/user-guide.md) | **User guide:** runs, approvals and the approvals inbox, artifacts, repository work, versions, schedules, feedback |
| [guides/builder-guide.md](guides/builder-guide.md) | **Builder guide:** choosing a harness and model, tool disclosure, skills, scripted cases, approvals and policy, run gates, budgets, versions |
| [guides/admin-guide.md](guides/admin-guide.md) | **Admin guide:** turning the harness and engines on, allowed websites and git hosts, budgets, kill switches, approvals, audit, run settings |
| [guides/operator-guide.md](guides/operator-guide.md) | **Operator guide:** what runs, choosing a deployment, monitoring, backup and restore, upgrades; links the pages below |
| [guides/HARNESS-OFF.md](guides/HARNESS-OFF.md) | What the platform does with the harness switched off |
| [guides/sizing.md](guides/sizing.md) | Sizing guide: measured numbers, the code's own host limits, worked examples, and what is not measured yet |
| [guides/runbooks.md](guides/runbooks.md) | Runbooks: incident response (kill, preserve evidence, rotate), stuck session, exhausted pool, gateway 5xx, CVE response, credential rotation, approval backlog |
| [guides/air-gapped-install.md](guides/air-gapped-install.md) | Air-gapped install: mirrored images (Compose, Helm), engine images, local models, phoning home |
| [guides/SUPPLY-CHAIN.md](guides/SUPPLY-CHAIN.md) | **Supply chain:** the hashed Python locks, `npm ci` and image pins every install goes through, the rules that enforce them, and how to update a dependency safely |
| [guides/vendor-dpa-checklist.md](guides/vendor-dpa-checklist.md) | Vendor DPA checklist: what to collect and check before turning on a hosted model provider, E2B, a coding engine or Opik |
| [guides/security-whitepaper.md](guides/security-whitepaper.md) | Security whitepaper: isolation and what is deferred, egress, credentials, the Rule of Two, extension points, audit, supply chain |
| [guides/threat-model.md](guides/threat-model.md) | Threat model tied to design-plan §9, with its review record |
| [guides/pipeshub-contract-reference.md](guides/pipeshub-contract-reference.md) | The versioned `/pipeshub` contract and `ph` CLI reference, with its version history |
| [guides/adding-a-harness-adapter.md](guides/adding-a-harness-adapter.md) | Adding a harness adapter (a coding engine): the adapter port, the conformance kit (CF cases), a worked example |

## Testing

| Document | What it is |
|---|---|
| [TEST-STRATEGY.md](TEST-STRATEGY.md) | Unit → contract → integration → fault → E2E → profile layers; CI lanes; phase gate → suite mapping |
| [test-scenarios.md](test-scenarios.md) | What must be true for the harness to count as complete, with the test that proves each scenario |
| [ui-cases/](ui-cases/) | UI cases the end-to-end specs capture |

Earlier v1 documents at `docs/agent-harness-*.md` remain as history; where they conflict with ARCHITECTURE.md,
ARCHITECTURE.md is current.
