# CONNECTOR.md — building a PipesHub connector

Start here to add or change a connector: the Python service that syncs one external source (Jira, Google Drive, Slack, a database, ...) into PipesHub. Written for coding agents first; humans can follow it the same way.

Read [AGENTS.md](./AGENTS.md) first (via [CLAUDE.md](./CLAUDE.md) in Claude Code). It covers the repository layout, the pluggable stores and the rules that apply to all code here. This file and the documents it links add what is specific to connectors.

## Read in this order

| Document | What it gives you | Read it |
| --- | --- | --- |
| [docs/connectors/01-high-level-design.md](./docs/connectors/01-high-level-design.md) | Where a connector runs, its lifecycle, full and incremental sync, the data model | before anything else |
| [docs/connectors/02-low-level-design.md](./docs/connectors/02-low-level-design.md) | `BaseConnector`, `ConnectorBuilder`, entities, the processor, sync points, filters, the client layer, streaming | before writing code, and as a reference |
| [docs/connectors/03-decisions.md](./docs/connectors/03-decisions.md) | The design choices to make, and which ones to ask a human about | before writing code |
| [docs/connectors/04-intake-questions.md](./docs/connectors/04-intake-questions.md) | What to look up in the vendor's documentation, and what to ask the person who wants the connector | before writing code |
| [docs/connectors/05-step-by-step.md](./docs/connectors/05-step-by-step.md) | The build in phases, each with a check, and every file to touch | while building |
| [docs/connectors/06-permissions.md](./docs/connectors/06-permissions.md) | How access is decided and how to map the source's sharing onto it | before mapping any record |
| [docs/connectors/07-errors-and-logging.md](./docs/connectors/07-errors-and-logging.md) | How to log source API errors so customer issues can be debugged, without leaking secrets | before writing any API call |
| [docs/connectors/08-testing.md](./docs/connectors/08-testing.md) | Contract, behaviour and integration tests, and the CI gates | while building |
| [CONNECTOR_CHECKLIST.md](./CONNECTOR_CHECKLIST.md) | The definition of done | before opening the PR, and when reviewing one |

In Claude Code, the `add-connector` skill (`.claude/skills/add-connector/SKILL.md`) walks through these in order.

## The rules that matter most

1. **Access must match the source.** Users see only what they can see in the source. When unsure, hide, and ask the human. [06-permissions.md](./docs/connectors/06-permissions.md)
2. **Never bypass the abstractions.** Write through `DataSourceEntitiesProcessor`, read config through `ConfigurationService`, keep checkpoints in `SyncPoint`. No graph, vector, broker or config-store client in connector code.
3. **Never block the event loop.** Every connector's sync shares one asyncio loop with the service's API.
4. **Log the source's real error, never a secret.** [07-errors-and-logging.md](./docs/connectors/07-errors-and-logging.md)
5. **Unknown is not empty.** A failed read of users, groups, ACLs, items, properties or links is never written as "there are none". An empty `related_external_records` list replaces stored links only after a successful read, and only for the record types in [02-low-level-design.md](./docs/connectors/02-low-level-design.md). A failed read keeps what is stored.
6. **Ask before changing shared framework code** under `app/connectors/core/`, `app/models/` or the graph providers. A new `RecordType` or `RecordGroupType` is one of those changes. `RecordType` (`app/models/entities.py`) and `RecordTypes` (`app/config/constants/arangodb.py`) are both live and have already drifted; [03-decisions.md](./docs/connectors/03-decisions.md) lists every place a new value has to land.

## When to stop and ask the human

Ask for the test account and credentials, the editions and objects in scope, and confirmation of the decisions in [03-decisions.md](./docs/connectors/03-decisions.md) marked "ask". Ask again when the source's permission model cannot be represented without over-sharing, when a required API scope may not be grantable, or when the live API contradicts its documentation. [04-intake-questions.md](./docs/connectors/04-intake-questions.md) has the full list and how to phrase the questions.

## Keeping these documents true

These documents describe code that changes. When you change connector framework code (`app/connectors/core/`, `app/models/entities.py`, `app/models/permission.py`, the processor or the builder), update the document that describes it in the same PR. Cite files and symbols, not line numbers.
