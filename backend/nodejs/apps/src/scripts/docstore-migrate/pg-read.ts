/** Reads rows of a mapped collection as the verifier needs them (raw `to_jsonb` rows, no repository code) and decodes them. */
import { fill } from '../../libs/repositories/pg-util';
import { Doc } from './canonical';
import { decodeDoc } from './decode';
import type { CollectionMapping } from './mapping/types';

type Row = Record<string, unknown>;

/** What reading needs: a kernel unit of work, or a plain pool for a global table. */
export interface Queryer {
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- callers name the row type they expect
  query<R = Record<string, unknown>>(
    text: string,
    values?: unknown[],
  ): Promise<{ rows: R[]; rowCount: number | null }>;
}
type KernelTx = Queryer;

const IDENT = /^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$/;
const ident = (s: string): string => {
  if (!IDENT.test(s)) throw new Error(`bad identifier '${s}'`);
  return s;
};

/**
 * `to_jsonb(alias)` with the bigint columns as JSON strings: `pg` parses the result with JSON.parse, which rounds a number above 2^53
 * (the decoder turns the string back into a number when it is safe).
 */
const rowJson = (alias: string, bigints: string[]): string =>
  bigints.length === 0
    ? `to_jsonb(${alias})`
    : `to_jsonb(${alias}) || jsonb_build_object(${bigints
        .map((c) => `'${ident(c)}', ${alias}.${ident(c)}::text`)
        .join(', ')})`;

const rootJson = (m: CollectionMapping): string =>
  rowJson('t', [
    ...(m.idType === 'bigint' ? [m.idCol] : []),
    ...m.fields.filter((f) => f.type === 'bigint').map((f) => f.col),
  ]);

async function childRows(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
): Promise<Record<string, Map<string, Row[]>>> {
  const out: Record<string, Map<string, Row[]>> = {};
  for (const c of m.children ?? []) {
    const { rows } = await k.query<{ r: Row }>(
      fill(
        '/* pipeshub:verify.children */ SELECT @{J} AS r FROM @{T} c WHERE c.@{P} = ANY($1::@{K}[])',
        {
          // a child's parent column has the type of the root id
          K: m.idType === 'bigint' ? 'bigint' : 'text',
          J: rowJson('c', [
            ...(m.idType === 'bigint' ? [c.parentCol] : []),
            ...c.fields.filter((f) => f.type === 'bigint').map((f) => f.col),
          ]),
          T: ident(c.table),
          P: ident(c.parentCol),
        },
      ),
      [ids],
    );
    const by = new Map<string, Row[]>();
    for (const { r } of rows) {
      const key = String(r[c.parentCol]);
      by.set(key, [...(by.get(key) ?? []), r]);
    }
    out[c.table] = by;
  }
  return out;
}

function assemble(
  m: CollectionMapping,
  roots: Row[],
  kids: Record<string, Map<string, Row[]>>,
): Doc[] {
  return roots.map((root) => {
    const id = String(root[m.idCol]);
    const children: Record<string, Row[]> = {};
    for (const c of m.children ?? [])
      children[c.table] = kids[c.table]?.get(id) ?? [];
    return decodeDoc(m, root, children);
  });
}

/** The documents for these ids (those that have rows). */
export async function readDecoded(
  k: KernelTx,
  m: CollectionMapping,
  ids: string[],
): Promise<Doc[]> {
  if (ids.length === 0) return [];
  const { rows } = await k.query<{ r: Row }>(
    fill(
      '/* pipeshub:verify.roots */ SELECT @{J} AS r FROM @{T} t WHERE t.@{I} = ANY($1::@{K}[])',
      {
        J: rootJson(m),
        T: ident(m.table),
        I: ident(m.idCol),
        K: m.idType === 'bigint' ? 'bigint' : 'text',
      },
    ),
    [ids],
  );
  const roots = rows.map((x) => x.r);
  return assemble(
    m,
    roots,
    await childRows(
      k,
      m,
      roots.map((r) => String(r[m.idCol])),
    ),
  );
}

/** The next page of documents in id order, after `afterId` (null: from the start). */
export async function readDecodedPage(
  k: KernelTx,
  m: CollectionMapping,
  afterId: string | null,
  limit: number,
): Promise<Doc[]> {
  const { rows } = await k.query<{ r: Row }>(
    fill(
      '/* pipeshub:verify.page */ SELECT @{J} AS r FROM @{T} t WHERE ($1::@{K} IS NULL OR t.@{I} > $1::@{K}) ORDER BY t.@{I} LIMIT $2',
      {
        J: rootJson(m),
        T: ident(m.table),
        I: ident(m.idCol),
        K: m.idType === 'bigint' ? 'bigint' : 'text',
      },
    ),
    [afterId, limit],
  );
  const roots = rows.map((x) => x.r);
  return assemble(
    m,
    roots,
    await childRows(
      k,
      m,
      roots.map((r) => String(r[m.idCol])),
    ),
  );
}

/** Row counts per organisation of the root table. */
export async function countByOrg(
  k: KernelTx,
  m: CollectionMapping,
): Promise<Map<string, number>> {
  if (!m.orgCol) {
    const { rows } = await k.query<{ n: string }>(
      fill('/* pipeshub:verify.count */ SELECT count(*)::text AS n FROM @{T}', {
        T: ident(m.table),
      }),
    );
    return new Map([['', Number(rows[0]?.n ?? 0)]]);
  }
  const { rows } = await k.query<{ o: string; n: string }>(
    fill(
      '/* pipeshub:verify.count_by_org */ SELECT t.@{O}::text AS o, count(*)::text AS n FROM @{T} t GROUP BY t.@{O}',
      {
        T: ident(m.table),
        O: ident(m.orgCol),
      },
    ),
  );
  return new Map(rows.map((r) => [r.o, Number(r.n)]));
}
