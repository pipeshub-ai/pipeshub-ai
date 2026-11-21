// Cross-language contract checks driven from pytest (C2, C3, C4). Reads one JSON request on
// stdin and writes one JSON response on stdout:
//   {"op":"parse","items":[{"schema":"core/envelope.schema.json","json":"<canonical text>"}]}
//     -> [{"ok":true,"canonical":"..."} | {"ok":false,"error":"..."}]
//   {"op":"canonical","items":["<json text>"]}  -> same result shape
import { readFileSync } from 'fs';
import {
  assertNoReservedKeys,
  canonicalJson,
} from '../../../src/modules/agent-harness/contracts/canonical-json';
import { zodFor } from './contracts';

type Result = { ok: true; canonical: string } | { ok: false; error: string };

function attempt(fn: () => string): Result {
  try {
    return { ok: true, canonical: fn() };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

const request = JSON.parse(readFileSync(0, 'utf8')) as {
  op: 'parse' | 'canonical';
  items: Array<{ schema: string; json: string } | string>;
};

const results: Result[] = request.items.map((item) => {
  if (request.op === 'canonical') return attempt(() => canonicalJson(JSON.parse(item as string)));
  const { schema, json } = item as { schema: string; json: string };
  return attempt(() => {
    const value: unknown = JSON.parse(json);
    assertNoReservedKeys(value);
    const parsed = zodFor(schema).safeParse(value);
    if (!parsed.success) throw new Error(JSON.stringify(parsed.error.issues.slice(0, 3)));
    return canonicalJson(parsed.data);
  });
});

process.stdout.write(JSON.stringify(results));
