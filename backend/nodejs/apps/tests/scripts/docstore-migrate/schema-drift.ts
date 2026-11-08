/** Schema drift: every path a Mongoose schema declares must be known to the collection's mapping. */
import mongoose from 'mongoose';
import { isKnown, knownPaths } from '../../../src/scripts/docstore-migrate/census';
import type { CollectionMapping } from '../../../src/scripts/docstore-migrate/mapping/types';

/** Paths of a schema, arrays of sub-documents as `path[]` with the paths beneath. */
export function schemaPaths(schema: mongoose.Schema, prefix = ''): string[] {
  const out: string[] = [];
  for (const [p, t] of Object.entries(schema.paths)) {
    const path = `${prefix}${p}`;
    out.push(path);
    const sub = (t as unknown as { schema?: mongoose.Schema }).schema;
    if (sub) {
      out.push(`${path}[]`);
      out.push(...schemaPaths(sub, `${path}[].`));
    }
  }
  return out;
}

/** `collection.path` for every schema path no mapping knows. `allowed` names paths the migration deliberately does not carry. */
export function schemaDrift(mappings: CollectionMapping[], allowed: Record<string, string> = {}): string[] {
  const problems: string[] = [];
  for (const model of Object.values(mongoose.models)) {
    const m = mappings.find((x) => x.collection === model.collection.name);
    if (!m) continue;
    const known = knownPaths(m);
    for (const p of schemaPaths(model.schema)) {
      if (p === '_id' || p === '__v' || p.endsWith('._id') || p.endsWith('.$*')) continue;
      if (!isKnown(known, p) && !(`${m.collection}.${p}` in allowed)) problems.push(`${m.collection}.${p}`);
    }
  }
  return problems;
}
