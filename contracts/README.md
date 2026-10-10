# Contract schemas

JSON Schema (draft-07) definitions for payloads that cross the Node and Python service boundary. Each contract lives in its own folder:

```text
contracts/<contract>/request.schema.json
contracts/<contract>/response.schema.json
contracts/<contract>/examples.json      # { request|response: { valid: [...], invalid: [...] } }
```

Both runners validate every example against its schema, so a schema edit that breaks either side fails CI in both languages:

- Node: `backend/nodejs/apps/tests/contracts/contracts.test.ts` (ajv)
- Python: `backend/python/tests/unit/contracts/test_contract_schemas.py` (jsonschema)

Adding a contract: create the folder, add schemas and at least one valid and one invalid example per direction. Runners discover folders automatically.

Keep schemas to what is stable on the wire. They do not replace the Pydantic / validator models on either side; they are the shared description both sides are checked against.

Current contracts: `attachments-validate` (`/api/v1/chat/attachments/validate`).
