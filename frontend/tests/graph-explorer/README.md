# Graph explorer browser tests

Real Chromium against the product's own explorer code (`app/(main)/graph`), with no backend, no login and no stack. A small harness page (`harness/`) mounts `GraphExplorer`; the only things replaced are what the explorer imports from the rest of the app (`harness/stubs/`) and the backend, which is an in-browser fake (`harness/fake-graph-server.ts`) that follows the `/api/v1/graph/*` and `/api/v1/code-graph/*` contract: 20 anchors and 100 neighbours at most, an opaque offset cursor, one 404 for hidden and missing, 503 on an outage, an in-browser graph of 2,000 functions where "bob" cannot read some files.

```bash
npm run test:graph-e2e            # builds the harness and runs every spec
npx playwright test -c playwright.graph.config.ts -g "revocation"   # one area
```

What is covered, in `specs/`: entry (links, search-first), paging and table parity, access re-checks and revocation (with a fake clock, so 30 seconds take milliseconds), failure and recovery (outage, Retry, the cleared picture coming back), what a viewer must never see (a second person's view, the URL, the table, the live regions), keyboard and screen-reader paths, touch targets and reflow, and the node caps.

Control the fake from a test with `window.__fake` (see `harness/fake-graph-server.ts`): `persona`, `revoked`, `outage`, `latencyMs`, `status`, `incomplete`. The Cytoscape instance is read through the container's `_cyreg` (helpers in `specs/helpers.ts`), so a spec can say how many nodes are drawn and where.

The fake is not the server: backend filtering and paging are tested with the backend (`backend/python/tests`). If the API contract changes, change the fake in the same pull request.
