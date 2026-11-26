/**
 * Durable runs, their journal and payloads on a migrated PostgreSQL (R46-CARRY, R45CARRY-FU-1), with MongoDB in memory: the copy and
 * the verify (payload names resolved on both sides), the harness's PostgreSQL stores reading what was carried (a run, its v1 events,
 * the journal and its counter, a claim-checked payload by the name MongoDB gave it, a receipt, the pending reaps), the forward
 * follower applying writes and deletes through the key map, the rollback after the application wrote to PostgreSQL, the reset, and
 * the key map's deployment-wide form.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { createHash } from 'node:crypto';
import { ObjectId, Timestamp } from 'mongodb';
import { system } from '../../../src/libs/context/orgContext';
import { withJobsUow } from '../../../src/libs/db';
import type { Tx as KernelTx } from '../../../src/libs/db';
import { PgTx } from '../../../src/libs/repositories/pg';
import { PgExecutionStore } from '../../../src/libs/repositories/agent-harness/execution/pg';
import { PgJournalStore } from '../../../src/libs/repositories/agent-harness/journal/pg';
import { PgPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/pg';
import { PgPendingReaps } from '../../../src/libs/repositories/agent-harness/pending-reaps/pg';
import { DURABLE_RUNS } from '../../../src/scripts/docstore-migrate/carry/durable-runs';
import { DocTable } from '../../../src/scripts/docstore-migrate/carry/doc-table';
import {
  applyCarried,
  clearCarried,
  copyCarried,
  restoreCarried,
  verifyCarried,
  type CarryPg,
} from '../../../src/scripts/docstore-migrate/carry/run';
import { follow } from '../../../src/scripts/docstore-migrate/follow';
import {
  rollbackOnline,
  writeBackCarried,
} from '../../../src/scripts/docstore-migrate/reverse-follow';
import { finalize } from '../../../src/scripts/docstore-migrate/finalize';
import { JobState } from '../../../src/scripts/docstore-migrate/state';
import { Pool } from 'pg';
import type { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import {
  PgStack,
  PG_BINS,
  haveStack,
  inOrg,
  startPgStack,
} from '../../stores/pg-store-harness';
import { FakeCarryMongo, clone } from './fake-carry-mongo';

const suite = haveStack ? describe : describe.skip;
const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const ORG2 = 'bbbbbbbbbbbbbbbbbbbbbbbb';
const SESSION = 'dddddddddddddddddddddddd';
const STREAM = `session:${SESSION}`;
const CARRIERS = [DURABLE_RUNS];

const pg: CarryPg = (fn, write) =>
  system('carry-runs-itest', () =>
    withJobsUow({ write, service: 'import' }, fn),
  );
const sql = <T extends Record<string, unknown>>(
  text: string,
  args: unknown[] = [],
): Promise<T[]> => pg(async (k) => (await k.query<T>(text, args)).rows, false);
const sha = (b: Buffer): string =>
  `sha256:${createHash('sha256').update(b).digest('hex')}`;

const SEALED_1 = Buffer.from('sealed claim-checked payload one');
const SEALED_2 = Buffer.from('sealed run content two');
const H1 = sha(SEALED_1);
const H2 = sha(SEALED_2);
const REF1 = `mongo:${ORG}:${H1}`;
const REF2 = `mongo:${ORG}:${H2}`;
const RUN_OID = new ObjectId('aaaaaaaaaaaaaaaaaaaa0001');

type Doc = Record<string, unknown>;

/** Documents as the MongoDB stores write them. */
function seed(): Record<string, Doc[]> {
  const run = (
    oid: ObjectId,
    runId: string,
    status: string,
    createdAt: number,
    extra: Doc = {},
  ): Doc => ({
    _id: oid,
    orgId: ORG,
    runId,
    sessionId: SESSION,
    actorId: 'u1',
    agentKey: null,
    status,
    revision: 3,
    harnessMajor: 2,
    attempts: 1,
    createdAt,
    updatedAt: createdAt + 10,
    absoluteDeadlineAt: createdAt + 3_600_000,
    outcome: null,
    attemptId: 'att-1',
    workerId: 'w-1',
    leaseExpiresAt: createdAt + 60_000,
    checkpoint: null,
    claimedFrom: null,
    notBefore: null,
    checkpointRef: null,
    input: { query: 'look at this', attachments: [`sealed:${runId}/${REF2}`] },
    waitingForWorker: null,
    activeSessionKey:
      status === 'running' ? JSON.stringify([ORG, SESSION]) : null,
    agentDefinition: null,
    engineProfile: null,
    modelAccess: null,
    via: 'session',
    pinned: null,
    ...extra,
  });
  return {
    agentRuns: [
      run(RUN_OID, 'run-1', 'running', 1_700_000_000_000),
      // the same millisecond: the insertion order breaks the tie
      run(
        new ObjectId('aaaaaaaaaaaaaaaaaaaa0002'),
        'run-0',
        'completed',
        1_700_000_000_000,
        { activeSessionKey: null },
      ),
    ],
    agentEvents: [
      {
        _id: new ObjectId(),
        orgId: ORG,
        sessionId: SESSION,
        runId: 'run-1',
        eventSeq: 1,
        commitSeq: 1,
        type: 'run.started',
        data: {},
        createdAt: 1_700_000_000_001,
      },
      {
        _id: new ObjectId(),
        orgId: ORG,
        sessionId: SESSION,
        runId: 'run-1',
        eventSeq: 2,
        commitSeq: 2,
        type: 'run.note',
        data: { n: 2 },
        createdAt: 1_700_000_000_002,
      },
    ],
    agentEventCounters: [
      {
        _id: new ObjectId(),
        orgId: ORG,
        sessionId: SESSION,
        nextEventSeq: 2,
        journalFeatures: ['journal.durable'],
        actors: ['u1', 'u2'],
      },
      // written before SS1/SS2: no lists, no /compact
      {
        _id: new ObjectId(),
        orgId: ORG,
        sessionId: 'old-session',
        nextEventSeq: 7,
      },
    ],
    agentNextRuns: [
      {
        _id: 'nr-1',
        orgId: ORG,
        sessionId: SESSION,
        actorId: 'u2',
        runId: 'run-2',
        behindRunId: 'run-1',
        status: 'pending',
        command: { input: { query: 'next' } },
        createdAt: 1_700_000_000_050,
      },
    ],
    sessionEvents: [
      {
        _id: new ObjectId(),
        orgId: ORG,
        sessionId: SESSION,
        streamId: STREAM,
        seq: 1,
        runId: 'run-1',
        type: 'tool.result',
        schemaVersion: 1,
        critical: false,
        ts: new Date('2026-11-01T10:00:00.000Z'),
        writer: 'worker',
        payloadRef: { ref: REF1, hash: H1, size: SEALED_1.length },
        visibility: 'internal',
      },
      {
        _id: new ObjectId(),
        orgId: ORG,
        sessionId: SESSION,
        streamId: STREAM,
        seq: 2,
        runId: 'run-1',
        type: 'message.added',
        schemaVersion: 1,
        critical: true,
        ts: new Date('2026-11-01T10:00:01.000Z'),
        writer: 'node',
        data: { content: [`sealed:run-1/${REF2}`] },
        visibility: 'public',
      },
    ],
    sessionStreamCounters: [
      {
        _id: new ObjectId(),
        orgId: ORG,
        streamId: STREAM,
        lastSeq: 2,
        ts: new Date('2026-11-01T10:00:01.000Z'),
      },
    ],
    agentJournalPayloads: [
      {
        _id: `${ORG}:${H1}`,
        orgId: ORG,
        hash: H1,
        size: SEALED_1.length,
        bytes: SEALED_1,
        createdAt: new Date('2026-11-01T10:00:00.000Z'),
        owner: { sessionId: SESSION, runId: 'run-1' },
        referenced: true,
      },
      // stored before E7: no owner, no mark
      {
        _id: `${ORG}:${H2}`,
        orgId: ORG,
        hash: H2,
        size: SEALED_2.length,
        bytes: SEALED_2,
        createdAt: new Date('2026-10-01T10:00:00.000Z'),
      },
    ],
    agentSessionForks: [
      {
        _id: 'fork-1',
        orgId: ORG,
        sourceSessionId: SESSION,
        actorId: 'u2',
        forkSessionId: 'ffffffffffffffffffffffff',
        result: null,
        createdAt: 1_700_000_000_100,
        completedAt: null,
      },
    ],
    agentSandboxReaps: [
      {
        _id: 'reap-1',
        orgId: ORG,
        labels: { orgId: ORG, runId: 'run-0' },
        failures: 2,
        nextAttemptAt: 1_700_000_000_500,
        createdAt: 1_700_000_000_400,
        lastErrorType: 'unavailable',
      },
      // a sandbox whose labels name no org
      {
        _id: 'reap-2',
        orgId: '',
        labels: { pool: 'warm' },
        failures: 1,
        nextAttemptAt: 1_700_000_000_600,
        createdAt: 1_700_000_000_400,
        lastErrorType: null,
      },
    ],
    agentSandboxSnapshots: [
      {
        _id: `${ORG}:obj-1`,
        orgId: ORG,
        sessionId: SESSION,
        runId: null,
        objectId: 'obj-1',
        takenAt: new Date('2026-11-01T10:05:00.000Z'),
        region: 'eu',
      },
    ],
    agentCommandReceipts: [
      {
        _id: new ObjectId(),
        orgId: ORG,
        runId: 'run-1',
        commandId: 'cmd-1',
        payloadHash: 'ph-1',
        receipt: { revision: 3, events: [1, 2] },
        createdAt: 1_700_000_000_200,
      },
    ],
    agentAdmissionReceipts: [
      {
        _id: new ObjectId(),
        scopeKey: 'scope-1',
        payloadHash: 'ph-a',
        result: {
          sessionId: SESSION,
          userMessageKey: 'k',
          run: { orgId: ORG, runId: 'run-1', input: { query: 'q' } },
          firstEventSeq: 1,
          lastEventSeq: 1,
          created: true,
        },
        createdAt: 1_700_000_000_000,
      },
    ],
    agentAdmissionQuotas: [
      {
        _id: JSON.stringify(['org', ORG]),
        windowStart: 1_700_000_000_000,
        count: 3,
        holds: [{ runId: 'run-1', at: 1_700_000_000_000 }],
      },
      // a window written before holds were kept
      {
        _id: JSON.stringify(['user', ORG2, 'u9']),
        windowStart: 1_700_000_000_000,
        count: 1,
      },
    ],
  };
}

