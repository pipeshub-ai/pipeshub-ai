/**
 * The ledgers, people's data and erasure carriers on a migrated PostgreSQL (R46-CARRY, R45CARRY-FU-2), with MongoDB in memory:
 * documents shaped as the MongoDB stores write them are copied, verified (listed transforms counted), read back through the
 * harness's own PostgreSQL stores, followed (writes and deletes through `follow()`), and rolled back. The erasure cases: an erasure
 * asked for during the follow (its job's insert and the erased documents' deletes) is applied on PostgreSQL, and data erased on
 * PostgreSQL after the move does not come back to MongoDB with a rollback.
 *
 * The stack logs in as both the import login (the copy) and the jobs login (R46CARRY-FU-3), so a store call that runs as the jobs
 * login (the erasure backfill's deployment-wide scans) reads the carried rows back through the store itself.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Timestamp } from 'mongodb';
import { system } from '../../../src/libs/context/orgContext';
import { withJobsUow } from '../../../src/libs/db';
import type { Tx as KernelTx } from '../../../src/libs/db';
import { PgTx } from '../../../src/libs/repositories/pg';
import { PgBudgetLedger } from '../../../src/libs/repositories/agent-harness/budget-ledger/pg';
import { PgMemoryStore } from '../../../src/libs/repositories/agent-harness/memory/pg';
import { PgScheduleStore } from '../../../src/libs/repositories/agent-harness/schedules/pg';
import { PgInboxStore } from '../../../src/libs/repositories/agent-harness/inbox/pg';
import { PgEngineSignIns } from '../../../src/libs/repositories/agent-harness/engine-signin/pg';
import { PgErasureJobStore } from '../../../src/libs/repositories/agent-harness/session-erasure/pg';
import { PgPrincipalErasureJobs } from '../../../src/libs/repositories/agent-harness/principal-erasure/pg';
import { PgErasureBackfillStore } from '../../../src/libs/repositories/agent-harness/erasure-backfill/pg';
import { CARRIERS } from '../../../src/scripts/docstore-migrate/carry';
import { LEDGERS } from '../../../src/scripts/docstore-migrate/carry/ledgers';
import { PEOPLES_DATA } from '../../../src/scripts/docstore-migrate/carry/people';
import { ERASURE_JOBS } from '../../../src/scripts/docstore-migrate/carry/erasure';
import { clearCarried, copyCarried, restoreCarried, verifyCarried, type CarryPg } from '../../../src/scripts/docstore-migrate/carry/run';
import { follow } from '../../../src/scripts/docstore-migrate/follow';
import type { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import type { JobState } from '../../../src/scripts/docstore-migrate/state';
import type { Doc } from '../../../src/scripts/docstore-migrate/carry/types';
import { PgStack, PG_BINS, haveStack, inOrg, startPgStack } from '../../stores/pg-store-harness';
import { FakeCarryMongo, clone } from './fake-carry-mongo';

const suite = haveStack ? describe : describe.skip;
const MINE = [LEDGERS, PEOPLES_DATA, ERASURE_JOBS];

const pg: CarryPg = (fn, write) => system('carry-people-itest', () => withJobsUow({ write, service: 'import' }, fn));
const sql = <T extends Record<string, unknown>>(text: string, args: unknown[] = []): Promise<T[]> =>
  pg(async (k) => (await k.query<T>(text, args)).rows, false);

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const ORG2 = 'bbbbbbbbbbbbbbbbbbbbbbbb';
const U1 = 'cccccccccccccccccccccccc';
const U2 = 'cccccccccccccccccccccccd';
const S1 = 'dddddddddddddddddddddddd';
const S2 = 'ddddddddddddddddddddddde';
const DIGEST = 'sha256:' + 'f'.repeat(64);
const amount = (usd: number, tokens = 0): Doc => ({ usdMicros: usd, tokens });

/** Documents as the MongoDB stores write them. */
function seed(): Record<string, Doc[]> {
  return {
    agentHarnessBudgetAccounts: [
      {
        _id: `${ORG}:run-1`,
        orgId: ORG,
        runId: 'run-1',
        rootRunId: 'run-1',
        parentRunId: null,
        ancestors: [],
        cap: { usdMicros: 5_000_000, tokens: null },
        committed: amount(1_000),
        reserved: amount(200),
        used: { inputTokens: 10, outputTokens: 5, cacheReadTokens: 0, cacheWriteTokens: 0 },
        closed: false,
        openedAt: 1_700_000_000_000,
      },
      {
        _id: `${ORG}:run-2`,
        orgId: ORG,
        runId: 'run-2',
        rootRunId: 'run-1',
        parentRunId: 'run-1',
        ancestors: ['run-1'],
        cap: { usdMicros: 1_000_000, tokens: null },
        committed: amount(0),
        reserved: amount(0),
        closed: false,
        openedAt: 1_700_000_000_100,
      },
    ],
    agentHarnessBudgetReservations: [
      {
        _id: `${ORG}:run-1:call-1`,
        orgId: ORG,
        runId: 'run-1',
        rootRunId: 'run-1',
        key: 'call-1',
        accounts: ['run-1'],
        requested: amount(200),
        granted: amount(200),
        expiresAt: 1_700_000_060_000,
        state: 'open',
        createdAt: 1_700_000_000_500,
        fence: 3,
      },
      {
        _id: `${ORG}:run-1:hold-1`,
        orgId: ORG,
        runId: 'run-1',
        rootRunId: 'run-1',
        key: 'hold-1',
        accounts: ['run-1'],
        requested: amount(50),
        granted: amount(50),
        expiresAt: 1_700_000_090_000,
        state: 'open',
        createdAt: 1_700_000_000_600,
        hold: true,
        holder: { userId: U1, agentKey: null },
      },
    ],
    agentEngineSignIns: [
      { _id: `${ORG}:u:${U1}`, orgId: ORG, userId: U1, enabled: true, email: 'a@x.test', plan: 'max', signedInAt: 1_700_000_003_000 },
      { _id: `${ORG}:org`, orgId: ORG, userId: null, claude: true },
    ],
    agentMemories: [
      {
        _id: `${ORG}:mem-1`,
        memoryId: 'mem-1',
        orgId: ORG,
        scope: 'user',
        ownerId: U1,
        agentKey: null,
        sessionId: S1,
        runId: 'run-1',
        origin: 'proposed',
        kind: 'fact',
        content: 'likes tea',
        status: 'active',
        label: null,
        createdAt: 1_700_000_004_000,
        updatedAt: 1_700_000_004_000,
        reviewedBy: null,
        reviewedAt: null,
        replaces: null,
        supersededBy: null,
      },
      {
        _id: `${ORG}:mem-2`,
        memoryId: 'mem-2',
        orgId: ORG,
        scope: 'user',
        ownerId: U2,
        agentKey: null,
        sessionId: S2,
        runId: 'run-3',
        origin: 'proposed',
        kind: 'fact',
        content: 'prefers metric',
        status: 'active',
        label: null,
        createdAt: 1_700_000_004_100,
        updatedAt: 1_700_000_004_100,
        reviewedBy: null,
        reviewedAt: null,
        replaces: null,
        supersededBy: null,
      },
    ],
    agentSchedules: [
      {
        _id: 'sch-1',
        orgId: ORG,
        actorId: U1,
        name: 'daily digest',
        input: null,
        trigger: { kind: 'cron', cron: '0 9 * * *', timeZone: 'UTC', catchUp: 'skip' },
        deadlineS: 600,
        status: 'active',
        statusReason: null,
        nextFireAt: 1_700_010_000_000,
        webhookTokenHash: null,
        createdAt: 1_700_000_005_000,
        updatedAt: 1_700_000_005_000,
      },
    ],
    agentScheduleFirings: [
      {
        _id: 'fire-1',
        orgId: ORG,
        scheduleId: 'sch-1',
        actorId: U1,
        slotKey: 'cron:1700000000000',
        slotAt: 1_700_000_000_000,
        kind: 'cron',
        status: 'fired',
        runId: 'run-1',
        sessionId: S1,
        reason: null,
        missed: 0,
        attempts: 1,
        leaseUntil: 1_700_000_030_000,
        createdAt: 1_700_000_000_000,
        settledAt: 1_700_000_000_200,
        watchUntil: null,
        notifiedAt: null,
      },
    ],
    agentScheduleCounters: [
      { _id: `org:${ORG}`, orgId: ORG, count: 1, updatedAt: 1_700_000_005_000 },
      { _id: `seen:${ORG}:sch-1:nonce-1`, orgId: ORG, count: 1, updatedAt: 1_700_000_006_000, expiresAt: new Date(1_700_000_906_000) },
    ],
    agentHarnessInboxPreferences: [
      { _id: `${ORG}:${U1}`, orgId: ORG, userId: U1, mutedSchedules: ['sch-1'], pausedUntil: null, pausedUntilResumed: true, runNoticeEmail: true, runNoticeSlack: false, updatedAt: 1_700_000_007_000 },
      // written before the notice switches and the mute list existed: the store reads them as off and empty
      { _id: `${ORG}:${U2}`, orgId: ORG, userId: U2, pausedUntil: 1_700_100_000_000, pausedUntilResumed: false, updatedAt: 1_700_000_007_100 },
    ],
    agentHarnessRunEndNotices: [{ _id: `${ORG}:run-1`, orgId: ORG, runId: 'run-1', userId: U1, createdAt: new Date(1_700_000_008_000) }],
    agentSessionErasures: [
      {
        _id: `${ORG2}:${S2}`,
        orgId: ORG2,
        sessionId: S2,
        reason: 'user_delete',
        by: U2,
        status: 'pending',
        step: 'cancel_runs',
        counts: {},
        attempt: 0,
        failures: 0,
        requestedAt: 1_700_000_010_000,
        nextAttemptAt: 1_700_000_010_000,
        completedAt: null,
        lastErrorType: null,
        holder: null,
        leaseExpiresAt: null,
      },
    ],
    agentErasures: [{ _id: `${ORG}:agent-x`, orgId: ORG, agentKey: 'agent-x', confirmed: true, failures: 0, requestedAt: 1_700_000_011_000, nextAttemptAt: 1_700_000_011_000, lastErrorType: null }],
    agentErasureBackfillPlans: [
      {
        _id: DIGEST,
        orgFilter: null,
        operator: 'ops@x.test',
        createdAt: 1_700_000_012_000,
        state: 'ready',
        orgs: [{ orgId: ORG, items: 1, batches: 1 }],
        items: 1,
        cursor: { org: 0, batch: 0, phase: 'next' },
        staleReason: null,
      },
    ],
    agentErasureBackfillItems: [
      { _id: `${DIGEST}:${S1}`, digest: DIGEST, orgId: ORG, sessionId: S1, batch: 0, fingerprint: 'fp', by: null, updatedAt: 1_700_000_012_100, counts: {}, state: 'planned' },
    ],
    agentErasureBackfillMarkers: [{ _id: ORG2, digest: DIGEST, operator: 'ops@x.test', completedAt: 1_700_000_013_000, erased: 2, withdrawn: 0, skipped: 1 }],
  };
}

