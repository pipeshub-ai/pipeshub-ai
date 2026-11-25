/**
 * A carrier for collections whose documents are rows of a harness table: key columns (org first), typed columns, and a `doc`
 * column that holds the document as the harness store writes it (`encodeDoc`). Declared per collection (`DocTableSpec`) and
 * checked both ways: what a document must become is computed from it, what a row holds is rebuilt from it, and the two are
 * compared whole, so a field the spec does not place is a difference, never a silent loss.
 *
 * A table that keeps the document's `_id` in a key column (`idCol`) needs nothing more. Any other is keyed through the key map
 * (`keymap.ts`): the follower removes the row a deleted document became, and a rollback gives every row its document's id back.
 * A deployment-wide table (`deployment`: no `org_id`, a row no org owns) is keyed the same way, in the key map's deployment-wide form.
 */
import { createHash } from 'node:crypto';
import { ObjectId } from 'mongodb';
import type { Tx as KernelTx } from '../../../libs/db';
import {
  decodeDoc,
  docParam,
  encodeDoc,
  rowRegion,
} from '../../../libs/repositories/agent-harness/pg-documents/pg';
import { fill } from '../../../libs/repositories/pg-util';
import { deletePath, getPath, hasPath, setPath } from '../canonical';
import {
  DEPLOYMENT_KEY,
  KeyEntry,
  clearKeys,
  dropKeys,
  keysByRow,
  keysOf,
  putKeys,
  rowKey,
} from './keymap';
import { canonicalText } from './plain';
import type { Carrier, CarryMongo, Doc, Restored, SourceDoc } from './types';

export interface ColumnSpec {
  col: string;
  /** Document path; `_id` is the document's id. */
  path: string;
  /**
   * text, int (bigint/integer), float (double precision), bool, json (jsonb, as `encodeDoc` writes it), bytes (bytea: a Buffer),
   * date (timestamptz: a Date), datems (bigint epoch milliseconds: a Date in MongoDB), texts (text[]).
   */
  kind:
    | 'text'
    | 'int'
    | 'float'
    | 'bool'
    | 'json'
    | 'bytes'
    | 'date'
    | 'datems'
    | 'texts';
  /**
   * The column's value computed from the document (a subject cut out of the `_id`, a flag the store writes as `=== true`) instead
   * of read at `path`: the document keeps what it was computed from, so the column is never given back into it.
   */
  from?: (src: SourceDoc) => unknown;
  /** The value a document that lacks the path is read as (the column is NOT NULL with this default): a listed transform. */
  absentAs?: unknown;
  /** The column may be NULL: a document without the value (or holding null) leaves it NULL. */
  nullable?: true;
}

export interface DocTableSpec {
  collection: string;
  table: string;
  /** Document path of the organisation id (the `org_id` column). */
  orgPath?: string;
  /** The organisation of a document that does not hold it at one path (it is in its `_id`, say); its `org_id` is not given back. */
  orgOf?: (src: SourceDoc) => string;
  /** A deployment-wide table: no `org_id` column, keyed by its key columns alone. */
  deployment?: true;
  /** The table has no `region` column (deployment plumbing). */
  noRegion?: true;
  /** Read in `_id` order (an identity column of the table numbers rows in the order they are inserted, as MongoDB's ids did). */
  ordered?: true;
  /** The key columns after `org_id`, in primary-key order. */
  key: ColumnSpec[];
  /** A key column that holds the document's `_id`: no key map is kept. */
  idCol?: string;
  /** Typed columns that are not keys. */
  columns?: ColumnSpec[];
  /** The `doc` column: the document without `_id` and without these paths (which their columns give back). */
  /** `idAs`: the store keeps the document's `_id` inside `doc` at this path too (it is given back as `_id`, not as a field). */
  doc?: { omit: string[]; idAs?: string };
  /**
   * Document paths the PostgreSQL store does not keep because they follow from the rest (MongoDB's copy of an org id inside a label
   * set): the row does not hold them, and the document a row gives back has them recomputed. The compare still sees a document whose
   * stored value differs from the recomputed one.
   */
  derived?: Record<string, (doc: Doc) => unknown>;
  /** The id a row made in PostgreSQL after the move gets at a rollback (default: a new ObjectId), when the store derives it. */
  newId?: (
    doc: Doc,
    row: Record<string, unknown>,
  ) => { id: string; oid: boolean };
}

