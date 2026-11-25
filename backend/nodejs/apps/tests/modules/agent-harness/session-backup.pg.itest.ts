/**
 * Session backup and restore (P9) on PostgreSQL: a session's rows out of one database and into another, verified
 * whole before anything is written, refused into a database that holds the session, and never carrying a row of
 * another organisation. Runs with the harness PostgreSQL suites (`npm run test:pg-harness`).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Client } from 'pg';
import { PgSessionBackup, SESSION_BACKUP_PG_TABLES } from '../../../src/libs/repositories/agent-harness/session-backup/pg';
import {
  SESSION_BACKUP_COVERAGE,
  SessionBackupRefused,
} from '../../../src/modules/agent-harness/domain/session-backup';
import { newObjectId } from '../../../src/libs/repositories/pg-util';
import { PgStack, hexOrg, inOrg, pgBins, pgSuite, resetHarnessPg, startHarnessPg } from './support/harness-pg';

const seed = async (c: Client, org: string, session: string, other: string): Promise<void> => {
  const citation = newObjectId();
  for (const [o, s] of [[org, session], [other, session]] as const) {
    await c.query(
      `INSERT INTO conversation.chat_sessions (org_id, id, user_id, initiator, created_at, updated_at) VALUES ($1, $2, $3, $3, now(), now())`,
      [o, s, newObjectId()],
    );
    await c.query(
      `INSERT INTO harness.runs (org_id, run_id, doc, region) VALUES ($1, $2, $3, 'default')`,
      [o, `run-${o}`, JSON.stringify({ orgId: o, runId: `run-${o}`, sessionId: s, status: 'completed', revision: 3, createdAt: 1, updatedAt: 2 })],
    );
    await c.query(
      `INSERT INTO harness.session_events (org_id, stream_id, seq, session_id, run_id, ts, doc, region) VALUES ($1, $2, 1, $2, $3, now(), $4, 'default')`,
      [o, s, `run-${o}`, JSON.stringify({ type: 'turn.started', bytes: { $bin: 'AAE=' } })],
    );
    await c.query(`INSERT INTO harness.stream_counters (org_id, stream_id, last_seq, ts, region) VALUES ($1, $2, 1, now(), 'default')`, [o, s]);
    await c.query(
      `INSERT INTO harness.command_receipts (org_id, run_id, command_id, payload_hash, receipt, created_at, region) VALUES ($1, $2, 'c1', 'h', '{"ok":true}', 5, 'default')`,
      [o, `run-${o}`],
    );
    await c.query(
      `INSERT INTO harness.data_keys (org_id, kid, version, kek_id, wrapped, created_at_ms, region) VALUES ($1, $2, 1, 'kek', 'wrapped', 1, 'default')`,
      [o, `kid-${o}`],
    );
  }
  await c.query(
    `INSERT INTO conversation.chat_messages (org_id, session_id, seq, id, message_type, content, citations, created_at, updated_at, sender_id)
     VALUES ($1, $2, 1, $3, 'bot_response', 'hello', $4, now(), now(), $5)`,
    [org, session, newObjectId(), JSON.stringify([{ citationId: citation }]), newObjectId()],
  );
  await c.query(
    `INSERT INTO conversation.citations (org_id, id, content, chunk_index, citation_type, record_id, metadata, created_at, updated_at)
     VALUES ($1, $2, 'quoted', 0, 'vectordb|document', 'rec-1', '{}', now(), now())`,
    [org, citation],
  );
};

const dump = async (c: Client, org: string): Promise<Record<string, unknown[]>> => {
  const out: Record<string, unknown[]> = {};
  for (const table of Object.keys(SESSION_BACKUP_PG_TABLES)) {
    const { rows } = await c.query<{ r: unknown }>(`SELECT to_jsonb(t) AS r FROM ${table} t WHERE org_id = $1 ORDER BY 1`, [org]);
    out[table] = rows.map((x) => x.r);
  }
  return out;
};

const refusal = async (work: Promise<unknown>): Promise<string> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(SessionBackupRefused);
    return (error as SessionBackupRefused).reasonCode;
  }
  return expect.fail('expected a refusal');
};

for (const bin of pgBins()) {
  pgSuite(`agent harness session backup on PostgreSQL (${bin})`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_backup');
    });
    after(async () => {
      await stack?.stop();
    });
    beforeEach(async () => {
      await resetHarnessPg(stack);
      await stack.admin((c) => c.query('TRUNCATE conversation.chat_sessions, conversation.citations CASCADE'));
    });

    it('every table it copies is a Mongo store the backup coverage copies, and every copied store has its table', () => {
      const copied = Object.entries(SESSION_BACKUP_COVERAGE)
        .filter(([, coverage]) => 'copy' in coverage)
        .map(([name]) => name);
      const pg = new Set(Object.values(SESSION_BACKUP_PG_TABLES));
      for (const name of pg) expect(copied, name).to.include(name);
      // the two pre-repository conversation collections have no PostgreSQL table: main's store never held them there
      expect(copied.filter((name) => !pg.has(name)).sort()).to.deep.equal(['agentconversations', 'conversations']);
    });

    it('a restore into an emptied database brings back exactly the session rows of its org, and no other org\'s', async () => {
      const org = hexOrg();
      const other = hexOrg();
      const session = newObjectId();
      await stack.admin((c) => seed(c, org, session, other));
      const before = await stack.admin((c) => dump(c, org));
      const theirs = await stack.admin((c) => dump(c, other));
      const dir = await mkdtemp(join(tmpdir(), 'pg-backup-'));
      const backup = new PgSessionBackup();
      // The CLI binds the org it is told (`--org`), as a job binds its own.
      const manifest = await inOrg(org, () => backup.export(org, session, dir));
      expect(manifest.collections['conversation.citations']?.documents).to.equal(1);
      expect(manifest.collections['harness.command_receipts']?.documents).to.equal(1);
      expect(await refusal(backup.restore(dir))).to.equal('session_exists');
      await stack.admin(async (c) => {
        for (const table of Object.keys(SESSION_BACKUP_PG_TABLES).filter((t) => t !== 'harness.data_keys'))
          await c.query(`DELETE FROM ${table} WHERE org_id = $1`, [org]);
      });
      await backup.restore(dir);
      expect(await stack.admin((c) => dump(c, org))).to.deep.equal(before);
      expect(await stack.admin((c) => dump(c, other))).to.deep.equal(theirs);
    });

    it('refuses a changed file and a row of another organisation before writing anything', async () => {
      const org = hexOrg();
      const session = newObjectId();
      await stack.admin((c) => seed(c, org, session, hexOrg()));
      const dir = await mkdtemp(join(tmpdir(), 'pg-backup-'));
      await inOrg(org, () => new PgSessionBackup().export(org, session, dir));
      const file = join(dir, 'harness.runs.ndjson');
      const original = await readFile(file, 'utf-8');
      await writeFile(file, original.replace(org, hexOrg()));
      expect(await refusal(new PgSessionBackup().restore(dir))).to.equal('archive_corrupt');
    });
  });
}