type Event = Record<string, unknown>;
let token = 0;
const event = (
  op: 'insert' | 'update' | 'delete',
  coll: string,
  doc: Doc,
): Event => ({
  _id: { t: (token += 1) },
  operationType: op,
  ns: { db: 'pipeshub', coll },
  documentKey: { _id: doc._id },
  ...(op === 'delete' ? {} : { fullDocument: clone(doc) }),
  clusterTime: new Timestamp({ t: Math.floor(Date.now() / 1000), i: token }),
});

/** The driver surface `mongoCarry` uses (find with sort, bulk replace and update, delete by ids), over the in-memory documents. */
function fakeDb(m: FakeCarryMongo): unknown {
  const key = (id: unknown): string =>
    id instanceof ObjectId ? `o:${id.toHexString()}` : `s:${String(id)}`;
  return {
    collection: (name: string) => ({
      find: () => {
        let sorted = false;
        const cursor = {
          sort: () => {
            sorted = true;
            return cursor;
          },
          async *[Symbol.asyncIterator]() {
            const docs = m.docs(name).map((d) => clone(d));
            if (sorted) docs.sort((a, b) => (key(a._id) < key(b._id) ? -1 : 1));
            yield* docs;
          },
        };
        return cursor;
      },
      bulkWrite: async (ops: Array<Record<string, any>>) => {
        for (const o of ops) {
          if (o.replaceOne)
            m.put(name, {
              _id: o.replaceOne.filter._id,
              ...clone(o.replaceOne.replacement),
            });
          if (o.updateOne) {
            const cur = m.get(name, o.updateOne.filter._id);
            if (!cur) continue;
            for (const p of Object.keys(o.updateOne.update.$unset ?? {}))
              delete cur[p];
            Object.assign(cur, clone(o.updateOne.update.$set ?? {}));
          }
        }
      },
      deleteMany: async (q: { _id: { $in: unknown[] } }) => {
        let n = 0;
        for (const id of q._id.$in)
          if (m.get(name, id)) {
            m.del(name, id);
            n += 1;
          }
        return { deletedCount: n };
      },
    }),
  };
}