const IDENT = /^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$/;

/** A source document's id with its kind (an ObjectId and a string of the same text are two documents). */
const sourceKey = (d: { id: string; oid: boolean }): string =>
  `${d.oid ? 'o' : 's'}:${d.id}`;

/** A document's canonical text, hashed: what a rollback compares to decide whether MongoDB already holds it. */
const digest = (doc: Doc): string =>
  createHash('sha256').update(canonicalText(doc)).digest('hex');
const ident = (s: string): string => {
  if (!IDENT.test(s)) throw new Error(`bad identifier '${s}'`);
  return s;
};

type Row = Record<string, unknown>;

/** The document's value for a column; a missing NOT NULL value refuses the document, naming it. */
function columnValue(
  c: ColumnSpec,
  src: SourceDoc,
  collection: string,
): unknown {
  const v = c.from
    ? c.from(src)
    : c.path === '_id'
      ? src.id
      : getPath(src.doc, c.path);
  if (v === undefined || v === null) {
    if (c.absentAs !== undefined) return c.absentAs;
    if (c.nullable) return null;
    throw new Error(
      `${collection} ${src.id}: ${c.path} is missing, and ${c.col} cannot be empty: correct the document, then copy again`,
    );
  }
  const wrong = (what: string): Error =>
    new Error(`${collection} ${src.id}: ${c.path} is not ${what}`);
  if (c.kind === 'text' && typeof v !== 'string') throw wrong('text');
  if (c.kind === 'int' && (typeof v !== 'number' || !Number.isSafeInteger(v)))
    throw wrong('an integer');
  if (c.kind === 'float' && (typeof v !== 'number' || !Number.isFinite(v)))
    throw wrong('a number');
  if (c.kind === 'bool' && typeof v !== 'boolean') throw wrong('a boolean');
  if (c.kind === 'bytes' && !Buffer.isBuffer(v)) throw wrong('bytes');
  if ((c.kind === 'date' || c.kind === 'datems') && !(v instanceof Date))
    throw wrong('a date');
  if (
    c.kind === 'texts' &&
    !(Array.isArray(v) && v.every((x) => typeof x === 'string'))
  )
    throw wrong('a list of text');
  return v;
}

/** The key the key map and the compare use for a deployment-wide row's organisation. */
const DEPLOYMENT = DEPLOYMENT_KEY;

const orgOf = (spec: DocTableSpec, src: SourceDoc): string => {
  if (spec.deployment) return DEPLOYMENT;
  if (spec.orgOf) return spec.orgOf(src);
  const o = spec.orgPath ? getPath(src.doc, spec.orgPath) : undefined;
  if (typeof o !== 'string' || o === '')
    throw new Error(
      `${spec.collection} ${src.id}: ${spec.orgPath ?? 'the organisation'} is missing, so the row has no organisation: correct the document, then copy again`,
    );
  return o;
};

const sqlValue = (c: ColumnSpec, v: unknown): unknown =>
  c.kind === 'json' && v !== null
    ? JSON.stringify(encodeDoc(v))
    : c.kind === 'datems' && v instanceof Date
      ? v.getTime()
      : v;

/** A column's value as `to_jsonb` of the row gives it, back to the value the document held. */
const fromColumn = (c: ColumnSpec, v: unknown): unknown => {
  if (v === null || v === undefined) return v;
  switch (c.kind) {
    case 'int':
    case 'float':
      return Number(v);
    case 'json':
      return decodeDoc(v);
    case 'bytes':
      return Buffer.from(String(v).replace(/^\\x/, ''), 'hex');
    case 'date':
      return new Date(String(v));
    case 'datems':
      return new Date(Number(v));
    case 'text':
    case 'bool':
    case 'texts':
      return v;
  }
};

/** A path the `doc` column does not keep, so its column gives it back. */
const columnOwns = (spec: DocTableSpec, path: string | undefined): boolean =>
  path !== undefined &&
  path !== '_id' &&
  (!spec.doc || spec.doc.omit.includes(path));