type Event = Record<string, unknown>;
let token = 0;
const event = (op: 'insert' | 'update' | 'delete', coll: string, doc: Doc): Event => ({
  _id: { t: (token += 1) },
  operationType: op,
  ns: { db: 'pipeshub', coll },
  documentKey: { _id: doc._id },
  ...(op === 'delete' ? {} : { fullDocument: clone(doc) }),
  clusterTime: new Timestamp({ t: Math.floor(Date.now() / 1000), i: token }),
});

/** A migration run whose MongoDB is a scripted change stream and whose PostgreSQL is the stack. */
function scriptedRun(events: Event[]): MigrationRuntime {
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
          ? { command: async () => ({ optimes: { lastCommittedOpTime: { ts: new Timestamp({ t: 2 ** 31 - 1, i: 0 }) } } }) }
          : { collection: () => ({ findOne: async () => null }) },
    },
    db: () => ({ collection: () => ({}) }),
    jobs: <T>(fn: (tx: PgTx, k: KernelTx) => Promise<T>, opts: { write?: boolean } = {}) => pg((k) => fn(new PgTx(k), k), opts.write !== false),
  } as unknown as MigrationRuntime;
}

const job = (): JobState =>
  ({ progress: { follow: { resumeToken: { t: 0 }, appliedUntil: null, events: 0 } }, checkpoint: async () => undefined }) as unknown as JobState;
