/** What the coverage checks accept as deliberately not reached, shared by the static test and the database itest. */
import { HELD, IGNORED, MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { CARRIERS } from '../../../src/scripts/docstore-migrate/carry';
import { EDITION_DOCSTORE_COVERAGE } from '../../../src/edition';

/** The schemas of the document store: every table in them is reached by a mapping, named by a held or ignored collection, or listed here. */
export const DOC_STORE_SCHEMAS = ['identity', 'conversation', 'content', 'oauth', 'platform', 'integrations', 'harness'];

/** Tables no collection maps to: written by functions, derived, or not data to move. */
export const UNMAPPED_TABLES: Record<string, string> = {
  'oauth.agent_call_events': 'agent activity log (30 days); a store move starts a new one',
  'platform.kv_entries': 'key-value store runtime state, rebuilt by use',
  'platform.kv_access': 'the key-value access mode (shadow or enforce), owned by the migrations and set by db.migrate',
  'platform.cache_entries': 'the Node API\'s session and short-lived cache, rebuilt by signing in again (a MongoDB install keeps it in Redis)',
  'platform.queue_deliveries': 'message queue runtime state, rebuilt by use',
  'platform.queue_groups': 'message queue runtime state, rebuilt by use',
  'platform.queue_messages': 'message queue runtime state, rebuilt by use',
  'platform.sched_jobs': 'job queue runtime state, rebuilt by use',
  'platform.sched_repeats': 'job queue runtime state, rebuilt by use',
  'platform.docstore_changes': 'the change log the reverse follower reads; written by triggers',
  'platform.store_epoch': 'the store fence, owned by the migrator',
  'platform.docstore_keymap': "the migrator's key map (which row each copied document became), kept for the follow and rollback window",
  'platform.docstore_keymap_deployment': "the migrator's key map for rows no org owns (deployment-wide tables), kept for the follow and rollback window",
  'platform.docstore_refused': "the migrator's complete list of the documents the copy refused (R48MIGFIX-FU-2), read by verify and rollback, removed with the job",
  'platform.jobs': 'job state',
  'platform.consumer_inbox': 'consumer de-duplication, rebuilt by use',
  'platform.deployment_profile': 'deployment profile, not application data',
  'identity.session_families': 'refresh-token families of the old store (session_families is not copied, ADR-WEB-02 D9): each person signs in once more',
};

/** Schema paths the migration deliberately does not carry (none today). */
export const ALLOWED_UNMAPPED_SCHEMA_PATHS: Record<string, string> = {};

/** Tables reached: mapped roots and children, the carriers' tables, and the tables of held and ignored collections. */
export function reachedTables(): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of MAPPINGS) for (const t of [m.table, ...(m.children ?? []).map((c) => c.table)]) out.set(t, `mapped by ${m.collection}`);
  for (const m of MAPPINGS) for (const t of m.sideTables ?? []) out.set(t, `written with ${m.collection}`);
  for (const c of CARRIERS) for (const t of c.tables) out.set(t, `carried by ${c.name}`);
  for (const h of HELD) for (const t of h.tables) out.set(t, `held with ${h.collection}`);
  for (const i of IGNORED) for (const t of i.tables ?? []) out.set(t, `not copied with ${i.collection}`);
  return out;
}

/** The columns a mapping accounts for, per table (root and children). */
export function namedColumns(): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  for (const m of MAPPINGS) {
    out.set(m.table, new Set<string>([m.idCol, ...(m.orgCol ? [m.orgCol] : []), ...(m.versionCol ? [m.versionCol] : []), ...(m.nullsCol ? [m.nullsCol] : []), ...m.derived, ...m.fields.map((f) => f.col)]));
    for (const ch of m.children ?? []) out.set(ch.table, new Set<string>([ch.parentCol, ch.orderCol, ...ch.derived, ...ch.fields.map((f) => f.col)]));
  }
  return out;
}

/** Problems of columns against what the mappings name: `have` lists each table's columns. */
export function columnProblems(have: (table: string) => string[]): string[] {
  const problems: string[] = [];
  for (const [table, named] of namedColumns()) {
    const cols = have(table);
    if (!cols.length) {
      problems.push(`table ${table} does not exist`);
      continue;
    }
    for (const c of cols) if (!named.has(c)) problems.push(`column ${table}.${c} is neither mapped nor derived`);
    for (const c of named) if (!cols.includes(c)) problems.push(`${table}.${c} does not exist`);
  }
  return problems;
}

/** Tables of the document-store schemas that nothing reaches or lists. */
export function unreachedTables(tables: string[]): string[] {
  const reached = reachedTables();
  return tables.filter(
    (t) =>
      DOC_STORE_SCHEMAS.includes(t.split('.')[0] as string) &&
      !reached.has(t) &&
      !(t in UNMAPPED_TABLES) &&
      !(t in EDITION_DOCSTORE_COVERAGE.unmappedTables),
  );
}

/**
 * Entries of UNMAPPED_TABLES that this build reaches after all: a stale reason (the table was mapped, carried or held since).
 * A table only an edition's held or not-copied collection names is not stale: this build's reason (no store of this build
 * writes it) still holds, and the list must read the same in every edition.
 */
export function staleUnmapped(): string[] {
  const reached = reachedTables();
  const edition = new Set([...EDITION_DOCSTORE_COVERAGE.held, ...EDITION_DOCSTORE_COVERAGE.ignored].flatMap((x) => x.tables ?? []));
  return Object.keys(UNMAPPED_TABLES).filter((t) => reached.has(t) && !edition.has(t));
}