/** A row's organisation as the key map and the compare name it. */
const rowOrg = (spec: DocTableSpec, row: Record<string, unknown>): string =>
  spec.deployment ? DEPLOYMENT : String(row.org_id);

/** The key map's organisation of a row (null: the deployment-wide form). */
const mapOrg = (org: string): string | null =>
  org === DEPLOYMENT ? null : org;

export class DocTable {
  constructor(readonly spec: DocTableSpec) {
    for (const c of [...spec.key, ...(spec.columns ?? [])]) ident(c.col);
    ident(spec.table);
    if (!spec.deployment && !spec.orgPath && !spec.orgOf)
      throw new Error(`${spec.collection}: no orgPath, orgOf or deployment`);
  }

  private get allColumns(): ColumnSpec[] {
    return [...this.spec.key, ...(this.spec.columns ?? [])];
  }

  /** The row key of a document (its key values after the organisation). */
  keyOf(src: SourceDoc): { orgId: string; rowKey: string } {
    return {
      orgId: orgOf(this.spec, src),
      rowKey: rowKey(
        this.spec.key.map((c) => columnValue(c, src, this.spec.collection)),
      ),
    };
  }

  /** The document as the row must give it back (listed transforms applied). */
  expectedDoc(src: SourceDoc, note: (t: string) => void): Doc {
    const d = structuredClone(src.doc);
    for (const c of this.allColumns)
      if (
        !c.from &&
        c.absentAs !== undefined &&
        c.path !== '_id' &&
        !hasPath(d, c.path)
      ) {
        setPath(d, c.path, c.absentAs);
        note(
          `${this.spec.collection}: absent ${c.path} read as ${JSON.stringify(c.absentAs)}`,
        );
      }
    return d;
  }

  /** The document a row holds (without `_id`). */
  docOf(row: Row): Doc {
    const d: Doc = this.spec.doc ? decodeDoc<Doc>(row.doc) : {};
    if (this.spec.doc?.idAs) deletePath(d, this.spec.doc.idAs);
    if (
      !this.spec.deployment &&
      this.spec.orgPath &&
      columnOwns(this.spec, this.spec.orgPath)
    )
      setPath(d, this.spec.orgPath, row.org_id);
    for (const c of this.allColumns.filter((x) => !x.from))
      if (
        columnOwns(this.spec, c.path) &&
        row[c.col] !== null &&
        row[c.col] !== undefined
      )
        setPath(d, c.path, fromColumn(c, row[c.col]));
      // a column the store writes null into (never leaves out) gives back null
      else if (columnOwns(this.spec, c.path) && c.absentAs === null)
        setPath(d, c.path, null);
    for (const [path, f] of Object.entries(this.spec.derived ?? {})) {
      const v = f(d);
      if (v !== undefined) setPath(d, path, v);
    }
    return d;
  }

  private rowKeyOf(row: Row): string {
    return rowKey(this.spec.key.map((c) => fromColumn(c, row[c.col])));
  }

  /** The row a document becomes, as the statement's parameters (`doc` and json columns as JSON text), without the region. */
  rowOf(src: SourceDoc): Row {
    const s = this.spec;
    if (s.idCol && src.oid)
      throw new Error(
        `${s.collection} ${src.id}: an ObjectId _id, but ${s.table} keeps the id as text (a rollback would give it back as a string)`,
      );
    const row: Row = s.deployment ? {} : { org_id: orgOf(s, src) };
    for (const c of this.allColumns)
      row[c.col] = sqlValue(c, columnValue(c, src, s.collection));
    if (s.doc) {
      const doc = structuredClone(src.doc);
      for (const p of [...s.doc.omit, ...Object.keys(s.derived ?? {})])
        deletePath(doc, p);
      if (s.doc.idAs) setPath(doc, s.doc.idAs, src.id);
      row.doc = docParam(doc);
    }
    return row;
  }