const runFollow = (events: Event[]): ReturnType<typeof follow> => follow(scriptedRun(events), job(), { untilIdleMs: 300, log: () => undefined });

const bad = (v: Awaited<ReturnType<typeof verifyCarried>>): string => JSON.stringify(v.filter((x) => !x.ok));

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: ledgers, people's data and erasure carried (R46-CARRY FU-2, ${bin})`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    let mongo: FakeCarryMongo;
    before(async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_carry_people', { importer: true, jobs: true });
      mongo = new FakeCarryMongo(seed());
    });
    after(async () => {
      await stack?.stop();
    });

    it('the three groups are built-in carriers and carry the 15 collections', () => {
      for (const c of MINE) expect(CARRIERS).to.include(c);
      expect(MINE.flatMap((c) => c.collections)).to.have.length(15);
    });

    it('copies every group and the verify finds every document in place, the absent values read as listed transforms', async () => {
      const read = await copyCarried(pg, mongo, MINE);
      expect(read).to.deep.include({
        'ledgers:agentHarnessBudgetAccounts': 2,
        'ledgers:agentHarnessBudgetReservations': 2,
        'peoples-data:agentEngineSignIns': 2,
        'peoples-data:agentHarnessInboxPreferences': 2,
        'erasure:agentPrincipalErasures': 0,
        'erasure:agentErasureBackfillPlans': 1,
        'erasure:agentErasureBackfillMarkers': 1,
      });
      const v = await verifyCarried(pg, mongo, MINE);
      expect(v.filter((x) => !x.ok), bad(v)).to.deep.equal([]);
      expect(v.find((x) => x.collection === 'agentHarnessInboxPreferences')?.transforms).to.deep.equal({
        'agentHarnessInboxPreferences: absent mutedSchedules read as []': 1,
        'agentHarnessInboxPreferences: absent runNoticeEmail read as false': 1,
        'agentHarnessInboxPreferences: absent runNoticeSlack read as false': 1,
      });
      // a second copy converges
      await copyCarried(pg, mongo, MINE);
      expect((await verifyCarried(pg, mongo, MINE)).every((x) => x.ok)).to.equal(true);
      // the typed columns the stores query are the documents' values
      expect(await sql('SELECT key, hold, state, expires_at_ms::text AS e FROM harness.budget_reservations ORDER BY key')).to.deep.equal([
        { key: 'call-1', hold: false, state: 'open', e: '1700000060000' },
        { key: 'hold-1', hold: true, state: 'open', e: '1700000090000' },
      ]);
      expect(await sql('SELECT subject, user_id, doc FROM harness.engine_signins ORDER BY subject')).to.deep.equal([
        { subject: 'org', user_id: null, doc: { claude: true } },
        { subject: `u:${U1}`, user_id: U1, doc: { enabled: true, email: 'a@x.test', plan: 'max', signedInAt: 1_700_000_003_000 } },
      ]);
      expect(await sql('SELECT key, expires_at::text AS e FROM harness.schedule_counters ORDER BY key')).to.deep.equal([
        { key: `org:${ORG}`, e: null },
        { key: `seen:${ORG}:sch-1:nonce-1`, e: '1700000906000' },
      ]);
      expect(await sql("SELECT id, doc->>'_id' AS doc_id FROM harness.schedules")).to.deep.equal([{ id: 'sch-1', doc_id: 'sch-1' }]);
      expect(await sql('SELECT org_id, doc FROM harness.erasure_backfill_markers')).to.deep.equal([
        { org_id: ORG2, doc: { orgId: ORG2, digest: DIGEST, operator: 'ops@x.test', completedAt: 1_700_000_013_000, erased: 2, withdrawn: 0, skipped: 1 } },
      ]);
      // the key map: tables that do not keep the id (accounts 2, reservations 2, sign-ins 2, memories 2, Inbox 2, notices 1,
      // session and agent erasures 2, backfill items 1, markers 1)
      expect((await sql<{ n: string }>('SELECT count(*)::text AS n FROM platform.docstore_keymap'))[0]?.n).to.equal('15');
    });

    it("the harness's PostgreSQL stores read what was carried as the MongoDB stores would", async () => {
      await inOrg(ORG, async () => {
        const ledger = new PgBudgetLedger();
        expect(await ledger.account(ORG, 'run-1')).to.deep.include({ rootRunId: 'run-1', closed: false, committed: amount(1_000), reserved: amount(200) });
        expect((await ledger.account(ORG, 'run-2'))?.ancestors).to.deep.equal(['run-1']);
        expect(await new PgMemoryStore().get(ORG, 'mem-1')).to.deep.include({ content: 'likes tea', ownerId: U1, status: 'active' });
        const schedules = new PgScheduleStore();
        expect(await schedules.get(ORG, 'sch-1')).to.deep.include({ id: 'sch-1', name: 'daily digest', nextFireAt: 1_700_010_000_000 });
        expect((await schedules.firings(ORG, 'sch-1', 10)).map((f) => [f.id, f.status])).to.deep.equal([['fire-1', 'fired']]);
        // the replay guard goes on from the copied count: the same nonce is not a first delivery
        expect(await schedules.firstDelivery(ORG, 'sch-1', 'nonce-1', 1_700_000_906_500, 1_700_001_000_000)).to.equal(false);
        const inbox = new PgInboxStore();
        expect(await inbox.preferences(ORG, U1)).to.deep.equal({ mutedSchedules: ['sch-1'], pause: { untilResumed: true }, runNotices: { email: true, slack: false } });
        expect(await inbox.preferences(ORG, U2)).to.deep.equal({ mutedSchedules: [], pause: { until: 1_700_100_000_000 }, runNotices: { email: false, slack: false } });
        // the once-only claim holds across the move
        expect(await inbox.claimRunNotice(ORG, 'run-1', U1, 1_700_000_009_500)).to.equal(false);
        const signIns = new PgEngineSignIns();
        expect(await signIns.orgAllows(ORG)).to.equal(true);
        expect(await signIns.signedInSince(ORG, 1_700_000_000_000)).to.deep.equal([U1]);
        expect(await new PgErasureJobStore().find(ORG, S1)).to.equal(null);
      });
      await inOrg(ORG2, async () => {
        expect(await new PgErasureJobStore().find(ORG2, S2)).to.deep.include({ status: 'pending', reason: 'user_delete' });
      });
      // the erasure backfill, read through its store as the jobs login (its scans are deployment-wide)
      const backfill = new PgErasureBackfillStore();
      await system('carry-people-itest', async () => {
        expect(await backfill.plan(DIGEST)).to.deep.include({ state: 'ready', orgFilter: null, digest: DIGEST, items: 1 });
        expect((await backfill.items(DIGEST, null, 10)).map((i) => [i.sessionId, i.batch])).to.deep.equal([[S1, 0]]);
        expect((await backfill.markers()).map((m) => m.orgId)).to.deep.equal([ORG2]);
      });
      // the store wrote PostgreSQL's own state since (the replayed nonce's count): MongoDB is told, so the next compare is even
      mongo.put('agentScheduleCounters', { ...seed().agentScheduleCounters![1]!, count: 2, updatedAt: 1_700_000_906_500 });
      const v = await verifyCarried(pg, mongo, MINE);
      expect(v.filter((x) => !x.ok), bad(v)).to.deep.equal([]);
    });

    it('the follower applies writes and deletes of every group through the key map', async () => {
      const [m1] = seed().agentMemories!;
      const edited = { ...m1!, content: 'likes green tea', updatedAt: 1_700_000_020_000 };
      const firing2 = { ...seed().agentScheduleFirings![0]!, _id: 'fire-2', slotKey: 'cron:1700086400000', slotAt: 1_700_086_400_000, status: 'leased', runId: null, sessionId: null, settledAt: null };
      const [, reservation] = seed().agentHarnessBudgetReservations!;
      const r = await runFollow([
        event('update', 'agentMemories', edited),
        event('insert', 'agentScheduleFirings', firing2),
        event('delete', 'agentHarnessBudgetReservations', { _id: reservation!._id }),
        event('delete', 'agentErasureBackfillPlans', { _id: DIGEST }),
      ]);
      expect(r.applied).to.equal(4);
      mongo.put('agentMemories', edited);
      mongo.put('agentScheduleFirings', firing2);
      mongo.del('agentHarnessBudgetReservations', reservation!._id);
      mongo.del('agentErasureBackfillPlans', DIGEST);
      const v = await verifyCarried(pg, mongo, MINE);
      expect(v.filter((x) => !x.ok), bad(v)).to.deep.equal([]);
      expect(await sql('SELECT key FROM harness.budget_reservations')).to.deep.equal([{ key: 'call-1' }]);
      expect(await sql('SELECT 1 FROM platform.docstore_keymap WHERE source_id = $1', [reservation!._id])).to.deep.equal([]);
      expect(await system('carry-people-itest', () => new PgErasureBackfillStore().plan(DIGEST))).to.equal(null);
      expect(await sql("SELECT doc->>'content' AS c FROM harness.memories WHERE memory_id = 'mem-1'")).to.deep.equal([{ c: 'likes green tea' }]);
    });

    it('an erasure asked for during the follow is applied on PostgreSQL: the jobs are there and the erased data is gone', async () => {
      // U1 is deleted while the follow runs: the person's erasure job and a session erasure are written, and the eraser on MongoDB
      // removes their memory, sign-in, Inbox settings, notice claim and the session's firings
      const userJob = {
        _id: `${ORG}:u:${U1}`,
        kind: 'user',
        orgId: ORG,
        userId: U1,
        by: U2,
        step: 'transfer',
        requestedAt: 1_700_000_030_000,
        eraseAfter: null,
        auditRetentionDays: 365,
        auditRemoveAfter: null,
        cursor: null,
        transfers: [],
        journaled: 0,
        counts: {},
        remindersSent: [],
        reminders: [],
        failures: 0,
        nextAttemptAt: 1_700_000_030_000,
        lastErrorType: null,
      };
      const sessionJob = { ...seed().agentSessionErasures![0]!, _id: `${ORG}:${S1}`, orgId: ORG, sessionId: S1, by: U2, requestedAt: 1_700_000_030_000, nextAttemptAt: 1_700_000_030_000 };
      const gone: Array<[string, string]> = [
        ['agentMemories', `${ORG}:mem-1`],
        ['agentEngineSignIns', `${ORG}:u:${U1}`],
        ['agentHarnessInboxPreferences', `${ORG}:${U1}`],
        ['agentHarnessRunEndNotices', `${ORG}:run-1`],
        ['agentScheduleFirings', 'fire-1'],
        ['agentScheduleFirings', 'fire-2'],
      ];
      const r = await runFollow([
        event('insert', 'agentPrincipalErasures', userJob),
        event('insert', 'agentSessionErasures', sessionJob),
        ...gone.map(([c, id]) => event('delete', c, { _id: id })),
      ]);
      expect(r.applied).to.equal(8);
      mongo.put('agentPrincipalErasures', userJob);
      mongo.put('agentSessionErasures', sessionJob);
      for (const [c, id] of gone) mongo.del(c, id);
      const v = await verifyCarried(pg, mongo, MINE);
      expect(v.filter((x) => !x.ok), bad(v)).to.deep.equal([]);
      await inOrg(ORG, async () => {
        expect(await new PgMemoryStore().get(ORG, 'mem-1')).to.equal(null);
        expect(await new PgEngineSignIns().signedInSince(ORG, 0)).to.deep.equal([]);
        expect(await new PgInboxStore().preferences(ORG, U1)).to.deep.equal({ mutedSchedules: [], pause: null, runNotices: { email: false, slack: false } });
        expect(await new PgScheduleStore().firings(ORG, 'sch-1', 10)).to.deep.equal([]);
        expect(await new PgErasureJobStore().find(ORG, S1)).to.deep.include({ status: 'pending', by: U2 });
        expect(await new PgPrincipalErasureJobs().findOrg(ORG)).to.equal(null);
      });
      expect(await sql('SELECT org_id, id, kind, step FROM harness.principal_erasures')).to.deep.equal([{ org_id: ORG, id: `${ORG}:u:${U1}`, kind: 'user', step: 'transfer' }]);
      for (const t of ['harness.memories', 'harness.engine_signins', 'harness.inbox_preferences', 'harness.run_end_notices'])
        expect(await sql(`SELECT 1 FROM ${t} x WHERE org_id = $1 AND to_jsonb(x)::text LIKE $2`, [ORG, `%${U1}%`]), t).to.deep.equal([]);
    });

    it('data erased on PostgreSQL after the move does not come back with a rollback, and rows made there get their MongoDB ids', async () => {
      await inOrg(ORG, async () => {
        // the application on PostgreSQL: U2's memory and Inbox settings erased, the schedule deleted, a memory proposed, the switch off
        expect(await new PgMemoryStore().eraseUser(ORG, U2)).to.equal(1);
        await new PgInboxStore().eraseUser(ORG, U2);
        expect(await new PgScheduleStore().delete(ORG, 'sch-1')).to.equal(true);
        const m2: Record<string, unknown> = { ...seed().agentMemories![1]! };
        delete m2['_id'];
        await new PgMemoryStore().propose({ ...m2, memoryId: 'mem-9', content: 'new on pg', createdAt: 1_800_000_000_000, updatedAt: 1_800_000_000_000 } as never);
        await new PgEngineSignIns().setOrgAllows(ORG, false);
      });
      // a marker as PgErasureBackfillStore.putMarker writes it
      await pg(
        (k) =>
          k.query("INSERT INTO harness.erasure_backfill_markers (org_id, doc, region) VALUES ($1, $2::jsonb, 'local')", [
            ORG,
            JSON.stringify({ orgId: ORG, digest: DIGEST, operator: 'ops@x.test', completedAt: 1_800_000_000_100, erased: 1, withdrawn: 0, skipped: 0 }),
          ]),
        true,
      );
      const out = await restoreCarried(pg, mongo, MINE);
      expect(out['peoples-data:agentMemories']).to.deep.include({ written: 1, removed: 1 });
      expect(mongo.get('agentMemories', `${ORG}:mem-2`)).to.equal(undefined);
      expect(mongo.get('agentHarnessInboxPreferences', `${ORG}:${U2}`)).to.equal(undefined);
      expect(mongo.get('agentSchedules', 'sch-1')).to.equal(undefined);
      // what the follow erased stays erased
      expect(mongo.get('agentMemories', `${ORG}:mem-1`)).to.equal(undefined);
      // the memory made on PostgreSQL comes back under the id the MongoDB store gives it, the org's switch as the store writes it
      expect(mongo.get('agentMemories', `${ORG}:mem-9`)).to.deep.include({ memoryId: 'mem-9', content: 'new on pg', orgId: ORG });
      expect(mongo.get('agentEngineSignIns', `${ORG}:org`)).to.deep.equal({ _id: `${ORG}:org`, orgId: ORG, userId: null, claude: false });
      // a marker made on PostgreSQL comes back under its org's id, the copied one keeps its own
      expect(mongo.get('agentErasureBackfillMarkers', ORG)).to.deep.include({ digest: DIGEST, erased: 1 });
      expect(mongo.get('agentErasureBackfillMarkers', ORG)).to.not.have.property('orgId');
      expect(mongo.get('agentErasureBackfillMarkers', ORG2)).to.deep.include({ erased: 2 });
      // the documents as the MongoDB stores keep them: the counters' TTL date, a reservation without a hold flag
      expect(mongo.get('agentScheduleCounters', `seen:${ORG}:sch-1:nonce-1`)?.expiresAt).to.deep.equal(new Date(1_700_000_906_000));
      expect(mongo.get('agentHarnessBudgetReservations', `${ORG}:run-1:call-1`)).to.not.have.property('hold');
      expect(mongo.get('agentHarnessRunEndNotices', `${ORG}:run-1`)).to.equal(undefined);
      // and the verify, the other way round, finds MongoDB equal to PostgreSQL
      const v = await verifyCarried(pg, mongo, MINE);
      expect(v.filter((x) => !x.ok), bad(v)).to.deep.equal([]);
    });

    it('a deployment-wide plan written on PostgreSQL rolls back under its digest', async () => {
      // a plan as PgErasureBackfillStore.createPlan writes it (no org of its own)
      await pg(
        (k) =>
          k.query("INSERT INTO harness.erasure_backfill_plans (digest, doc, region) VALUES ($1, $2::jsonb, 'local')", [
            DIGEST,
            JSON.stringify({ digest: DIGEST, orgFilter: ORG, operator: 'ops@x.test', createdAt: 1_800_000_001_000, state: 'persisting', orgs: [], items: 0, cursor: { org: 0, batch: 0, phase: 'next' }, staleReason: null }),
          ]),
        true,
      );
      await restoreCarried(pg, mongo, [ERASURE_JOBS]);
      expect(mongo.get('agentErasureBackfillPlans', DIGEST)).to.deep.include({ orgFilter: ORG, state: 'persisting' });
      expect(mongo.get('agentErasureBackfillPlans', DIGEST)).to.not.have.property('digest');
      expect((await verifyCarried(pg, mongo, [ERASURE_JOBS])).every((x) => x.ok)).to.equal(true);
    });

    it('reset removes every row the three groups wrote and their key map', async () => {
      await pg((k) => clearCarried(k, MINE, false), true);
      for (const c of MINE) for (const t of c.tables) expect((await sql<{ n: string }>(`SELECT count(*)::text AS n FROM ${t}`))[0]?.n, t).to.equal('0');
      expect(await sql('SELECT 1 FROM platform.docstore_keymap WHERE collection = ANY($1::text[])', [MINE.flatMap((c) => c.collections)])).to.deep.equal([]);
    });
  });
}
