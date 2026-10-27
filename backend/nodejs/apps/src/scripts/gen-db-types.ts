import { writeFileSync } from 'node:fs';
import { PgRuntime, createPgPool, generateTypes } from '../libs/db';

/**
 * Usage: PG_TYPEGEN_URL=<dsn of a migrated database> node dist/scripts/gen-db-types.js <out.ts> <schema.table>...
 * The DSN's login must be allowed to take the role named by PG_TYPEGEN_ROLE (default `node`).
 */
async function main(): Promise<void> {
  const [out, ...tables] = process.argv.slice(2);
  const url = process.env.PG_TYPEGEN_URL;
  if (!out || tables.length === 0 || !url)
    throw new Error(
      'usage: PG_TYPEGEN_URL=... gen-db-types <out.ts> <schema.table>...',
    );
  const pool = createPgPool({
    connectionString: url,
    service: 'typegen',
    version: 'dev',
    max: 1,
  });
  try {
    const rt = new PgRuntime({
      pool,
      defaultService: process.env.PG_TYPEGEN_ROLE ?? 'node',
    });
    writeFileSync(out, await generateTypes(rt, tables));
  } finally {
    await pool.end();
  }
}

if (require.main === module) {
  main().catch((e: unknown) => {
    console.error(e instanceof Error ? e.message : e);
    process.exit(1);
  });
}
