import { PgRuntime } from './uow';

export interface Probe {
  pgNum: number;
  pgMajor: number;
  /** name -> installed / available version (null when not). */
  extensions: Record<
    string,
    { installed: string | null; available: string | null }
  >;
  preload: string;
  azureAllowlist: string | null;
}

const PROBE_SQL = `/* pipeshub:kernel.capability_probe */
SELECT current_setting('server_version_num')::int AS pg_num,
       (SELECT coalesce(jsonb_object_agg(name, jsonb_build_object('installed', installed_version, 'available', default_version)), '{}'::jsonb)
          FROM pg_available_extensions WHERE name IN ('vector','pg_trgm','pg_textsearch','pg_search','pg_stat_statements')) AS ext,
       coalesce(current_setting('shared_preload_libraries', true), '') AS preload,
       current_setting('azure.extensions', true) AS azure_allowlist`;

interface ProbeRow {
  pg_num: number;
  ext: Probe['extensions'];
  preload: string;
  azure_allowlist: string | null;
}

export async function probeCapabilities(rt: PgRuntime): Promise<Probe> {
  const r = await rt.withUow({ preOrg: true }, (tx) =>
    tx.query<ProbeRow>(PROBE_SQL),
  );
  const row = r.rows[0] as ProbeRow;
  return {
    pgNum: row.pg_num,
    pgMajor: Math.floor(row.pg_num / 10000),
    extensions: row.ext,
    preload: row.preload,
    azureAllowlist: row.azure_allowlist,
  };
}

// shortcut: floors live here until backend/db/capabilities.yaml exists; then load that file instead.
const PG_FLOOR = 17;
const PG_TESTED_UP_TO = 18;
const VECTOR_FLOOR = '0.8.0';
const HNSW_VACUUM_SAFE = '0.8.4';
const TRGM_FLOOR = '1.6';

export interface Capabilities {
  bm25: boolean;
  hnswVacuumSafe: boolean;
  problems: string[];
}

export interface Needs {
  vector?: boolean;
}

/** Compare dotted versions numerically: '0.8.10' > '0.8.4'. */
export function versionAtLeast(have: string, floor: string): boolean {
  const a = have.split('.').map(Number);
  const b = floor.split('.').map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d !== 0) return d > 0;
  }
  return true;
}

/** Problems are the reasons `strict` refuses to start; with strict off the caller logs them as downgrades. */
export function evaluateCapabilities(
  p: Probe,
  needs: Needs = {},
): Capabilities {
  const problems: string[] = [];
  if (p.pgMajor < PG_FLOOR)
    problems.push(`PostgreSQL ${p.pgMajor} is below the floor ${PG_FLOOR}`);
  if (p.pgMajor > PG_TESTED_UP_TO)
    problems.push(
      `PostgreSQL ${p.pgMajor} is newer than the tested ${PG_TESTED_UP_TO}`,
    );
  const have = (name: string): string | null =>
    p.extensions[name]?.installed ?? null;
  const missing = (name: string): string =>
    p.extensions[name]?.available
      ? `${name} is available but not created: run migrate`
      : `${name} is not available on this server`;
  const trgm = have('pg_trgm');
  if (!trgm) problems.push(missing('pg_trgm'));
  else if (!versionAtLeast(trgm, TRGM_FLOOR))
    problems.push(`pg_trgm ${trgm} is below ${TRGM_FLOOR}`);
  const vec = have('vector');
  if (needs.vector) {
    if (!vec) problems.push(missing('vector'));
    else if (!versionAtLeast(vec, VECTOR_FLOOR))
      problems.push(`pgvector ${vec} is below ${VECTOR_FLOOR}`);
  }
  return {
    bm25: have('pg_textsearch') !== null,
    hnswVacuumSafe: vec !== null && versionAtLeast(vec, HNSW_VACUUM_SAFE),
    problems,
  };
}
