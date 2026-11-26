/**
 * Timing run (skipped unless MIGRATE_SCALE=1): a synthetic organisation with many chats, messages (with tool output), citations,
 * files and activities is copied and verified. Sizes come from MIGRATE_SCALE_SESSIONS (default 5000; ten messages each).
 * The result is printed as JSON on stderr and written to MIGRATE_SCALE_OUT.
 */
import 'reflect-metadata';
import { writeFileSync } from 'node:fs';
import { expect } from 'chai';
import { ObjectId } from 'mongodb';
import { runCli } from '../../../src/scripts/docstore-migrate/cli';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { CONVERSATION_INVARIANTS } from '../../../src/scripts/docstore-migrate/mapping/conversation';
import { TestMongo, haveMongod, startMongo } from './mongo-harness';

const suite = process.env.MIGRATE_SCALE === '1' && haveStack && haveMongod ? describe : describe.skip;
const N = Number(process.env.MIGRATE_SCALE_SESSIONS ?? 5000);
const DB = 'es_scale';
const hex = (n: number): string => n.toString(16).padStart(24, '0');
const text = (n: number): string => ('lorem ipsum dolor sit amet '.repeat(Math.ceil(n / 27))).slice(0, n);

for (const bin of PG_BINS.length ? PG_BINS.slice(0, 1) : ['none']) {
  suite(`docstore-migrate timing (${bin})`, function () {
    this.timeout(3_600_000);
    let stack: PgStack;
    let mongo: TestMongo;
    after(async () => {
      await mongo?.stop();
      await stack?.stop();
    });

    it(`copies and verifies ${N} chats`, async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_scale', { importer: true });
      mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp');   // a replica set: the follower needs a change stream
      const db = mongo.client.db(DB);
      const org = new ObjectId(hex(1));
      const now = new Date('2026-05-01T10:00:00.000Z');
      const users = Array.from({ length: 200 }, (_, i) => ({ _id: new ObjectId(hex(1000 + i)), orgId: org, fullName: `User ${i}`, email: `u${i}@scale.example`, slug: `user-${i + 1}`, isDeleted: false, hasLoggedIn: true, createdAt: now, updatedAt: now, __v: 0 }));
      await db.collection('org').insertOne({ _id: org, slug: 'org-1001', registeredName: 'Scale', domain: 'scale.example', contactEmail: 'o@scale.example', accountType: 'business', isDeleted: false, createdAt: now, updatedAt: now, __v: 0 });
      await db.collection('users').insertMany(users as never[]);
      const t0 = Date.now();
      let msgs = 0;
      let cits = 0;
      for (let from = 0; from < N; from += 500) {
        const sessions: unknown[] = [];
        const messages: unknown[] = [];
        const citations: unknown[] = [];
        for (let i = from; i < Math.min(N, from + 500); i++) {
          const sid = new ObjectId(hex(100000 + i));
          const u = users[i % users.length]!._id;
          sessions.push({ _id: sid, sessionType: 'chat', nextSeq: 10, userId: u, orgId: org, title: `Chat ${i}`, initiator: u, isShared: i % 10 === 0, sharedWith: i % 10 === 0 ? [{ userId: users[(i + 1) % 200]!._id, accessLevel: 'read', _id: new ObjectId() }] : [], isDeleted: false, isArchived: false, lastActivityAt: now.getTime() + i, status: 'Complete', createdAt: now, updatedAt: now, __v: 1 });
          for (let s = 1; s <= 10; s++) {
            const bot = s % 2 === 0;
            const cids = bot ? [new ObjectId(), new ObjectId()] : [];
            for (const c of cids) citations.push({ _id: c, content: text(600), chunkIndex: 1, citationType: 'vectordb|document', metadata: { recordId: `rec-${i}`, recordName: 'doc', orgId: String(org) }, createdAt: now, updatedAt: now, __v: 0 });
            messages.push({ _id: new ObjectId(), sessionId: sid, orgId: org, seq: s, messageType: bot ? 'bot_response' : 'user_query', content: text(bot ? 1800 : 200), contentFormat: 'MARKDOWN', citations: cids.map((c) => ({ citationId: c })), followUpQuestions: [], feedback: [], modelInfo: { chatMode: 'quick' }, appliedFilters: {}, attachments: [], referenceData: [], tools: bot ? [{ name: 'search', result: text(3000) }] : [], reasoning: [], parts: [], authorUserId: u, createdAt: now, updatedAt: now });
          }
        }
        await db.collection('chatSessions').insertMany(sessions as never[]);
        await db.collection('chatSessionMessages').insertMany(messages as never[]);
        await db.collection('citations').insertMany(citations as never[]);
        msgs += messages.length;
        cits += citations.length;
      }
      const docs = Array.from({ length: Math.round(N * 4) }, (_, i) => ({ _id: new ObjectId(hex(5_000_000 + i)), orgId: org, documentName: `file-${i}.txt`, documentPath: `org/file-${i}`, isVersionedFile: false, initiatorUserId: users[i % 200]!._id, sizeInBytes: 1000 + i, mimeType: 'text/plain', extension: '.txt', versionHistory: [], customMetadata: [], isDeleted: false, createdAt: now.getTime(), updatedAt: now.getTime(), storageVendor: 'local', local: { localPath: `/x/${i}` }, __v: 0 }));
      for (let i = 0; i < docs.length; i += 5000) await db.collection('documents').insertMany(docs.slice(i, i + 5000) as never[]);
      const acts = Array.from({ length: N * 5 }, (_, i) => ({ _id: new ObjectId(), userId: users[i % 200]!._id, orgId: org, email: `u${i % 200}@scale.example`, activityType: 'LOGIN', ipAddress: '10.0.0.1', isDeleted: false, createdAt: now, updatedAt: now }));
      for (let i = 0; i < acts.length; i += 5000) await db.collection('userActivities').insertMany(acts.slice(i, i + 5000) as never[]);
      const seedMs = Date.now() - t0;
      const mongoBytes = (await db.stats()).dataSize;

      const env = {
        ...process.env,
        MONGO_URI: mongo.uri,
        MONGO_DB_NAME: DB,
        PG_IMPORT_URL: stack.cluster.url('migrate_scale', 'pipeshub_import').replace('pipeshub_import@', 'pipeshub_import:x@'),
        PG_NODE_URL: stack.cluster.url('migrate_scale', 'pipeshub_node_login', stack.bouncer.port),
        PG_MIGRATE_URL: stack.cluster.url('migrate_scale', 'pipeshub_migrate'),
        MIGRATE_SCHEMA_COMMAND: 'none',
        MIGRATE_PG_FREE_BYTES: String(10 ** 13),
      };
      const events: string[] = [];
      const run = async (args: string[], extra: NodeJS.ProcessEnv = {}): Promise<{ code: number; ms: number }> => {
        const w = process.stdout.write.bind(process.stdout);
        const e = process.stderr.write.bind(process.stderr);
        (process.stdout as any).write = (): boolean => true;
        (process.stderr as any).write = (x: string): boolean => (events.push(x), true);
        const t = Date.now();
        try {
          return { code: await runCli(args, { ...env, ...extra }), ms: Date.now() - t };
        } finally {
          (process.stdout as any).write = w;
          (process.stderr as any).write = e;
        }
      };
      const pre = await run(['preflight']);
      const fstart = await run(['follow', 'start']);
      const bulk = await run(['bulk']);
      const ver = await run(['verify']);
      const sampled = await run(['verify', '--sample', '0.05']);
      // the application keeps writing: a share of the messages and chats change, and the follower applies them
      const changedMsgs = (await db.collection('chatSessionMessages').updateMany({ seq: 1 } as never, { $set: { content: 'changed while following' } })).modifiedCount;
      const changedChats = (await db.collection('chatSessions').updateMany({ isShared: false } as never, { $set: { title: 'renamed while following' } })).modifiedCount;
      const follow = await run(['follow', 'run', '--until-idle', '5']);
      const ver2 = await run(['verify', '--sample', '0.05']);
      let seq: { ms: number; code: number } | undefined;
      if (process.env.MIGRATE_SCALE_SEQ === '1') {
        // the same copy again with one worker, for the comparison
        await run(['reset', '--yes']);
        seq = await run(['bulk'], { MIGRATE_PARALLEL: '1' });
      }
      const pgBytes = Number((await stack.admin((c) => c.query("SELECT sum(pg_total_relation_size(c.oid))::bigint AS n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname IN ('identity','conversation','content','access','oauth','platform')"))).rows[0].n);
      const invariantMs: Record<string, number> = {};
      for (const inv of CONVERSATION_INVARIANTS) {
        const t = Date.now();
        await stack.admin((c) => c.query(inv.sql));
        invariantMs[inv.name.slice(0, 40)] = Date.now() - t;
      }
      const result = { invariantMs, sessions: N, messages: msgs, citations: cits, documents: docs.length, activities: acts.length, mongoDataMB: Math.round(mongoBytes / 1e6), pgTotalMB: Math.round(pgBytes / 1e6), seedSeconds: Math.round(seedMs / 1000), preflightSeconds: Math.round(pre.ms / 1000), bulkSeconds: Math.round(bulk.ms / 1000), verifySeconds: Math.round(ver.ms / 1000), sampledVerifySeconds: Math.round(sampled.ms / 1000), followedEvents: changedMsgs + changedChats, followSeconds: Math.round(follow.ms / 1000), followEventsPerSecond: Math.round((changedMsgs + changedChats) / Math.max(1, follow.ms / 1000 - 5)), sampledVerifyAfterFollowSeconds: Math.round(ver2.ms / 1000), sequentialBulkSeconds: seq ? Math.round(seq.ms / 1000) : null, parallel: Number(process.env.MIGRATE_PARALLEL ?? 4), bulkRowsPerSecond: Math.round((N + msgs + cits + docs.length + acts.length) / (bulk.ms / 1000)), exit: { preflight: pre.code, bulk: bulk.code, verify: ver.code, sampled: sampled.code, follow: follow.code, afterFollow: ver2.code, followStart: fstart.code } };
      writeFileSync(process.env.MIGRATE_SCALE_OUT ?? '/dev/null', JSON.stringify(result, null, 1));
      if (process.env.MIGRATE_SCALE_EVENTS) writeFileSync(process.env.MIGRATE_SCALE_EVENTS, events.join(''));
      process.stderr.write(`${JSON.stringify(result, null, 1)}\n`);
      // every timed step must have worked: a step that failed fast would otherwise read as a fast one
      for (const [step, r] of Object.entries({ pre, fstart, bulk, ver, sampled, follow, ver2, ...(seq ? { seq } : {}) }))
        expect(r.code, `the ${step} step exited ${String(r.code)}`).to.equal(0);
    });
  });
}
