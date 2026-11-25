# Legacy chat and agent contract goldens (P0.7)

These files freeze what users and integrations see today from the chat and
agent streams. Any change to them is a change to the user-facing contract and
is reviewed like an API change.

| File | Written by | What it is |
| --- | --- | --- |
| `upstream/<scenario>.sse` | `backend/python/tests/contracts/test_sse_golden.py` | The SSE the Python AI service sends to Node, recorded with the scripted model |
| `<scenario>.sse` | `tests/contracts/sse-golden.test.ts` (G1) | The SSE Node sends to the browser for that upstream stream |
| `<scenario>.transcript.json` | `tests/contracts/transcript-golden.test.ts` (G2) | The conversation and messages (including `parts[]`) Node persisted |

`tests/contracts/mcp-package-parser.test.ts` (G3) reads each `<scenario>.sse`
stream through the published `@pipeshub-ai/mcp` parser and has no golden file
of its own.

## Normalization

Only generated ids and clock readings are replaced, numbered by first
appearance so a repeated id still points at the same thing:

- Python: UUIDs become `uuid-N`, `msg_<12 hex>` message ids become `msg-N`,
  ISO timestamps become `<timestamp>`.
- Node: Mongo ObjectIds become `oid-N`, UUIDs Node generates become
  `node-uuid-N`, ISO timestamps become `<timestamp>`, and the numeric
  `lastActivityAt` and `duration` become `"<clock>"`.

Everything else, including JSON spacing, key order and frame order, is
compared byte for byte.

## Re-recording (G4)

A normal test run only compares. Goldens change only when you ask for it:

```bash
# Python first: it writes both its own goldens and upstream/*.sse here.
cd backend/python
UPDATE_GOLDENS=1 python -m pytest tests/contracts

# Then Node, which replays upstream/*.sse.
cd backend/nodejs/apps
UPDATE_GOLDENS=1 npx mocha 'tests/contracts/**/*.test.ts'
```

Review the diff before committing it. The Python suite fails if
`upstream/*.sse` and its own goldens disagree, so the two sides cannot drift
silently.