function scriptedRun(events: Event[], db?: unknown): MigrationRuntime {
  const queue = [...events];
  let last: unknown = null;
  const stream = {
    tryNext: async () => {
      const e = queue.shift() ?? null;
      if (e) last = e._id;
      return e;
    },
    get resumeToken() {
      return last;
    },
    close: async () => undefined,
  };
  return {
    cfg: { mongoDb: 'pipeshub', lagAlarmMs: 60_000, batchSize: 100 },
    mongo: {
      watch: () => stream,
      db: (name: string) =>
        name === 'admin'
          ? {
              command: async () => ({
                optimes: {
                  lastCommittedOpTime: {
                    ts: new Timestamp({ t: 2 ** 31 - 1, i: 0 }),
                  },
                },
              }),
            }
          : { collection: () => ({ findOne: async () => null }) },
    },
    db: () => db ?? { collection: () => ({}) },
    jobs: <T>(
      fn: (tx: PgTx, k: KernelTx) => Promise<T>,
      opts: { write?: boolean } = {},
    ) => pg((k) => fn(new PgTx(k), k), opts.write !== false),
  } as unknown as MigrationRuntime;
}

const job = (): JobState =>
  ({
    progress: {
      follow: { resumeToken: { t: 0 }, appliedUntil: null, events: 0 },
    },
    checkpoint: async () => undefined,
  }) as unknown as JobState;
