/**
 * Settings and data people made (R46-CARRY, R45CARRY-FU-2), carried as one unit: Claude sign-ins, memories, schedules with their
 * fire history and counters, Inbox settings, and the once-only claims of "your run finished" notices.
 * A schedule's firings keep a slot from firing twice and its counters hold the limits and the webhook replay guard, so they move
 * with it. Sealed values (a schedule's input template, a webhook signing key) are carried in their sealed form, never opened.
 *
 *  - `agentEngineSignIns` -> `harness.engine_signins` (by org and subject, `PgEngineSignIns`): the subject is the MongoDB id
 *    after `<org>:` (`u:<user>` or `org`), the record without its org and person in `doc`;
 *  - `agentMemories` -> `harness.memories` (by org and memory id), `agentSchedules` -> `harness.schedules` and
 *    `agentScheduleFirings` -> `harness.schedule_firings` (by org and id, the document with its `_id` in `doc` as the PostgreSQL
 *    store writes it): the document in `doc`;
 *  - `agentScheduleCounters` -> `harness.schedule_counters` (by org and key): typed columns, the TTL date as epoch milliseconds;
 *  - `agentHarnessInboxPreferences` -> `harness.inbox_preferences` (by org and person) and `agentHarnessRunEndNotices` ->
 *    `harness.run_end_notices` (by org and run): typed columns, a claim's date as epoch milliseconds.
 */
import { docTableCarrier, type DocTableSpec } from './doc-table';
import type { SourceDoc } from './types';

const orgScoped =
  (part: string) =>
  (doc: Record<string, unknown>): { id: string; oid: boolean } => ({
    id: `${String(doc.orgId)}:${String(doc[part])}`,
    oid: false,
  });

/** A sign-in's subject: its id after `<org>:`. */
const signInSubject = (src: SourceDoc): string => {
  const org = String(src.doc.orgId);
  if (!src.id.startsWith(`${org}:`))
    throw new Error(
      `agentEngineSignIns ${src.id}: the id does not start with its org, so it names no subject: correct the document, then copy again`,
    );
  return src.id.slice(org.length + 1);
};

export const PEOPLE_TABLES: DocTableSpec[] = [
  {
    collection: 'agentEngineSignIns',
    table: 'harness.engine_signins',
    orgPath: 'orgId',
    key: [{ col: 'subject', path: '_id', kind: 'text', from: signInSubject }],
    columns: [{ col: 'user_id', path: 'userId', kind: 'text', absentAs: null }],
    doc: { omit: ['orgId', 'userId'] },
    newId: (_doc, row) => ({
      id: `${String(row.org_id)}:${String(row.subject)}`,
      oid: false,
    }),
  },
  {
    collection: 'agentMemories',
    table: 'harness.memories',
    orgPath: 'orgId',
    key: [{ col: 'memory_id', path: 'memoryId', kind: 'text' }],
    doc: { omit: [] },
    newId: orgScoped('memoryId'),
  },
  {
    collection: 'agentSchedules',
    table: 'harness.schedules',
    orgPath: 'orgId',
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    idCol: 'id',
    doc: { omit: [], idAs: '_id' },
  },
  {
    collection: 'agentScheduleFirings',
    table: 'harness.schedule_firings',
    orgPath: 'orgId',
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    idCol: 'id',
    doc: { omit: [], idAs: '_id' },
  },
  {
    collection: 'agentScheduleCounters',
    table: 'harness.schedule_counters',
    orgPath: 'orgId',
    key: [{ col: 'key', path: '_id', kind: 'text' }],
    idCol: 'key',
    columns: [
      { col: 'count', path: 'count', kind: 'int' },
      { col: 'updated_at', path: 'updatedAt', kind: 'int' },
      { col: 'expires_at', path: 'expiresAt', kind: 'datems', nullable: true },
    ],
  },
  {
    collection: 'agentHarnessInboxPreferences',
    table: 'harness.inbox_preferences',
    orgPath: 'orgId',
    key: [{ col: 'user_id', path: 'userId', kind: 'text' }],
    columns: [
      {
        col: 'muted_schedules',
        path: 'mutedSchedules',
        kind: 'texts',
        absentAs: [],
      },
      { col: 'paused_until', path: 'pausedUntil', kind: 'int', absentAs: null },
      {
        col: 'paused_until_resumed',
        path: 'pausedUntilResumed',
        kind: 'bool',
        absentAs: false,
      },
      {
        col: 'run_notice_email',
        path: 'runNoticeEmail',
        kind: 'bool',
        absentAs: false,
      },
      {
        col: 'run_notice_slack',
        path: 'runNoticeSlack',
        kind: 'bool',
        absentAs: false,
      },
      { col: 'updated_at', path: 'updatedAt', kind: 'int' },
    ],
    newId: orgScoped('userId'),
  },
  {
    collection: 'agentHarnessRunEndNotices',
    table: 'harness.run_end_notices',
    orgPath: 'orgId',
    key: [{ col: 'run_id', path: 'runId', kind: 'text' }],
    columns: [
      { col: 'user_id', path: 'userId', kind: 'text' },
      { col: 'created_at', path: 'createdAt', kind: 'datems' },
    ],
    newId: orgScoped('runId'),
  },
];

export const PEOPLES_DATA = docTableCarrier(
  'peoples-data',
  'settings and data people made: Claude sign-ins, memories, schedules with their fire history and counters, Inbox settings and run-end notice claims',
  PEOPLE_TABLES,
);