  async write(k: KernelTx, docs: SourceDoc[]): Promise<void> {
    if (!docs.length) return;
    const s = this.spec;
    const cols = [
      ...(s.deployment ? [] : ['org_id']),
      ...this.allColumns.map((c) => c.col),
      ...(s.doc ? ['doc'] : []),
      ...(s.noRegion ? [] : ['region']),
    ];
    const keyCols = [
      ...(s.deployment ? [] : ['org_id']),
      ...s.key.map((c) => c.col),
    ];
    const update = cols.filter((c) => !keyCols.includes(c));
    const before = s.idCol
      ? []
      : await keysOf(
          k,
          s.collection,
          docs.map((d) => d.id),
        );
    const entries: KeyEntry[] = [];
    for (const src of docs) {
      const row = this.rowOf(src);
      const values = cols.map((c) => (c === 'region' ? rowRegion() : row[c]));
      await k.query(
        fill(
          '/* pipeshub:migrate.carry_write */ INSERT INTO @{T} (@{C}) VALUES (@{V}) ON CONFLICT (@{K}) DO @{U}',
          {
            T: s.table,
            C: cols.join(', '),
            V: cols.map((_, i) => '$' + String(i + 1)).join(', '),
            K: keyCols.join(', '),
            // a table of key columns only has nothing to update
            U: update.length
              ? [
                  'UPDATE',
                  'SET',
                  update.map((c) => c + ' = EXCLUDED.' + c).join(', '),
                ].join(' ')
              : 'NOTHING',
          },
        ),
        values,
      );
      if (!s.idCol) {
        const key = this.keyOf(src);
        // a document whose key changed leaves no row under its old key
        const old = before.find((e) => e.sourceId === src.id);
        const org = mapOrg(key.orgId);
        if (old && (old.rowKey !== key.rowKey || old.orgId !== org))
          await this.deleteRow(k, old);
        entries.push({
          orgId: org,
          rowKey: key.rowKey,
          sourceId: src.id,
          oid: src.oid,
        });
      }
    }
    await putKeys(k, s.collection, entries);
  }

  private async deleteRow(
    k: KernelTx,
    e: { orgId: string | null; rowKey: string },
  ): Promise<void> {
    const values = JSON.parse(e.rowKey) as unknown[];
    const byOrg = !this.spec.deployment;
    const first = byOrg ? 2 : 1;
    await k.query(
      fill(
        '/* pipeshub:migrate.carry_delete_row */ DELETE FROM @{T} WHERE @{W}',
        {
          T: this.spec.table,
          W:
            [
              ...(byOrg ? ['org_id = $1'] : []),
              ...this.spec.key.map(
                (c, i) => c.col + ' = $' + String(i + first),
              ),
            ].join(' AND ') || 'true',
        },
      ),
      byOrg ? [e.orgId, ...values] : values,
    );
  }

  async remove(k: KernelTx, ids: string[]): Promise<void> {
    if (!ids.length) return;
    const s = this.spec;
    if (s.idCol) {
      await k.query(
        fill(
          '/* pipeshub:migrate.carry_delete_ids */ DELETE FROM @{T} WHERE @{I} = ANY($1::text[])',
          { T: s.table, I: ident(s.idCol) },
        ),
        [ids],
      );
      return;
    }
    for (const e of await keysOf(k, s.collection, ids))
      await this.deleteRow(k, e);
    await dropKeys(k, s.collection, ids);
  }

  expected(
    src: SourceDoc,
    note: (t: string) => void,
  ): Array<[string, unknown]> {
    const { orgId, rowKey: rk } = this.keyOf(src);
    // the document id is compared too: the key map must name the document the row came from
    return [
      [`${orgId}|${rk}`, { _id: src.id, ...this.expectedDoc(src, note) }],
    ];
  }

  private async rows(k: KernelTx): Promise<Row[]> {
    const { rows } = await k.query<{ r: Row }>(
      fill(
        '/* pipeshub:migrate.carry_rows */ SELECT to_jsonb(t) AS r FROM @{T} t',
        { T: this.spec.table },
      ),
    );
    return rows.map((x) => x.r);
  }

