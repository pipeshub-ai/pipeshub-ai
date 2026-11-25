/** An in-memory `CarryMongo` for the carriers' tests: raw documents per collection, read through the carriers' own `sourceDoc`. */
import { ObjectId } from 'mongodb';
import { sourceDoc } from '../../../src/scripts/docstore-migrate/carry/plain';
import type { CarryMongo, Doc, SourceDoc } from '../../../src/scripts/docstore-migrate/carry/types';

const keyOf = (id: unknown): string => (id instanceof ObjectId ? `o:${id.toHexString()}` : `s:${String(id)}`);
const idOf = (id: string, oid: boolean): ObjectId | string => (oid ? new ObjectId(id) : id);

/** A deep copy that keeps ObjectIds, Dates and Buffers what they are (structuredClone would not). */
export function clone<T>(v: T): T {
  if (v instanceof ObjectId) return new ObjectId(v.toHexString()) as T;
  if (v instanceof Date) return new Date(v.getTime()) as T;
  if (Buffer.isBuffer(v)) return Buffer.from(v) as T;
  if (Array.isArray(v)) return v.map(clone) as T;
  if (v !== null && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, clone(x)])) as T;
  return v;
}

export class FakeCarryMongo implements CarryMongo {
  readonly data = new Map<string, Map<string, Doc>>();

  constructor(seed: Record<string, Doc[]> = {}) {
    for (const [c, docs] of Object.entries(seed)) for (const d of docs) this.put(c, d);
  }

  put(collection: string, doc: Doc): void {
    const m = this.data.get(collection) ?? new Map<string, Doc>();
    m.set(keyOf(doc._id), clone(doc));
    this.data.set(collection, m);
  }

  del(collection: string, id: unknown): void {
    this.data.get(collection)?.delete(keyOf(id));
  }

  docs(collection: string): Doc[] {
    return [...(this.data.get(collection)?.values() ?? [])];
  }

  get(collection: string, id: unknown): Doc | undefined {
    return this.data.get(collection)?.get(keyOf(id));
  }

  async *scan(collection: string, read: { pick?: readonly string[]; idPaths?: readonly string[]; order?: 'id' } = {}): AsyncIterable<SourceDoc> {
    const docs = this.docs(collection);
    if (read.order === 'id') docs.sort((a, b) => (keyOf(a._id) < keyOf(b._id) ? -1 : keyOf(a._id) > keyOf(b._id) ? 1 : 0));
    for (const raw of docs) {
      const projected = read.pick
        ? Object.fromEntries(Object.entries(raw).filter(([k]) => k === '_id' || read.pick?.includes(k)))
        : raw;
      yield sourceDoc(projected, collection, read);
    }
  }

  async replace(collection: string, docs: SourceDoc[]): Promise<void> {
    for (const d of docs) this.put(collection, { _id: idOf(d.id, d.oid), ...d.doc });
  }

  async update(collection: string, ops: Array<{ id: string; oid: boolean; set: Doc; unset: string[] }>): Promise<void> {
    for (const o of ops) {
      const cur = this.get(collection, idOf(o.id, o.oid));
      if (!cur) continue;
      for (const p of o.unset) delete cur[p];
      Object.assign(cur, o.set);
    }
  }

  async remove(collection: string, ids: Array<{ id: string; oid: boolean }>): Promise<number> {
    let n = 0;
    for (const i of ids) {
      const k = keyOf(idOf(i.id, i.oid));
      if (this.data.get(collection)?.delete(k)) n += 1;
    }
    return n;
  }
}
