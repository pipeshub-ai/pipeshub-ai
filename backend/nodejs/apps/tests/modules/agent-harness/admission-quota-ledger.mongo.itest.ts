/**
 * PERF-C (PERFB-F1) on a MongoDB replica set (run as
 * execution-store.mongo.itest.ts says): the org's admission window reserved
 * outside the admission's transaction. PERFC-3 holds the MongoDB updates to
 * the domain's rules; PERFC-4 and PERFC-7..11 are the store using them.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import fc from 'fast-check';
import { ClientSession, MongoError } from 'mongodb';
import { Types } from 'mongoose';
import {
  ADMISSION_HOLD_MS,
  ADMISSION_QUOTAS,
  AdmissionQuotas,
  holdsNothing,
  orgQuotaKey,
  userQuotaKey,
  QuotaLedgerState,
  QuotaReservationRequest,
  releaseQuota,
  reservationOf,
  reserveQuota,
} from '../../../src/modules/agent-harness/domain/admission-quotas';
import { HarnessQuotaExceededError } from '../../../src/modules/agent-harness/domain/errors';
import { MongoAdmissionQuotaLedger } from '../../../src/libs/repositories/agent-harness/admission-quota-ledger/mongo';
import { AgentAdmissionQuotaModel, AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { propertyRuns } from '../../support/harness/property-profile';
import { contractClock, ContractHarness, createContractHarness, START } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { asParty, probeStore } from './support/store-race';

const QUOTAS_COLLECTION = AgentAdmissionQuotaModel.collection.collectionName;
const WINDOW_MS = 60_000;

const stored = async (key: string): Promise<QuotaLedgerState | null> => {
  const document = await AgentAdmissionQuotaModel.findOne({ _id: key }).lean();
  return document === null
    ? null
    : { windowStart: document.windowStart, count: document.count, holds: document.holds ?? [] };
};

type Line = [level: string, message: string, meta: Record<string, unknown>];

const recordingLogger = (): { logger: HarnessLogger; lines: Line[] } => {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } };
};

interface LedgerOp {
  kind: 'reserve' | 'release';
  id: string;
  /** Added to the clock before the operation. */
  later: number;
  refund: boolean;
  /** The admission's receipt key on the reservation: two ids under one key are twins (QA21-1). */
  admission: string | undefined;
}

const ledgerOps = fc.array(
  fc.record<LedgerOp>({
    kind: fc.constantFrom<LedgerOp['kind']>('reserve', 'reserve', 'release'),
    id: fc.constantFrom('a', 'b', 'c', 'd'),
    later: fc.constantFrom(0, 0, 0, 1, WINDOW_MS / 2, WINDOW_MS, ADMISSION_HOLD_MS),
    refund: fc.boolean(),
    admission: fc.constantFrom(undefined, undefined, 'turn-x', 'turn-y'),
  }),
  { minLength: 1, maxLength: 14 },
);

