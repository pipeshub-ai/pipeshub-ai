---
name: add-connector
description: Build a new PipesHub connector, or extend an existing one, end to end, following CONNECTOR.md. Use when asked to add, create, build or port a connector or integration for an external source (Jira, Drive, Slack, a database, a SaaS API), or to add sync of a new object type to an existing connector.
---

# Add a PipesHub connector

Follow [CONNECTOR.md](../../../CONNECTOR.md) and the documents it links in `docs/connectors/`. This skill is the order of work and the points where you must stop; the documents hold the detail. Where they disagree with the code, the code is right: say so, and fix the document in the same PR.

## 1. Understand before you design

1. Read `CONNECTOR.md`, then `docs/connectors/01-high-level-design.md` and `02-low-level-design.md`.
2. Pick the reference connector of the same shape from the table in `docs/connectors/05-step-by-step.md` and read its connector class, client and behaviour tests.
3. Read the vendor's API documentation for everything listed under "Find out yourself" in `docs/connectors/04-intake-questions.md`. Note the documentation page for each fact.

## 2. Ask, once, with recommendations

Send the human one message with the questions in `04-intake-questions.md` that the documentation did not answer, and the decisions in `docs/connectors/03-decisions.md` that need them. For each, give your recommendation and what you found, so they can confirm. Always ask for a test account and credentials, and say what cannot be verified without one.

Do not write code until the human has answered. If they cannot provide a test account, carry on with unit tests against fakes, and say in the PR that the connector was not run against the real source.

## 3. Write the design into the PR description

Before code: the "Connector intake" section, each decision from `03-decisions.md` with its reason, and the permission mapping per object type from `docs/connectors/06-permissions.md`. Show it to the human if any part of the permission mapping errs towards over-sharing or hides content the source shows.

## 4. Build in phases

Work through `docs/connectors/05-step-by-step.md` phase by phase. Run each phase's check and do not start the next phase until it passes. While writing any API call, apply `docs/connectors/07-errors-and-logging.md`. While writing any record or group, apply `06-permissions.md`.

Stop and ask the human when:

- the source's sharing cannot be expressed without over-sharing;
- a needed API scope may not be grantable, or needs a paid tier;
- the live API contradicts its documentation;
- you would need to change shared code under `backend/python/app/connectors/core/`, `backend/python/app/models/` or the graph providers.

## 5. Finish

1. Run the greps in `07-errors-and-logging.md` on the new connector and its client, and read every hit.
2. Run the checks in `docs/connectors/08-testing.md`: the contract tests, the connector's tests, the whole unit suite, ruff on the changed files.
3. Go through `CONNECTOR_CHECKLIST.md` item by item, and copy it, ticked, into the PR description. For each unticked item, say why.
4. Report to the human what was verified against the real source, what only against fakes, and what is known not to work.
