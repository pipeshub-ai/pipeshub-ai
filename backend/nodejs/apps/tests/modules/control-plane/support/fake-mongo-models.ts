/**
 * In-memory stand-ins for the mongoose model calls the control-plane Mongo
 * adapters make, so the store contracts also run on those adapters in the unit
 * lane: what each adapter asks for (row ids, compare-and-set filters, the
 * duplicate-key path) is checked without a replica set. Query planning and
 * transactions stay the `.mongo.itest.ts` suites' job.
 */
import sinon from 'sinon';
import type { Model } from 'mongoose';

type Row = Record<string, unknown> & { _id: string };
type Filter = Record<string, unknown>;

const valuesAt = (row: unknown, path: string[]): unknown[] => {
  if (path.length === 0) return [row];
  if (Array.isArray(row)) return row.flatMap((item) => valuesAt(item, path));
  if (typeof row !== 'object' || row === null) return [];
  return valuesAt((row as Record<string, unknown>)[path[0]!], path.slice(1));
};

const matches = (row: Row, filter: Filter): boolean =>
  Object.entries(filter).every(([path, wanted]) => {
    const found = valuesAt(row, path.split('.'));
    const allowed =
      typeof wanted === 'object' && wanted !== null && '$in' in wanted ? (wanted as { $in: unknown[] }).$in : [wanted];
    return found.some((value) => allowed.includes(value));
  });

class Query<T> implements PromiseLike<T> {
  constructor(private readonly run: (order: Record<string, 1 | -1>, limit: number) => T) {}
  private order: Record<string, 1 | -1> = {};
  private max = Infinity;
  sort(order: Record<string, 1 | -1>): this {
    this.order = order;
    return this;
  }
  limit(max: number): this {
    this.max = max;
    return this;
  }
  session(): this {
    return this;
  }
  lean(): this {
    return this;
  }
  async exec(): Promise<T> {
    return this.run(this.order, this.max);
  }
  then<A, B>(resolve?: (value: T) => A | PromiseLike<A>, reject?: (reason: unknown) => B | PromiseLike<B>): PromiseLike<A | B> {
    return this.exec().then(resolve, reject);
  }
}

export class FakeCollection {
  readonly rows = new Map<string, Row>();
  /** Reads made: one per round trip a real collection would take. */
  reads = 0;

  private select(filter: Filter): Row[] {
    return [...this.rows.values()].filter((row) => matches(row, filter));
  }

  install(model: Model<never>): void {
    const target = model as unknown as Record<string, unknown>;
    sinon.stub(target, 'findById' as never).callsFake(((id: string) =>
      new Query(() => {
        this.reads += 1;
        return structuredClone(this.rows.get(id) ?? null);
      })) as never);
    sinon.stub(target, 'find' as never).callsFake(((filter: Filter) =>
      new Query((order, limit) => {
        this.reads += 1;
        const [[key, direction] = ['_id', 1]] = Object.entries(order);
        const sorted = this.select(filter).sort((a, b) => (a[key]! < b[key]! ? -direction : a[key]! > b[key]! ? direction : 0));
        return structuredClone(sorted.slice(0, limit));
      })) as never);
    sinon.stub(target, 'create' as never).callsFake((async (docs: Row[]) => {
      for (const doc of docs) {
        if (this.rows.has(doc._id)) throw Object.assign(new Error('E11000 duplicate key'), { code: 11000 });
        this.rows.set(doc._id, structuredClone(doc));
      }
    }) as never);
    sinon.stub(target, 'updateOne' as never).callsFake(((filter: Filter, update: { $set: Row }, options?: { upsert?: boolean }) =>
      new Query(() => {
        const [row] = this.select(filter);
        if (row !== undefined) Object.assign(row, structuredClone(update.$set));
        else if (options?.upsert === true) {
          this.rows.set(String(filter._id), { ...structuredClone(update.$set), _id: String(filter._id) });
        }
        return { matchedCount: row === undefined ? 0 : 1 };
      })) as never);
    const remove = (filter: Filter, all: boolean) =>
      new Query(() => {
        const found = this.select(filter).slice(0, all ? undefined : 1);
        for (const row of found) this.rows.delete(row._id);
        return { deletedCount: found.length };
      });
    sinon.stub(target, 'deleteOne' as never).callsFake(((filter: Filter) => remove(filter, false)) as never);
    sinon.stub(target, 'deleteMany' as never).callsFake(((filter: Filter) => remove(filter, true)) as never);
  }
}

/** Replaces each model's calls with a fresh collection until `sinon.restore()`. */
export function fakeCollections<K extends string>(models: Record<K, Model<never>>): Record<K, FakeCollection> {
  const collections = {} as Record<K, FakeCollection>;
  for (const key of Object.keys(models) as K[]) {
    collections[key] = new FakeCollection();
    collections[key].install(models[key]);
  }
  return collections;
}
