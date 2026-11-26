/**
 * P9 BR-2, BR-4, BR-5 on a MongoDB replica set: one session of two exported
 * from a source database and restored into a fresh one; a restore into a
 * database that holds the session, and a changed archive, are refused before
 * anything is written. (The drill with a resumed engine run is BR-3, Python.)
 *
 *   MONGO_IT_URI=mongodb://localhost:<port>/?replicaSet=rs0 npx mocha --no-config \
 *     --require reflect-metadata --require ts-node/register --exit \
 *     tests/modules/agent-harness/session-backup.mongo.itest.ts
 */
import { expect } from 'chai';
import { mkdir, mkdtemp, readdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import mongoose, { Connection } from 'mongoose';
import { ObjectId } from 'mongodb';
import { MongoSessionBackup } from '../../../src/libs/repositories/agent-harness/session-backup/mongo';
import { SessionBackupRefused } from '../../../src/modules/agent-harness/domain/session-backup';
import { runSessionBackupCli } from '../../../src/modules/agent-harness/ops/session-backup-cli';
import { harnessMongoUri } from './support/harness-mongo';

const ORG = new ObjectId().toHexString();
const KEEP = new ObjectId().toHexString();
const OTHER = new ObjectId().toHexString();

async function seed(db: NonNullable<Connection['db']>, sessionId: string, tag: string): Promise<void> {
  const runId = `run-${tag}`;
  const citation = new ObjectId();
  await db.collection('agentRuns').insertOne({ orgId: ORG, sessionId, runId, status: 'queued' });
  await db.collection('sessionEvents').insertMany([
    { orgId: ORG, sessionId, streamId: sessionId, seq: 1, type: 'run.admitted', runId, sealed: Buffer.from(tag) },
    { orgId: ORG, sessionId, streamId: `${sessionId}:child`, seq: 1, type: 'child.spawned', runId },
  ]);
  await db.collection('sessionStreamCounters').insertMany([
    { orgId: ORG, streamId: sessionId, lastSeq: 1 },
    { orgId: ORG, streamId: `${sessionId}:child`, lastSeq: 1 },
  ]);
  await db.collection('chatSessions').insertOne({ _id: new ObjectId(sessionId), orgId: new ObjectId(ORG), title: tag });
  await db.collection('chatSessionMessages').insertOne({
    sessionId: new ObjectId(sessionId),
    orgId: new ObjectId(ORG),
    citations: [{ citationId: citation }],
  });
  await db.collection('citations').insertOne({ _id: citation, metadata: { orgId: ORG } });
  await db.collection('agentCommandReceipts').insertOne({ orgId: ORG, runId, commandId: `cmd-${tag}` });
  await db.collection('agentJournalPayloads').insertOne({ _id: `p-${tag}` as never, orgId: ORG, owner: { sessionId, runId } });
  await db.collection('agentWorkspaceManifests').insertMany([
    { orgId: ORG, workspaceId: `ws:${sessionId}`, revision: 1 },
    { orgId: ORG, workspaceId: `ws:${sessionId}:skills:abc`, revision: 1 },
  ]);
  await db.collection('agentWorkspaceHeads').insertOne({ orgId: ORG, workspaceId: `ws:${sessionId}`, revision: 1 });
  await db.collection('agentWorkspaceKeys').insertOne({ orgId: ORG, sessionId, keyId: `k-${tag}` });
  await db.collection('agentEventCounters').insertOne({ orgId: ORG, sessionId, next: 2 });
}

describe('session backup and restore (P9, Mongo replica set)', function () {
  this.timeout(60_000);
  let source: Connection;
  let target: Connection;
  let archive: string;

  before(async () => {
    const uri = harnessMongoUri();
    source = await mongoose.createConnection(uri, { dbName: `pxh_p9_backup_src_${process.pid}` }).asPromise();
    target = await mongoose.createConnection(uri, { dbName: `pxh_p9_backup_dst_${process.pid}` }).asPromise();
    await seed(source.db!, KEEP, 'keep');
    await seed(source.db!, OTHER, 'other');
    await source.db!.collection('agentHarnessDataKeys').insertOne({ orgId: ORG, version: 1, wrapped: 'w' });
    await source.db!.collection('agentAuditRecords').insertOne({ orgId: ORG, sessionId: KEEP });
    await source.db!.collection('outbox_events').insertOne({ orderingKey: 'run-keep' });
    archive = await mkdtemp(join(tmpdir(), 'pxh-p9-backup-'));
  });

  after(async () => {
    await source.dropDatabase();
    await target.dropDatabase();
    await source.close();
    await target.close();
    await rm(archive, { recursive: true, force: true });
  });

  it('BR-2: exports exactly one session (and its org keys) and restores it into a fresh database', async () => {
    const manifest = await new MongoSessionBackup(source.db!).export(ORG, KEEP, archive);
    const counts = Object.fromEntries(Object.entries(manifest.collections).map(([k, v]) => [k, v.documents]));
    expect(counts).to.include({
      agentRuns: 1,
      sessionEvents: 2,
      sessionStreamCounters: 2,
      chatSessions: 1,
      chatSessionMessages: 1,
      citations: 1,
      agentCommandReceipts: 1,
      agentJournalPayloads: 1,
      agentWorkspaceManifests: 2,
      agentWorkspaceHeads: 1,
      agentWorkspaceKeys: 1,
      agentEventCounters: 1,
      agentHarnessDataKeys: 1,
    });
    expect(manifest.collections).to.not.have.property('agentAuditRecords');
    expect(manifest.collections).to.not.have.property('outbox_events');
    const text = await readFile(join(archive, 'sessionEvents.ndjson'), 'utf-8');
    expect(text).to.not.include(OTHER);

    await new MongoSessionBackup(target.db!).restore(archive);
    const restored = target.db!;
    expect(await restored.collection('agentRuns').countDocuments({ sessionId: KEEP })).to.equal(1);
    expect(await restored.collection('agentRuns').countDocuments({ sessionId: OTHER })).to.equal(0);
    const event = await restored.collection('sessionEvents').findOne({ sessionId: KEEP, seq: 1, streamId: KEEP });
    expect(Buffer.from((event!['sealed'] as { buffer: Uint8Array }).buffer).toString()).to.equal('keep');
    const chat = await restored.collection('chatSessions').findOne({ _id: new ObjectId(KEEP) });
    expect(chat?.['title']).to.equal('keep');
    expect(await restored.collection('agentAuditRecords').countDocuments({})).to.equal(0);
  });

  it('CLEANUP-S2: the archive (plaintext messages and citations) is readable by its maker only: directory 0700, files 0600, also when overwritten', async () => {
    await mkdir(archive, { recursive: true, mode: 0o755 });
    await writeFile(join(archive, 'chatSessions.ndjson'), 'old', { mode: 0o644 });
    await new MongoSessionBackup(source.db!).export(ORG, KEEP, archive);
    expect((await stat(archive)).mode & 0o777).to.equal(0o700);
    const names = await readdir(archive);
    expect(names).to.include.members(['manifest.json', 'chatSessions.ndjson']);
    for (const name of names) {
      expect((await stat(join(archive, name))).mode & 0o777, name).to.equal(0o600);
    }
  });

  it('BR-4: a database that already holds the session is refused, nothing written', async () => {
    const before = await target.db!.collection('sessionEvents').countDocuments({});
    try {
      await new MongoSessionBackup(target.db!).restore(archive);
      expect.fail('restored over an existing session');
    } catch (error) {
      expect(error).to.be.instanceOf(SessionBackupRefused);
      expect((error as SessionBackupRefused).reasonCode).to.equal('session_exists');
    }
    expect(await target.db!.collection('sessionEvents').countDocuments({})).to.equal(before);
  });

  it('BR-5: a changed archive is refused before any write', async () => {
    const fresh = await mongoose
      .createConnection(harnessMongoUri(), { dbName: `pxh_p9_backup_bad_${process.pid}` })
      .asPromise();
    try {
      const file = join(archive, 'agentRuns.ndjson');
      const original = await readFile(file, 'utf-8');
      await writeFile(file, original.replace('queued', 'running'));
      try {
        await new MongoSessionBackup(fresh.db!).restore(archive);
        expect.fail('restored a changed archive');
      } catch (error) {
        expect((error as SessionBackupRefused).reasonCode).to.equal('archive_corrupt');
      }
      expect(await fresh.db!.collection('agentRuns').countDocuments({})).to.equal(0);
      await writeFile(file, original);
    } finally {
      await fresh.dropDatabase();
      await fresh.close();
    }
  });

  it('the CLI exports and refuses with exit codes', async () => {
    const out = await mkdtemp(join(tmpdir(), 'pxh-p9-cli-'));
    const uri = harnessMongoUri();
    const dbName = `pxh_p9_backup_src_${process.pid}`;
    try {
      expect(await runSessionBackupCli(['export', '--mongo-uri', uri, '--db', dbName, '--org', ORG, '--session', KEEP, '--out', out])).to.equal(0);
      expect(
        await runSessionBackupCli(['restore', '--mongo-uri', uri, '--db', `pxh_p9_backup_dst_${process.pid}`, '--in', out]),
      ).to.equal(1);
      expect(await runSessionBackupCli(['restore', '--mongo-uri', uri])).to.equal(2);
    } finally {
      await rm(out, { recursive: true, force: true });
    }
  });
});