const runFollow = (rt: MigrationRuntime): ReturnType<typeof follow> =>
  follow(rt, job(), { untilIdleMs: 300, log: () => undefined });
const failures = async (mongo: FakeCarryMongo) =>
  (await verifyCarried(pg, mongo, CARRIERS)).filter((x) => !x.ok);
const execution = (): PgExecutionStore =>
  new PgExecutionStore({} as never, {} as never);

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(
    `docstore-migrate: durable runs, journal and payloads on PostgreSQL (R46-CARRY, ${bin})`,
    function () {
      this.timeout(180_000);
      let stack: PgStack;
      let mongo: FakeCarryMongo;
      before(async () => {
        process.env.DOC_STORE = 'postgres';
        stack = await startPgStack(bin, 'migrate_carry_runs', {
          importer: true,
        });
      });
      after(async () => {
        await stack?.stop();
      });

      it('copies the group, and the verify finds every document in place and every payload name resolving as it did', async () => {
        mongo = new FakeCarryMongo(seed());
        const read = await copyCarried(pg, mongo, CARRIERS);
        expect(read).to.deep.include({
          'durable-runs:agentRuns': 2,
          'durable-runs:sessionEvents': 2,
          'durable-runs:agentJournalPayloads': 2,
          'durable-runs:agentSandboxReaps': 2,
        });
        const v = await verifyCarried(pg, mongo, CARRIERS);
        expect(
          v.filter((x) => !x.ok),
          JSON.stringify(v),
        ).to.deep.equal([]);
        expect(
          v.find((x) => x.collection === 'agentEventCounters')?.transforms,
        ).to.deep.equal({
          'agentEventCounters: absent journalFeatures read as []': 1,
          'agentEventCounters: absent actors read as []': 1,
        });
        expect(
          v.find((x) => x.collection === 'agentAdmissionQuotas')?.transforms,
        ).to.deep.equal({ 'agentAdmissionQuotas: absent holds read as []': 1 });
        expect(v.find((x) => x.collection === '(check)')).to.deep.include({
          ok: true,
          refused: [],
        });
        // runs keep MongoDB's insertion order (the tie-break of "newest first"), whatever order the scan found them in
        expect(
          await sql('SELECT run_id FROM harness.runs ORDER BY admission_order'),
        ).to.deep.equal([{ run_id: 'run-1' }, { run_id: 'run-0' }]);
        // the payload bytes are the sealed bytes, unchanged; names are not rewritten
        expect(
          (
            await sql<{ bytes: Buffer }>(
              'SELECT bytes FROM harness.journal_payloads WHERE id = $1',
              [`${ORG}:${H1}`],
            )
          )[0]?.bytes.equals(SEALED_1),
        ).to.equal(true);
        expect(
          (
            await sql<{ doc: { payloadRef: { ref: string } } }>(
              'SELECT doc FROM harness.session_events WHERE seq = 1',
            )
          )[0]?.doc.payloadRef.ref,
        ).to.equal(REF1);
        // a second copy changes nothing
        await copyCarried(pg, mongo, CARRIERS);
        expect(await failures(mongo)).to.deep.equal([]);
      });

      it("the harness's PostgreSQL stores read the carried runs, journal, receipts, reaps and payloads", async () => {
        await inOrg(ORG, async () => {
          const run = await execution().get(ORG, 'run-1');
          expect(run).to.deep.include({
            runId: 'run-1',
            status: 'running',
            revision: 3,
            harnessMajor: 2,
          });
          expect((await execution().getActive(ORG, SESSION))?.runId).to.equal(
            'run-1',
          );
          expect(
            (await execution().readEvents(ORG, SESSION, 0, 10)).map((e) => [
              e.eventSeq,
              e.type,
            ]),
          ).to.deep.equal([
            [1, 'run.started'],
            [2, 'run.note'],
          ]);
          expect(
            await execution().findReceipt(ORG, 'run-1', 'cmd-1'),
          ).to.deep.include({ payloadHash: 'ph-1' });
          const journal = new PgJournalStore();
          const events = await journal.readStream(ORG, STREAM, 0, 10);
          expect(events.map((e) => [e.seq, e.type, e.ts])).to.deep.equal([
            [1, 'tool.result', '2026-11-01T10:00:00.000Z'],
            [2, 'message.added', '2026-11-01T10:00:01.000Z'],
          ]);
          expect(await journal.lastSeq(ORG, [STREAM])).to.deep.equal({
            [STREAM]: 2,
          });
          // the claim-check and the content ref read by the names MongoDB gave them
          const payloads = new PgPayloadStore();
          expect(
            (await payloads.get(ORG, events[0]!.payloadRef!.ref))?.equals(
              SEALED_1,
            ),
          ).to.equal(true);
          expect((await payloads.get(ORG, REF2))?.equals(SEALED_2)).to.equal(
            true,
          );
          expect(await payloads.get(ORG2, REF2)).to.equal(null);
        });
        // the journal goes on from the carried counter: the next append is seq 3
        await pg(
          (k) =>
            k.query(
              'UPDATE harness.stream_counters SET last_seq = last_seq + 1 WHERE stream_id = $1 RETURNING last_seq',
              [STREAM],
            ),
          true,
        );
        expect(
          await sql('SELECT last_seq::int AS s FROM harness.stream_counters'),
        ).to.deep.equal([{ s: 3 }]);
        await pg(
          (k) =>
            k.query(
              'UPDATE harness.stream_counters SET last_seq = 2 WHERE stream_id = $1',
              [STREAM],
            ),
          true,
        );
        const due = await system('carry-runs-itest', () =>
          new PgPendingReaps().due(1_800_000_000_000, 10),
        );
        expect(due.map((r) => [r.id, r.labels, r.failures])).to.deep.equal([
          ['reap-1', { orgId: ORG, runId: 'run-0' }, 2],
          ['reap-2', { pool: 'warm' }, 1],
        ]);
      });

      it('the payload check sees a payload lost or changed in the copy, and leaves one already missing on MongoDB as it was', async () => {
        await pg(
          (k) =>
            k.query(
              "UPDATE harness.journal_payloads SET bytes = 'x'::bytea WHERE id = $1",
              [`${ORG}:${H2}`],
            ),
          true,
        );
        await pg(
          (k) =>
            k.query('DELETE FROM harness.journal_payloads WHERE id = $1', [
              `${ORG}:${H1}`,
            ]),
          true,
        );
        const check = (await verifyCarried(pg, mongo, CARRIERS)).find(
          (x) => x.collection === '(check)',
        );
        expect(check?.ok).to.equal(false);
        expect(check?.refused).to.include(
          `the payload ${ORG} ${REF1} resolves differently: MongoDB ok, PostgreSQL missing`,
        );
        expect(check?.refused).to.include(
          `the payload ${ORG} ${REF2} resolves differently: MongoDB ok, PostgreSQL corrupt`,
        );
        expect(JSON.stringify(check)).not.to.contain('sealed claim-checked');
        await copyCarried(pg, mongo, CARRIERS);
        expect(await failures(mongo)).to.deep.equal([]);
        // a name that was already dangling on MongoDB stays dangling, the same on both sides
        const gone = `mongo:${ORG}:${sha(Buffer.from('never stored'))}`;
        const dangling = {
          _id: new ObjectId(),
          orgId: ORG,
          sessionId: SESSION,
          streamId: 'session:other',
          seq: 1,
          type: 't',
          schemaVersion: 1,
          critical: false,
          ts: new Date(0),
          writer: 'node',
          payloadRef: { ref: gone, hash: 'sha256:00', size: 1 },
          visibility: 'internal',
        };
        mongo.put('sessionEvents', dangling);
        await copyCarried(pg, mongo, CARRIERS);
        expect(await failures(mongo)).to.deep.equal([]);
        mongo.del('sessionEvents', dangling._id);
        await applyCarried(
          pg,
          'sessionEvents',
          [],
          [String(dangling._id)],
          CARRIERS,
        );
        expect(
          await sql(
            'SELECT 1 FROM harness.session_events WHERE stream_id = $1',
            ['session:other'],
          ),
        ).to.deep.equal([]);
      });

      it('the follower applies writes and deletes through the key map: an erased session leaves nothing behind on PostgreSQL', async () => {
        // the documents MongoDB holds (the follower's events name their ids)
        const r1 = mongo.get('agentRuns', RUN_OID)!;
        const ended = {
          ...r1,
          status: 'completed',
          revision: 4,
          activeSessionKey: null,
        };
        const byseq = (n: number): Doc =>
          mongo
            .docs('sessionEvents')
            .find((d) => d.seq === n && d.streamId === STREAM)!;
        const [e1, e2] = [byseq(1), byseq(2)];
        const third = {
          ...e2,
          _id: new ObjectId(),
          seq: 3,
          data: { text: 'later' },
        };
        const [counter] = mongo.docs('sessionStreamCounters');
        const moved = { ...counter!, lastSeq: 3 };
        const p2 = mongo.get('agentJournalPayloads', `${ORG}:${H2}`)!;
        const [receipt] = mongo.docs('agentCommandReceipts');
        const reap2 = mongo.get('agentSandboxReaps', 'reap-2')!;
        const r = await runFollow(
          scriptedRun([
            event('update', 'agentRuns', ended),
            event('insert', 'sessionEvents', third),
            event('update', 'sessionStreamCounters', moved),
            event('delete', 'sessionEvents', { _id: e1._id }),
            event('delete', 'agentJournalPayloads', { _id: p2._id }),
            event('delete', 'agentCommandReceipts', { _id: receipt!._id }),
            event('delete', 'agentSandboxReaps', { _id: reap2._id }),
          ]),
        );
        expect(r.applied).to.equal(7);
        mongo.put('agentRuns', ended);
        mongo.put('sessionEvents', third);
        mongo.put('sessionStreamCounters', moved);
        mongo.del('sessionEvents', e1._id);
        mongo.del('agentJournalPayloads', p2._id);
        mongo.del('agentCommandReceipts', receipt!._id);
        mongo.del('agentSandboxReaps', reap2._id);
        // the content ref of seq 2 now dangles on both sides alike
        expect(
          await failures(mongo),
          JSON.stringify(await failures(mongo)),
        ).to.deep.equal([]);
        expect(
          await sql(
            'SELECT seq::int AS seq FROM harness.session_events ORDER BY seq',
          ),
        ).to.deep.equal([{ seq: 2 }, { seq: 3 }]);
        expect(
          await sql(
            'SELECT 1 FROM platform.docstore_keymap WHERE source_id = $1',
            [String(e1._id)],
          ),
        ).to.deep.equal([]);
        expect(
          await sql('SELECT 1 FROM harness.command_receipts'),
        ).to.deep.equal([]);
        expect(await sql('SELECT id FROM harness.sandbox_reaps')).to.deep.equal(
          [{ id: 'reap-1' }],
        );
        expect(
          await sql("SELECT status FROM harness.runs WHERE run_id = 'run-1'"),
        ).to.deep.equal([{ status: 'completed' }]);
      });

      it("the reverse follower's sweep writes back what changed on PostgreSQL in the bridge window, and a second sweep writes nothing", async () => {
        await pg(
          (k) =>
            k.query(
              `UPDATE harness.runs SET doc = jsonb_set(doc, '{label}', '{"level":"internal"}') WHERE run_id = 'run-0'`,
            ),
          true,
        );
        const rt = scriptedRun([], fakeDb(mongo));
        const first = await writeBackCarried(rt);
        expect(first.written).to.be.greaterThan(0);
        expect(first.removed).to.equal(0);
        expect(
          mongo.docs('agentRuns').find((d) => d.runId === 'run-0')?.label,
        ).to.deep.equal({ level: 'internal' });
        // what a sweep wrote MongoDB now holds: the next sweep (and a rollback's freeze) writes only what changes after it
        const second = await writeBackCarried(rt);
        expect(second).to.deep.include({ written: 0, removed: 0 });
        expect(second.unchanged).to.equal(first.written + first.unchanged);
        expect(await failures(mongo)).to.deep.equal([]);
      });

      it('a rollback after the application wrote to PostgreSQL gives every document its id back, removes what PostgreSQL removed, and keeps the payloads readable by either name', async () => {
        await inOrg(ORG, async () => {
          const ref = await new PgPayloadStore().put(
            ORG,
            sha(Buffer.from('written on pg')),
            Buffer.from('written on pg'),
            { sessionId: SESSION },
          );
          expect(ref.startsWith('pg:')).to.equal(true);
        });
        await pg(
          (k) =>
            k.query(
              "INSERT INTO harness.session_events (org_id, stream_id, seq, session_id, run_id, ts, doc, region) VALUES ($1, $2, 4, $3, 'run-1', '2026-11-02T00:00:00Z', $4::jsonb, 'local')",
              [
                ORG,
                STREAM,
                SESSION,
                JSON.stringify({
                  orgId: ORG,
                  sessionId: SESSION,
                  streamId: STREAM,
                  seq: 4,
                  runId: 'run-1',
                  type: 'run.completed',
                  schemaVersion: 1,
                  critical: true,
                  ts: { $date: Date.parse('2026-11-02T00:00:00Z') },
                  writer: 'node',
                  visibility: 'public',
                }),
              ],
            ),
          true,
        );
        await pg(
          (k) => k.query("DELETE FROM harness.next_runs WHERE id = 'nr-1'"),
          true,
        );
        const runIds = mongo.docs('agentRuns').map((d) => String(d._id));
        const out = await restoreCarried(pg, mongo, CARRIERS);
        // only what changed since the last sweep is written
        expect(out['durable-runs:sessionEvents']).to.deep.equal({
          written: 1,
          unchanged: 2,
          removed: 0,
        });
        expect(out['durable-runs:agentNextRuns']).to.deep.equal({
          written: 0,
          unchanged: 0,
          removed: 1,
        });
        expect(out['durable-runs:agentRuns']).to.deep.equal({
          written: 0,
          unchanged: 2,
          removed: 0,
        });
        expect(
          mongo
            .docs('agentRuns')
            .map((d) => String(d._id))
            .sort(),
        ).to.deep.equal(runIds.sort());
        expect(mongo.get('agentRuns', RUN_OID)).to.deep.include({
          status: 'completed',
          revision: 4,
        });
        const fresh = mongo.docs('sessionEvents').find((d) => d.seq === 4);
        expect(fresh?._id).to.be.instanceOf(ObjectId);
        expect(fresh?.ts).to.deep.equal(new Date('2026-11-02T00:00:00Z'));
        const back = mongo.get(
          'agentJournalPayloads',
          `${ORG}:${sha(Buffer.from('written on pg'))}`,
        );
        expect(back).to.deep.include({
          orgId: ORG,
          owner: { sessionId: SESSION },
          referenced: false,
        });
        expect(
          Buffer.isBuffer(back?.bytes) &&
            (back?.bytes as Buffer).equals(Buffer.from('written on pg')),
        ).to.equal(true);
        expect(mongo.get('agentSandboxReaps', 'reap-1')).to.deep.include({
          orgId: ORG,
          failures: 2,
        });
        expect(
          mongo.get('agentSandboxSnapshots', `${ORG}:obj-1`),
        ).to.deep.include({ region: 'eu', runId: null });
        expect(await failures(mongo)).to.deep.equal([]);
      });

      it("the key map's deployment-wide form: a row no org owns, not keyed by its id, is followed, deleted and rolled back by id", async () => {
        // a deployment-wide table keyed by a field (the shape of a backfill plan keyed by its digest)
        const t = new DocTable({
          collection: 'qaDeploymentRows',
          table: 'harness.sandbox_reaps',
          deployment: true,
          noRegion: true,
          key: [{ col: 'id', path: 'key', kind: 'text' }],
          columns: [
            { col: 'labels', path: 'labels', kind: 'json' },
            { col: 'failures', path: 'failures', kind: 'int' },
            { col: 'next_attempt_at', path: 'nextAttemptAt', kind: 'float' },
            { col: 'created_at', path: 'createdAt', kind: 'float' },
          ],
        });
        await pg((k) => k.query('DELETE FROM harness.sandbox_reaps'), true);
        const oid = new ObjectId();
        const doc = {
          key: 'plan-digest-1',
          labels: {},
          failures: 0,
          nextAttemptAt: 1,
          createdAt: 1,
        };
        await pg(
          (k) => t.write(k, [{ id: oid.toHexString(), oid: true, doc }]),
          true,
        );
        expect(
          await sql(
            'SELECT collection, source_id, source_oid, row_key FROM platform.docstore_keymap_deployment',
          ),
        ).to.deep.equal([
          {
            collection: 'qaDeploymentRows',
            source_id: oid.toHexString(),
            source_oid: true,
            row_key: '["plan-digest-1"]',
          },
        ]);
        expect(
          await sql(
            'SELECT 1 FROM platform.docstore_keymap WHERE collection = $1',
            ['qaDeploymentRows'],
          ),
        ).to.deep.equal([]);
        const actual = await pg((k) => t.actual(k), false);
        expect([...actual.keys()]).to.deep.equal([
          '(deployment)|["plan-digest-1"]',
        ]);
        expect(
          (actual.get('(deployment)|["plan-digest-1"]') as Doc)._id,
        ).to.equal(oid.toHexString());
        const fake = new FakeCarryMongo();
        await pg((k) => t.restore(k, fake), true);
        expect(fake.get('qaDeploymentRows', oid)).to.deep.include({
          key: 'plan-digest-1',
        });
        await pg((k) => t.remove(k, [oid.toHexString()]), true);
        expect(await sql('SELECT 1 FROM harness.sandbox_reaps')).to.deep.equal(
          [],
        );
        expect(
          await sql('SELECT 1 FROM platform.docstore_keymap_deployment'),
        ).to.deep.equal([]);
      });

      it('finalize ends the bridge window of a finished move: it removes the key map (both forms), and an online rollback is refused from then on', async () => {
        const migratePool = new Pool({
          connectionString: stack.cluster.url(stack.db, 'pipeshub_migrate'),
          max: 3,
        });
        let fenceMode = 'readonly';
        const db = {
          collection: (name: string) =>
            name === 'docstore_fence'
              ? {
                  findOne: async () => ({
                    _id: 'fence',
                    mode: fenceMode,
                    epoch: 2,
                  }),
                }
              : {},
        };
        const rt = {
          ...(scriptedRun([], db) as unknown as Record<string, unknown>),
          migratePool,
        } as unknown as MigrationRuntime;
        const log = (): void => undefined;
        const epoch = (backend: string) =>
          stack.admin((c) =>
            c.query(
              'UPDATE platform.store_epoch SET backend = $1, readonly = false',
              [backend],
            ),
          );
        try {
          await copyCarried(pg, mongo, CARRIERS);
          await pg(
            (k) =>
              new DocTable({
                collection: 'qaDeploymentRows',
                table: 'harness.sandbox_reaps',
                deployment: true,
                noRegion: true,
                key: [{ col: 'id', path: 'key', kind: 'text' }],
                columns: [
                  { col: 'labels', path: 'labels', kind: 'json' },
                  { col: 'failures', path: 'failures', kind: 'int' },
                  {
                    col: 'next_attempt_at',
                    path: 'nextAttemptAt',
                    kind: 'float',
                  },
                  { col: 'created_at', path: 'createdAt', kind: 'float' },
                ],
              }).write(k, [
                {
                  id: 'q1',
                  oid: false,
                  doc: {
                    key: 'k1',
                    labels: {},
                    failures: 0,
                    nextAttemptAt: 1,
                    createdAt: 1,
                  },
                },
              ]),
            true,
          );
          const mapped = async (): Promise<number> =>
            Number(
              (
                await sql<{ n: string }>(
                  'SELECT ((SELECT count(*) FROM platform.docstore_keymap) + (SELECT count(*) FROM platform.docstore_keymap_deployment))::text AS n',
                )
              )[0]?.n,
            );
          const before = await mapped();
          expect(before).to.be.greaterThan(1);
          await epoch('mongo');
          expect((await finalize(rt, { log })).message).to.contain(
            'not PostgreSQL',
          );
          await epoch('postgres');
          expect((await finalize(rt, { log })).message).to.contain(
            'MongoDB is not retired',
          );
          fenceMode = 'retired';
          await stack.admin((c) => c.query('DELETE FROM platform.jobs'));
          expect((await finalize(rt, { log })).message).to.contain(
            'nothing to finalise',
          );
          // the flip to PostgreSQL, then the reverse follower attached to a job of its own
          const move = await JobState.attach(rt);
          move.progress.phase = 'cutover_done';
          await move.checkpoint();
          await move.finish('done');
          const follower = await JobState.attach(rt);
          expect((await finalize(rt, { log })).message).to.contain(
            'a migration job is running',
          );
          await follower.finish('done');
          const f = await finalize(rt, { log });
          expect(f).to.deep.include({ ok: true, keys: before });
          expect(await mapped()).to.equal(0);
          expect(
            await rollbackOnline(rt, { log, maxFreezeMs: 1000 }),
          ).to.deep.include({ ok: false, stage: 'finalised' });
          expect(await finalize(rt, { log })).to.deep.include({
            ok: true,
            message: 'the move was already finalised',
          });
        } finally {
          await epoch('mongo');
          await stack.admin((c) => c.query('DELETE FROM platform.jobs'));
          await migratePool.end();
        }
      });

      it('reset removes every carried row and both forms of the key map', async () => {
        await pg((k) => clearCarried(k, CARRIERS), true);
        for (const tbl of [
          ...DURABLE_RUNS.tables,
          'platform.docstore_keymap',
          'platform.docstore_keymap_deployment',
        ])
          expect(
            (
              await sql<{ n: string }>(`SELECT count(*)::text AS n FROM ${tbl}`)
            )[0]?.n,
            tbl,
          ).to.equal('0');
      });
    },
  );
}
