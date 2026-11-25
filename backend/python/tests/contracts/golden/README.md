# Legacy AI-service stream goldens (P0.7)

`<scenario>.sse` is the normalized SSE the AI service sends for each scenario
in `tests/contracts/scenarios.py`, recorded with the scripted model. The same
bytes are Node's upstream input in
`backend/nodejs/apps/tests/contracts/golden/upstream/`; that directory's
README describes the normalization and the Node-side goldens.

A normal run only compares. Re-record with an explicit flag and review the
diff:

```bash
UPDATE_GOLDENS=1 python -m pytest tests/contracts
```