  /** Rows by key, each with the id of the document it came from (`row:` and the key when the key map has none). */
  async actual(k: KernelTx): Promise<Map<string, unknown>> {
    const s = this.spec;
    const map = s.idCol ? undefined : await keysByRow(k, s.collection);
    const out = new Map<string, unknown>();
    for (const row of await this.rows(k)) {
      const key = `${rowOrg(s, row)}|${this.rowKeyOf(row)}`;
      const id = s.idCol
        ? String(row[s.idCol])
        : (map?.get(key)?.sourceId ?? `row:${key}`);
      out.set(key, { _id: id, ...this.docOf(row) });
    }
    return out;
  }

  /** Every row written back under its document's id (a row made after the move gets a new one); documents with no row are removed. */
  async restore(k: KernelTx, mongo: CarryMongo): Promise<Restored> {
    const s = this.spec;
    const map = s.idCol ? undefined : await keysByRow(k, s.collection);
    const docs: SourceDoc[] = [];
    const fresh: KeyEntry[] = [];
    for (const row of await this.rows(k)) {
      const doc = this.docOf(row);
      if (s.idCol) {
        docs.push({ id: String(row[s.idCol]), oid: false, doc });
        continue;
      }
      const rk = this.rowKeyOf(row);
      const org = rowOrg(s, row);
      const known = map?.get(`${org}|${rk}`);
      if (known) docs.push({ id: known.sourceId, oid: known.oid, doc });
      else {
        const { id, oid } = s.newId?.(doc, row) ?? {
          id: new ObjectId().toHexString(),
          oid: true,
        };
        docs.push({ id, oid, doc });
        fresh.push({
          orgId: mapOrg(org),
          sourceId: id,
          oid,
          rowKey: rk,
        });
      }
    }
    await putKeys(k, s.collection, fresh);
    // only documents MongoDB does not already hold as the row gives them are written: the reverse follower's sweeps write what
    // changed in the bridge window, so the rollback's freeze writes only what changed since the last one
    const held = new Map<string, string>();
    for await (const d of mongo.scan(s.collection))
      held.set(sourceKey(d), d.refused ? '' : digest(d.doc));
    const changed = docs.filter(
      (d) => held.get(sourceKey(d)) !== digest(d.doc),
    );
    for (let i = 0; i < changed.length; i += 500)
      await mongo.replace(s.collection, changed.slice(i, i + 500));
    const keep = new Set(docs.map(sourceKey));
    const gone: Array<{ id: string; oid: boolean }> = [];
    for (const key of held.keys())
      if (!keep.has(key))
        gone.push({ id: key.slice(2), oid: key.startsWith('o:') });
    const removed = gone.length ? await mongo.remove(s.collection, gone) : 0;
    return {
      written: changed.length,
      unchanged: docs.length - changed.length,
      removed,
    };
  }

  async clear(k: KernelTx): Promise<void> {
    await k.query(
      fill('/* pipeshub:migrate.carry_clear */ DELETE FROM @{T}', {
        T: this.spec.table,
      }),
    );
    if (!this.spec.idCol) await clearKeys(k, [this.spec.collection]);
  }
}

/** One store group of document tables, carried as a unit. */
export function docTableCarrier(
  name: string,
  reason: string,
  specs: DocTableSpec[],
): Carrier {
  const by = new Map(specs.map((s) => [s.collection, new DocTable(s)]));
  const table = (collection: string): DocTable => {
    const t = by.get(collection);
    if (!t) throw new Error(`${name} does not carry ${collection}`);
    return t;
  };
  const reads = Object.fromEntries(
    specs
      .filter((s) => s.ordered)
      .map((s) => [s.collection, { order: 'id' as const }]),
  );
  return {
    name,
    reason,
    collections: specs.map((s) => s.collection),
    fields: {},
    ...(Object.keys(reads).length ? { reads } : {}),
    tables: specs.map((s) => s.table),
    write: (k, c, docs) => table(c).write(k, docs),
    remove: (k, c, ids) => table(c).remove(k, ids),
    expected: (c, doc, note) =>
      table(c).expected(doc, note ?? (() => undefined)),
    actual: (k, c) => table(c).actual(k),
    restore: (k, c, mongo) => table(c).restore(k, mongo),
    async clear(k) {
      for (const t of [...by.values()].reverse()) await t.clear(k);
    },
  };
}