describe('PERF-C: the org admission window outside the admission transaction (MongoDB replica set)', function () {
  this.timeout(300_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describe('PERFC-3: the MongoDB ledger is the domain ledger', () => {
    const ledger = new MongoAdmissionQuotaLedger();

    it('any sequence of reservations and releases leaves the document the rules say, and answers as they do', async () => {
      let round = 0;
      await fc.assert(
        fc.asyncProperty(ledgerOps, fc.integer({ min: 1, max: 3 }), async (ops, limit) => {
          round += 1;
          const key = `ledger-${String(round)}`;
          let model: QuotaLedgerState | null = null;
          let now = START;
          for (const op of ops) {
            now += op.later;
            if (op.kind === 'reserve') {
              const request: QuotaReservationRequest = {
                id: op.id,
                runId: `run-${op.id}`,
                now,
                limit,
                windowMs: WINDOW_MS,
                holdMs: ADMISSION_HOLD_MS,
                ...(op.admission !== undefined ? { admission: op.admission } : {}),
              };
              model = reserveQuota(model, request);
              expect(await ledger.reserve(key, request)).to.deep.equal(reservationOf(model, request));
            } else {
              if (model !== null) model = releaseQuota(model, op.id, op.refund);
              if (model !== null && holdsNothing(model)) model = null;
              await ledger.release(key, op.id, op.refund);
            }
            expect(await stored(key)).to.deep.equal(model);
          }
        }),
        propertyRuns(60),
      );
    });
  });

  describe('the store', () => {
    let h: ContractHarness;
    let lines: Line[];
    let returned: string[];

    const harness = (quotas: Partial<AdmissionQuotas> = {}) => {
      const clock = contractClock();
      const recorded = recordingLogger();
      lines = recorded.lines;
      returned = [];
      h = createContractHarness(
        mongoBackend({
          clock,
          quotas: { ...ADMISSION_QUOTAS, ...quotas },
          logger: recorded.logger,
          quotaMetrics: { returned: (why) => returned.push(why) },
        }),
        clock,
      );
    };
    const actor = () => ({ orgId: h.tenant.orgId, actorId: new Types.ObjectId().toString() });
    const orgWindow = () => stored(orgQuotaKey(h.tenant.orgId));
    const settledAll = <T>(work: Array<Promise<T>>) => Promise.allSettled(work);
    const refusals = (outcomes: Array<PromiseSettledResult<unknown>>) =>
      outcomes
        .filter((outcome): outcome is PromiseRejectedResult => outcome.status === 'rejected')
        .map((outcome) => outcome.reason as HarnessQuotaExceededError);

    it("PERFC-4: 24 actors at once at the org's active limit: exactly the limit is admitted, every other one refused for it", async () => {
      harness({ activeRunsPerOrg: 5 });
      const outcomes = await settledAll(Array.from({ length: 24 }, () => h.admit(actor())));
      const refused = refusals(outcomes);
      expect(outcomes.length - refused.length).to.equal(5);
      expect(refused.every((error) => error instanceof HarnessQuotaExceededError)).to.equal(true);
      expect(new Set(refused.map((error) => `${error.scope}:${error.quota}`))).to.deep.equal(new Set(['org:active_runs']));
      // Refused for what is ahead or for what is there: the same error and the same wait either way.
      expect(new Set(refused.map((error) => error.retryAfterS))).to.deep.equal(new Set([ADMISSION_QUOTAS.activeRetryAfterS]));
      expect(returned).to.deep.equal(Array.from({ length: 19 }, () => 'not_admitted'));
      expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(5);
      expect(await orgWindow()).to.deep.include({ count: 5, holds: [] });
    });

    it("PERFC-4: 24 actors at once at the org's rate limit: exactly the window is admitted, and it counts exactly them", async () => {
      harness({ admissionsPerOrg: 7 });
      const outcomes = await settledAll(Array.from({ length: 24 }, () => h.admit(actor())));
      const refused = refusals(outcomes);
      expect(outcomes.length - refused.length).to.equal(7);
      expect(new Set(refused.map((error) => `${error.scope}:${error.quota}`))).to.deep.equal(new Set(['org:admission_rate']));
      expect(refused.every((error) => error.retryAfterS === WINDOW_MS / 1000)).to.equal(true);
      expect(await orgWindow()).to.deep.include({ windowStart: START, count: 7, holds: [] });
    });

    it('PERFC-12: an admission that read before another one committed still counts it: the last place is taken once', async () => {
      harness({ activeRunsPerOrg: 1 });
      const outcome = (work: Promise<unknown>) =>
        work.then(
          () => 'admitted',
          (error: HarnessQuotaExceededError) => `${error.scope}:${error.quota}`,
        );
      let reached!: () => void;
      let release!: () => void;
      const atSnapshot = new Promise<void>((resolve) => (reached = resolve));
      const held = new Promise<void>((resolve) => (release = resolve));
      let gated = false;
      // The first read inside the transaction fixes its snapshot: `late` is held right after it.
      const remove = probeStore(async (op, phase) => {
        if (gated || op.party !== 'late' || !op.inTransaction || phase !== 'after') return;
        gated = true;
        reached();
        await held;
      });
      try {
        const late = outcome(asParty('late', () => h.admit(actor())));
        await atSnapshot;
        const early = await outcome(h.admit(actor()));
        release();
        expect([early, await late]).to.deep.equal(['org:active_runs', 'admitted']);
      } finally {
        release();
        remove();
      }
      expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(1);
      expect(await orgWindow()).to.deep.include({ count: 1, holds: [] });
    });

    it("DOS-MONGO-1: one person's burst of 300 costs each admission one transaction attempt; a colleague in the same instant starts", async () => {
      // Before, the actor's window was written in every admission's transaction: 500 at once made 22,424 attempts.
      harness({ activeRunsPerUser: 2, admissionsPerUser: 4, activeRunsPerOrg: 3, admissionsPerOrg: 6 });
      const prototype = ClientSession.prototype;
      const start = prototype.startTransaction;
      let attempts = 0;
      prototype.startTransaction = function (this: ClientSession, ...args: Parameters<typeof start>) {
        attempts += 1;
        return start.apply(this, args);
      };
      let outcomes: Array<PromiseSettledResult<unknown>>;
      let colleague: string;
      try {
        const burst = settledAll(Array.from({ length: 300 }, () => h.admit()));
        colleague = await h.admit(actor()).then(
          () => 'admitted',
          (error: HarnessQuotaExceededError) => `${error.scope}:${error.quota}`,
        );
        outcomes = await burst;
      } finally {
        prototype.startTransaction = start;
      }
      expect(colleague).to.equal('admitted');
      expect(outcomes.filter((outcome) => outcome.status === 'fulfilled')).to.have.length(2);
      expect(refusals(outcomes).every((error) => error.scope === 'user')).to.equal(true);
      expect(attempts, 'a transaction attempt per admission and the colleague\'s').to.equal(301);
      expect(await orgWindow(), "the org's window counts the admitted ones only").to.deep.include({ count: 3, holds: [] });
    });

    it('PERFC-5: a refusal and a replay leave the window as it was; the window ends on the clock', async () => {
      harness({ activeRunsPerUser: 1, admissionsPerOrg: 3 });
      const first = await h.admit({ idempotencyKey: 'once' });
      expect(await orgWindow()).to.deep.include({ count: 1, holds: [] });
      await h.admit({ idempotencyKey: 'once' });
      expect((await orgWindow())?.count, 'a replay takes no place').to.equal(1);
      const refused = await h.admit().then(
        () => null,
        (error: unknown) => error,
      );
      expect(refused).to.be.instanceOf(HarnessQuotaExceededError);
      expect((await orgWindow())?.count, 'a refusal takes no place').to.equal(1);
      expect(first.created).to.equal(true);
      expect(returned, 'PERFC-14: each place given back is counted, by why').to.deep.equal(['replayed', 'not_admitted']);
      h.setNow(START + WINDOW_MS);
      await h.admit(actor());
      expect(await orgWindow()).to.deep.include({ windowStart: START + WINDOW_MS, count: 1, holds: [] });
    });

    it("PERFE-9: the org's and the actor's active runs are counted in one read: another actor's runs count for the org only", async () => {
      harness({ activeRunsPerUser: 1, activeRunsPerOrg: 3 });
      const mine = actor();
      await h.admit(mine);
      await h.admit(actor());
      const quotaOf = (work: Promise<unknown>) =>
        work.then(
          () => 'admitted',
          (error: HarnessQuotaExceededError) => `${error.scope}:${error.quota}`,
        );
      expect(await quotaOf(h.admit(mine))).to.equal('user:active_runs');
      expect(await quotaOf(h.admit(actor()))).to.equal('admitted');
      expect(await quotaOf(h.admit(actor()))).to.equal('org:active_runs');
    });

    it('PERFC-13: a place held by a Node that died goes with its window (60 s), with nothing to do', async () => {
      harness({ admissionsPerOrg: 1 });
      const key = orgQuotaKey(h.tenant.orgId);
      // The Node reserved and died: nothing ever settles this hold.
      await new MongoAdmissionQuotaLedger().reserve(key, {
        id: 'died',
        runId: 'run-never-admitted',
        now: START,
        limit: 1,
        windowMs: WINDOW_MS,
        holdMs: ADMISSION_HOLD_MS,
      });
      const refusedAt = async (now: number) => {
        h.setNow(now);
        return h.admit(actor()).then(
          () => 'admitted',
          (error: HarnessQuotaExceededError) => `${error.scope}:${error.quota}:${String(error.retryAfterS)}`,
        );
      };
      expect(await refusedAt(START + 1_000)).to.equal('org:admission_rate:59');
      expect(await refusedAt(START + WINDOW_MS - 1)).to.equal('org:admission_rate:1');
      expect(await refusedAt(START + WINDOW_MS)).to.equal('admitted');
      expect(await orgWindow()).to.deep.include({ windowStart: START + WINDOW_MS, count: 1 });
      expect((await orgWindow())?.holds.map((hold) => hold.id), 'the dead hold itself lapses later').to.deep.equal(['died']);
      h.setNow(START + ADMISSION_HOLD_MS);
      await h.admit(actor()).catch(() => undefined);
      expect((await orgWindow())?.holds).to.deep.equal([]);
    });

    it('PERFC-7: a reservation whose answer was lost is given back when the admission fails', async () => {
      harness();
      await h.admit();
      const before = await orgWindow();
      let fired = false;
      const remove = probeStore((op, phase) => {
        if (fired || phase !== 'after' || op.inTransaction) return;
        if (op.collection !== QUOTAS_COLLECTION || op.method !== 'findOneAndUpdate') return;
        fired = true;
        throw new Error('the reservation answer was lost');
      });
      try {
        const failed = await h.admit(actor()).then(
          () => null,
          (error: Error) => error.message,
        );
        expect(failed).to.equal('the reservation answer was lost');
      } finally {
        remove();
      }
      expect(await orgWindow()).to.deep.equal(before);
    });

    it('PERFC-8: a commit whose answer was lost keeps its hold and its place', async () => {
      harness();
      const prototype = ClientSession.prototype;
      const commit = prototype.commitTransaction;
      let lost = false;
      prototype.commitTransaction = async function (this: ClientSession) {
        await commit.call(this);
        if (!lost) {
          lost = true;
          throw new MongoError('the commit answer was lost');
        }
      };
      let failed: string | null;
      try {
        failed = await h.admit({}, 'run-in-doubt').then(
          () => null,
          (error: Error) => error.message,
        );
      } finally {
        prototype.commitTransaction = commit;
      }
      expect(failed).to.equal('the commit answer was lost');
      const window = await orgWindow();
      expect(window?.count, 'the place is not given back').to.equal(1);
      expect(window?.holds.map((hold) => hold.runId)).to.deep.equal(['run-in-doubt']);
      // The run did commit: whoever reserves next reads it and counts it once.
      await h.admit(actor());
      expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(2);
      expect((await orgWindow())?.count).to.equal(2);
    });

    it('PERFC-9: a release that fails after the commit is logged; the admission stands', async () => {
      harness();
      // Every quota write after the admission's commit is a release.
      const prototype = ClientSession.prototype;
      const commit = prototype.commitTransaction;
      let committed = false;
      prototype.commitTransaction = async function (this: ClientSession) {
        await commit.call(this);
        committed = true;
      };
      const remove = probeStore((op, phase) => {
        if (phase !== 'before' || op.inTransaction || !committed) return;
        if (op.collection === QUOTAS_COLLECTION) throw new Error('store away');
      });
      let admitted;
      try {
        admitted = await h.admit({}, 'run-kept');
      } finally {
        remove();
        prototype.commitTransaction = commit;
      }
      expect(admitted.created).to.equal(true);
      expect(lines.filter(([, message]) => message === 'admission.quota_release_failed')).to.deep.equal([
        [
          'warn',
          'admission.quota_release_failed',
          {
            event: 'admission.quota_release_failed',
            orgId: h.tenant.orgId,
            runId: 'run-kept',
            errorType: 'Error',
            choice: 'hold_lapses',
          },
        ],
      ]);
      expect((await orgWindow())?.holds).to.have.length(1);
      // The hold lapses on the clock: until then it is ahead and already in the snapshot, afterwards it is gone.
      h.setNow(START + ADMISSION_HOLD_MS);
      await h.admit(actor());
      expect((await orgWindow())?.holds).to.deep.equal([]);
    });

    describe('PERFC-10: beside a Node of the previous release', () => {
      /** The org's (or, given its key, the actor's) window as the previous release advanced it: inside its admission's transaction. */
      const previousRelease = (orgId: string, now: number, key = orgQuotaKey(orgId)) =>
        inMongoTransaction(async (session) => {
          const lapsed = {
            $or: [{ $eq: [{ $type: '$windowStart' }, 'missing'] }, { $lte: ['$windowStart', now - WINDOW_MS] }],
          };
          const window = await AgentAdmissionQuotaModel.findOneAndUpdate(
            { _id: key },
            [
              {
                $set: {
                  windowStart: { $cond: [lapsed, now, '$windowStart'] },
                  count: { $cond: [lapsed, 1, { $add: ['$count', 1] }] },
                },
              },
            ],
            { updatePipeline: true, upsert: true, new: true, session },
          ).lean();
          return window.count;
        });

      it('both count in one window: a window the old release filled refuses here, and the other way round', async () => {
        harness({ admissionsPerOrg: 3 });
        expect(await previousRelease(h.tenant.orgId, START)).to.equal(1);
        await h.admit(actor());
        expect(await orgWindow(), 'a document without holds is read as one with none').to.deep.include({ count: 2, holds: [] });
        expect(await previousRelease(h.tenant.orgId, START)).to.equal(3);
        const refused = await h.admit(actor()).then(
          () => null,
          (error: HarnessQuotaExceededError) => `${error.scope}:${error.quota}`,
        );
        expect(refused).to.equal('org:admission_rate');
        expect(await previousRelease(h.tenant.orgId, START), 'past the limit: the old release refuses on this count').to.equal(4);
      });

      it("DOS-MONGO-1: the actor's window too: the old release's advance in its transaction and this release's reservation count in one window", async () => {
        harness({ admissionsPerUser: 3 });
        const mine = actor();
        const key = userQuotaKey(mine.orgId, mine.actorId);
        expect(await previousRelease(mine.orgId, START, key)).to.equal(1);
        await h.admit(mine);
        expect(await stored(key), 'a document without holds is read as one with none').to.deep.include({ count: 2, holds: [] });
        expect(await previousRelease(mine.orgId, START, key)).to.equal(3);
        const refused = await h.admit(mine).then(
          () => null,
          (error: HarnessQuotaExceededError) => `${error.scope}:${error.quota}`,
        );
        expect(refused).to.equal('user:admission_rate');
        expect((await orgWindow())?.count, 'refused on the actor\'s window, the org was not asked').to.equal(1);
      });

      it("the old release's write keeps the holds this release wrote", async () => {
        harness();
        const ledger = new MongoAdmissionQuotaLedger();
        const key = orgQuotaKey(h.tenant.orgId);
        await ledger.reserve(key, { id: 'held', runId: 'run-held', now: START, limit: 10, windowMs: WINDOW_MS, holdMs: ADMISSION_HOLD_MS });
        expect(await previousRelease(h.tenant.orgId, START)).to.equal(2);
        expect((await orgWindow())?.holds.map((hold) => hold.id)).to.deep.equal(['held']);
        await ledger.release(key, 'held', true);
        expect(await orgWindow()).to.deep.include({ count: 1, holds: [] });
      });
    });

    it("PERFC-11: another org admits while this org's window is full and held", async () => {
      harness({ admissionsPerOrg: 1 });
      await h.admit();
      await new MongoAdmissionQuotaLedger().reserve(orgQuotaKey(h.tenant.orgId), {
        id: 'stuck',
        runId: 'run-stuck',
        now: START,
        limit: 5,
        windowMs: WINDOW_MS,
        holdMs: ADMISSION_HOLD_MS,
      });
      const other = h.newTenant();
      expect((await h.admit(other)).created).to.equal(true);
      expect(await stored(orgQuotaKey(other.orgId))).to.deep.include({ count: 1, holds: [] });
      expect((await orgWindow())?.holds.map((hold) => hold.id)).to.deep.equal(['stuck']);
    });
  });
});
