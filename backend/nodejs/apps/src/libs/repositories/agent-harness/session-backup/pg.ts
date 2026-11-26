/**
 * One session's rows out of a PostgreSQL document store and into another (P9), as `MongoSessionBackup` does for
 * MongoDB: one NDJSON file per table (each row as `to_jsonb` gives it, restored with `jsonb_populate_record`, so every
 * column type round-trips) and a manifest with counts and digests. The selectors mirror the Mongo ones table by table
 * (`SESSION_BACKUP_PG_TABLES` names the Mongo store each one is), so a backup holds what an erasure would remove,
 * plus the org's wrapped data keys. A restore is one unit of work as the archive's org: all of it or nothing.
 */
import { chmod, mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { sessionWorkspaceId } from '../../../../modules/agent-harness/domain/workspace';
import {
  archiveFileDigest,
  manifestDigest,
  SESSION_BACKUP_PG_FORMAT,
  SessionBackupManifest,
  SessionBackupRefused,
} from '../../../../modules/agent-harness/domain/session-backup';
import { forOrg } from '../../../context/orgContext';
import { fill, likeEscape } from '../../pg-util';
import { inBoundOrg } from '../durable-transaction/bound-org';
import { PgUnitOfWork, kernelTx } from '../../pg';
import type { PgKernelTx } from '../pg-documents/pg';

interface Selection {
  orgId: string;
  sessionId: string;
  workspace: string;
  runIds: string[];
  streamIds: string[];
  citationIds: string[];
}

type Select = (s: Selection) => { where: string; values: unknown[] };

const bySession: Select = (s) => ({
  where: 'session_id = $2',
  values: [s.sessionId],
});

/** In order: later selectors read what earlier ones found, and a restore inserts parents before children. */
const TABLES: ReadonlyArray<{
  table: string;
  mongo: string;
  select: Select;
  org?: true;
}> = [
  { table: 'harness.runs', mongo: 'agentRuns', select: bySession },
  {
    table: 'harness.session_events',
    mongo: 'sessionEvents',
    select: bySession,
  },
  {
    table: 'conversation.chat_sessions',
    mongo: 'chatSessions',
    select: (s) => ({ where: 'id = $2', values: [s.sessionId] }),
  },
  {
    table: 'conversation.chat_session_members',
    mongo: 'chatSessions',
    select: bySession,
  },
  {
    table: 'conversation.chat_messages',
    mongo: 'chatSessionMessages',
    select: bySession,
  },
  {
    table: 'conversation.citations',
    mongo: 'citations',
    select: (s) => ({ where: 'id = ANY($2::text[])', values: [s.citationIds] }),
  },
  { table: 'harness.next_runs', mongo: 'agentNextRuns', select: bySession },
  { table: 'harness.run_events', mongo: 'agentEvents', select: bySession },
  {
    table: 'harness.command_receipts',
    mongo: 'agentCommandReceipts',
    select: (s) => ({ where: 'run_id = ANY($2::text[])', values: [s.runIds] }),
  },
  {
    table: 'harness.admission_receipts',
    mongo: 'agentAdmissionReceipts',
    select: bySession,
  },
  {
    table: 'harness.session_forks',
    mongo: 'agentSessionForks',
    select: (s) => ({
      where: '(source_session_id = $2 OR fork_session_id = $2)',
      values: [s.sessionId],
    }),
  },
  {
    table: 'harness.journal_payloads',
    mongo: 'agentJournalPayloads',
    select: (s) => ({ where: 'owner_session_id = $2', values: [s.sessionId] }),
  },
  ...(
    [
      'harness.workspace_manifests',
      'harness.workspace_heads',
      'harness.workspace_writers',
    ] as const
  ).map((table) => ({
    table,
    mongo: {
      'harness.workspace_manifests': 'agentWorkspaceManifests',
      'harness.workspace_heads': 'agentWorkspaceHeads',
      'harness.workspace_writers': 'agentWorkspaceWriters',
    }[table],
    select: (s: Selection) => ({
      where: `(workspace_id = $2 OR workspace_id LIKE $3 ESCAPE '\\')`,
      values: [s.workspace, `${likeEscape(s.workspace)}:%`],
    }),
  })),
  {
    table: 'harness.workspace_keys',
    mongo: 'agentWorkspaceKeys',
    select: bySession,
  },
  {
    table: 'harness.session_counters',
    mongo: 'agentEventCounters',
    select: bySession,
  },
  {
    table: 'harness.stream_counters',
    mongo: 'sessionStreamCounters',
    select: (s) => ({
      where: 'stream_id = ANY($2::text[])',
      values: [s.streamIds],
    }),
  },
  {
    table: 'harness.data_keys',
    mongo: 'agentHarnessDataKeys',
    select: () => ({ where: 'true', values: [] }),
    org: true,
  },
];

/** Each table and the Mongo store it is (C-14 holds the Mongo stores to the backup coverage; a test holds these to it). */
export const SESSION_BACKUP_PG_TABLES: Readonly<Record<string, string>> =
  Object.fromEntries(TABLES.map(({ table, mongo }) => [table, mongo]));

const writePrivate = async (path: string, text: string): Promise<void> => {
  await writeFile(path, text, { mode: 0o600 });
  await chmod(path, 0o600);
};

/** A unit of its own as `orgId`, which must be the org bound (`requireBoundOrg`). */
const asOrgUnit = <T>(
  orgId: string,
  write: boolean,
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> =>
  inBoundOrg(orgId, () =>
    new PgUnitOfWork().run((tx) => fn(kernelTx(tx)), { readOnly: !write }),
  );

export class PgSessionBackup {
  async export(
    orgId: string,
    sessionId: string,
    directory: string,
  ): Promise<SessionBackupManifest> {
    await mkdir(directory, { recursive: true, mode: 0o700 });
    await chmod(directory, 0o700);
    const files = await asOrgUnit(orgId, false, async (k) => {
      const selection: Selection = {
        orgId,
        sessionId,
        workspace: sessionWorkspaceId(sessionId),
        runIds: [],
        streamIds: [],
        citationIds: [],
      };
      const out: Array<[string, Array<Record<string, unknown>>]> = [];
      for (const { table, select } of TABLES) {
        const { where, values } = select(selection);
        const { rows } = await k.query<{ row: Record<string, unknown> }>(
          fill(
            `/* pipeshub:harness.session_backup_export */ SELECT to_jsonb(t) AS row FROM @{TABLE} t WHERE t.org_id = $1 AND @{WHERE} ORDER BY 1`,
            { TABLE: table, WHERE: where },
          ),
          [orgId, ...values],
        );
        const found = rows.map((r) => r.row);
        learn(table, found, selection);
        out.push([table, found]);
      }
      return out;
    });
    const collections: SessionBackupManifest['collections'] = {};
    for (const [table, rows] of files) {
      const text = rows.map((row) => JSON.stringify(row)).join('\n');
      await writePrivate(join(directory, `${table}.ndjson`), text);
      collections[table] = {
        documents: rows.length,
        sha256: archiveFileDigest(text),
      };
    }
    const manifest: SessionBackupManifest = {
      format: SESSION_BACKUP_PG_FORMAT,
      orgId,
      sessionId,
      createdAt: new Date().toISOString(),
      collections,
      digest: manifestDigest(collections, archiveFileDigest),
    };
    await writePrivate(
      join(directory, 'manifest.json'),
      JSON.stringify(manifest, null, 2),
    );
    return manifest;
  }

  /** Into a database that does not hold the session; the archive is verified whole before anything is written. */
  async restore(directory: string): Promise<SessionBackupManifest> {
    const manifest = JSON.parse(
      await readFile(join(directory, 'manifest.json'), 'utf-8'),
    ) as SessionBackupManifest;
    if (manifest.format !== SESSION_BACKUP_PG_FORMAT) {
      throw new SessionBackupRefused(
        'archive_format',
        `not a ${SESSION_BACKUP_PG_FORMAT} archive`,
      );
    }
    const contents = new Map<string, Array<Record<string, unknown>>>();
    for (const [table, entry] of Object.entries(manifest.collections)) {
      if (!(table in SESSION_BACKUP_PG_TABLES)) {
        throw new SessionBackupRefused(
          'archive_format',
          `${table} is not a table this backup writes`,
        );
      }
      const text = await readFile(join(directory, `${table}.ndjson`), 'utf-8');
      if (archiveFileDigest(text) !== entry.sha256) {
        throw new SessionBackupRefused(
          'archive_corrupt',
          `${table} does not match the manifest`,
        );
      }
      const rows =
        text === ''
          ? []
          : text
              .split('\n')
              .map((line) => JSON.parse(line) as Record<string, unknown>);
      if (rows.some((row) => row['org_id'] !== manifest.orgId)) {
        throw new SessionBackupRefused(
          'archive_corrupt',
          `${table} holds a row of another organisation`,
        );
      }
      contents.set(table, rows);
    }
    if (
      manifestDigest(manifest.collections, archiveFileDigest) !==
      manifest.digest
    ) {
      throw new SessionBackupRefused(
        'archive_corrupt',
        'the manifest digest does not match',
      );
    }
    // The archive, verified whole above, is the job's own record of its org (as a job row is): bound from it.
    await forOrg(manifest.orgId, 'session-backup', () =>
      asOrgUnit(manifest.orgId, true, async (k) => {
        const { rows } = await k.query<{ held: boolean }>(
          `/* pipeshub:harness.session_backup_present */ SELECT EXISTS (SELECT 1 FROM harness.runs WHERE org_id = $1 AND session_id = $2)
            OR EXISTS (SELECT 1 FROM harness.session_events WHERE org_id = $1 AND session_id = $2) AS held`,
          [manifest.orgId, manifest.sessionId],
        );
        if (rows[0]?.held === true) {
          throw new SessionBackupRefused(
            'session_exists',
            'the target database already holds this session',
          );
        }
        for (const { table, org } of TABLES) {
          const found = contents.get(table) ?? [];
          if (found.length === 0) continue;
          const { columns, identity } = await writableColumns(k, table);
          const list = columns.map((c) => `"${c}"`).join(', ');
          // The org's keys may already be there (an earlier restore of the same org): never overwrite one.
          await k.query(
            fill(
              `/* pipeshub:harness.session_backup_restore */ INSERT INTO @{TABLE} (@{COLUMNS})@{OVERRIDING}
             SELECT @{VALUES} FROM jsonb_array_elements($1::jsonb) e,
               LATERAL jsonb_populate_record(NULL::@{TABLE}, e) r@{CONFLICT}`,
              {
                TABLE: table,
                COLUMNS: list,
                // An identity value is the row's place in its table's order (a run's admission): restored as archived.
                OVERRIDING: identity ? ' OVERRIDING SYSTEM VALUE' : '',
                VALUES: columns.map((c) => `r."${c}"`).join(', '),
                CONFLICT: org ? ' ON CONFLICT DO NOTHING' : '',
              },
            ),
            [JSON.stringify(found)],
          );
        }
      }),
    );
    return manifest;
  }
}

/** The columns a row is written with: every column but the generated ones, which the database computes again. */
async function writableColumns(
  k: PgKernelTx,
  table: string,
): Promise<{ columns: string[]; identity: boolean }> {
  const { rows } = await k.query<{ name: string; identity: boolean }>(
    `/* pipeshub:harness.session_backup_columns */ SELECT attname AS name, attidentity <> '' AS identity
      FROM pg_catalog.pg_attribute
      WHERE attrelid = $1::regclass AND attnum > 0 AND NOT attisdropped AND attgenerated = '' ORDER BY attnum`,
    [table],
  );
  return {
    columns: rows.map((r) => r.name),
    identity: rows.some((r) => r.identity),
  };
}

function learn(
  table: string,
  rows: Array<Record<string, unknown>>,
  selection: Selection,
): void {
  if (table === 'harness.runs')
    selection.runIds = rows.map((row) => String(row['run_id']));
  if (table === 'harness.session_events')
    selection.streamIds = [
      ...new Set(rows.map((row) => String(row['stream_id']))),
    ];
  if (table === 'conversation.chat_messages') {
    selection.citationIds = [
      ...new Set(
        rows.flatMap((row) =>
          (
            (row['citations'] as Array<{ citationId?: unknown }> | null) ?? []
          ).flatMap((c) =>
            typeof c.citationId === 'string' ? [c.citationId] : [],
          ),
        ),
      ),
    ];
  }
}
