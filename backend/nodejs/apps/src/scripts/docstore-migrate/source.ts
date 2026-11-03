/** The source side of a comparison: a raw MongoDB document brought to the form the mapping describes, with every change counted. */
import { createHash } from 'node:crypto';
import { neutral } from '../../libs/repositories/import';
import { canonicalDocument } from './verify-values';
import {
  Doc,
  deletePath,
  getPath,
  hasPath,
  plainJson,
  setPath,
  stripNulCounted,
} from './canonical';
import type { CollectionMapping } from './mapping/types';

export class SourceReport {
  nul = new Map<string, number>();
  /** Listed transforms applied, by name. */
  applied = new Map<string, number>();
  /** Documents left out by the mapping, by reason. */
  skipped = new Map<string, number>();
  bump(name: string, by = 1): void {
    this.applied.set(name, (this.applied.get(name) ?? 0) + by);
  }
  merge(o: SourceReport): void {
    for (const [k, v] of o.nul) this.nul.set(k, (this.nul.get(k) ?? 0) + v);
    for (const [k, v] of o.applied)
      this.applied.set(k, (this.applied.get(k) ?? 0) + v);
    for (const [k, v] of o.skipped)
      this.skipped.set(k, (this.skipped.get(k) ?? 0) + v);
  }
  toJSON(): unknown {
    return {
      nulStripped: Object.fromEntries(this.nul),
      transforms: Object.fromEntries(this.applied),
      skipped: Object.fromEntries(this.skipped),
    };
  }
}

/** Store-neutral form of a raw driver document, with U+0000 stripped (counted by path). Used by the copy and the verifier alike. */
export function toNeutral(
  raw: unknown,
  rep: SourceReport,
  collection: string,
): Doc {
  const nul = new Map<string, number>();
  const doc = stripNulCounted(neutral(raw) as Doc, nul);
  for (const [p, n] of nul)
    rep.nul.set(
      `${collection}.${p}`,
      (rep.nul.get(`${collection}.${p}`) ?? 0) + n,
    );
  return doc;
}

/**
 * The verifier's reading of a raw document: its own decoder (verify-values.ts), not the copy's `neutral()`. U+0000 is removed and
 * counted, as the copy does (the one listed transform).
 */
export function toCanonical(
  raw: unknown,
  rep: SourceReport,
  collection: string,
): Doc {
  const nul = new Map<string, number>();
  const doc = canonicalDocument(raw, nul) as Doc;
  for (const [p, n] of nul)
    rep.nul.set(
      `${collection}.${p}`,
      (rep.nul.get(`${collection}.${p}`) ?? 0) + n,
    );
  return doc;
}

/** `null` when the mapping leaves the document out. */
/** `alreadyHashed`: the source already holds the hashes (a MongoDB that was filled by a reverse copy). */
export function prepareSource(
  m: CollectionMapping,
  raw: unknown,
  rep: SourceReport,
  alreadyHashed = false,
): Doc | null {
  const doc = toCanonical(raw, rep, m.collection);
  const why = m.skip?.(doc);
  if (why) {
    rep.skipped.set(why, (rep.skipped.get(why) ?? 0) + 1);
    return null;
  }
  for (const t of m.transforms ?? []) if (t.apply(doc)) rep.bump(t.name);
  for (const d of m.dropped ?? [])
    if (deletePath(doc, d.path)) rep.bump(`dropped ${d.path}`);
  for (const f of m.fields) {
    if (f.readAbsentAs !== undefined && !hasPath(doc, f.path)) {
      setPath(doc, f.path, f.readAbsentAs);
      rep.bump(`absent ${f.path} read as ${JSON.stringify(f.readAbsentAs)}`);
    }
    if (
      f.type === 'sha256' &&
      !alreadyHashed &&
      typeof getPath(doc, f.path) === 'string'
    )
      setPath(
        doc,
        f.path,
        createHash('sha256')
          .update(getPath(doc, f.path) as string)
          .digest('hex'),
      );
    else if (f.type === 'oid' && typeof getPath(doc, f.path) === 'string')
      setPath(doc, f.path, (getPath(doc, f.path) as string).toLowerCase());
  }
  for (const c of m.children ?? []) {
    if (c.emptyIsArray !== false && !hasPath(doc, c.path)) {
      setPath(doc, c.path, []);
      rep.bump(`absent array ${c.path} read as []`);
    }
  }
  return plainJson(doc) as Doc;
}
