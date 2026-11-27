// Node's worker-commit route schema, driven from pytest (CHILDREF): a worker's commit body is
// checked by the schema the route validates with, not a copy of it. Reads one JSON request on
// stdin, writes one JSON response on stdout:
//   {"items":[<commit body>, ...]} -> [{"ok":true} | {"ok":false,"error":"<first issues>"}]
import { readFileSync } from 'fs';
import { commitRunSchema } from '../../../src/modules/agent-harness/transport/schemas';

const request = JSON.parse(readFileSync(0, 'utf8')) as { items: unknown[] };

const results = request.items.map((body) => {
  const parsed = commitRunSchema.safeParse({ params: { runId: 'run-1' }, body });
  return parsed.success
    ? { ok: true }
    : { ok: false, error: JSON.stringify(parsed.error.issues.slice(0, 3)) };
});

process.stdout.write(JSON.stringify(results));
