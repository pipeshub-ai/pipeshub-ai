import { PgRuntime } from './uow';

export interface ColumnRow {
  schema: string;
  table: string;
  column: string;
  type: string;
  nullable: boolean;
  /** 'always' = generated, 'default' = has a default or identity BY DEFAULT, '' = plain. */
  generated: 'always' | 'default' | '';
}

/** Read from pg_catalog so the result does not depend on the connecting role's privileges. Partitions are skipped. */
export const COLUMNS_SQL = `/* pipeshub:kernel.typegen_columns */
SELECT n.nspname AS schema, c.relname AS "table", a.attname AS "column", t.typname AS type, NOT a.attnotnull AS nullable,
       CASE WHEN a.attgenerated <> '' OR a.attidentity = 'a' THEN 'always'
            WHEN a.atthasdef OR a.attidentity = 'd' THEN 'default' ELSE '' END AS generated
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_type t ON t.oid = a.atttypid
WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND a.attnum > 0 AND NOT a.attisdropped
  AND (n.nspname || '.' || c.relname) = ANY($1::text[])
ORDER BY 1, 2, a.attnum`;

const TS_TYPES: Record<string, string> = {
  text: 'string',
  varchar: 'string',
  bpchar: 'string',
  name: 'string',
  uuid: 'string',
  oid24: 'string',
  citext: 'string',
  int2: 'number',
  int4: 'number',
  float4: 'number',
  float8: 'number',
  oid: 'number',
  // the driver returns 64-bit integers and numeric as strings
  int8: 'string',
  numeric: 'string',
  bool: 'boolean',
  timestamptz: 'Date',
  timestamp: 'Date',
  date: 'Date',
  jsonb: 'unknown',
  json: 'unknown',
  bytea: 'Buffer',
};

function tsType(pgType: string): string {
  if (pgType.startsWith('_')) return `${tsType(pgType.slice(1))}[]`;
  return TS_TYPES[pgType] ?? 'unknown';
}

const pascal = (s: string): string =>
  s.replace(/(^|_)([a-z0-9])/g, (_, __, c: string) => c.toUpperCase());

/** Deterministic Kysely table types for the named tables (`schema.table`). */
export function renderTypes(rows: ColumnRow[]): string {
  const tables = new Map<string, ColumnRow[]>();
  for (const r of rows) {
    const k = `${r.schema}.${r.table}`;
    tables.set(k, [...(tables.get(k) ?? []), r]);
  }
  const names = [...tables.keys()].sort();
  const used = ['default', 'always']
    .filter((g) => rows.some((r) => r.generated === g))
    .map((g) => (g === 'default' ? 'Generated' : 'GeneratedAlways'));
  const out = [
    '// Generated from the migrated database (src/scripts/gen-db-types.ts). Do not edit by hand.',
    ...(used.length
      ? [`import type { ${used.join(', ')} } from 'kysely';`]
      : []),
    '',
  ];
  for (const k of names) {
    out.push(`export interface ${pascal(k.replace('.', '_'))} {`);
    for (const c of tables.get(k) as ColumnRow[]) {
      let t = tsType(c.type);
      if (c.nullable && t !== 'unknown') t += ' | null'; // unknown already includes null
      if (c.generated === 'always') t = `GeneratedAlways<${t}>`;
      else if (c.generated === 'default') t = `Generated<${t}>`;
      out.push(`  ${c.column}: ${t};`);
    }
    out.push('}', '');
  }
  out.push('export interface DB {');
  for (const k of names) out.push(`  '${k}': ${pascal(k.replace('.', '_'))};`);
  out.push('}', '');
  return out.join('\n');
}

export async function generateTypes(
  rt: PgRuntime,
  tables: string[],
): Promise<string> {
  const r = await rt.withUow({ preOrg: true }, (tx) =>
    tx.query<ColumnRow>(COLUMNS_SQL, [tables]),
  );
  const missing = tables.filter(
    (t) => !r.rows.some((x) => `${x.schema}.${x.table}` === t),
  );
  if (missing.length)
    throw new Error(`tables not found (or not visible): ${missing.join(', ')}`);
  return renderTypes(r.rows);
}
