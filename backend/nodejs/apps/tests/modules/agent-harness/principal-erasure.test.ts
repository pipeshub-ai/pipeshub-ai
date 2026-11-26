/**
 * CLEANUP-G1, G2: the principal erasure runner's own rules, over fakes: a
 * user job runs its built-in steps; an org job runs its org steps (built in,
 * or an extension's through `HarnessEdition.orgErasure`) and, with none
 * bound, waits unchanged; a
 * step that fails waits with backoff and is retried at that step; a job
 * removed while a pass was in flight stops the pass.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import {
  ORG_JOB_WITHOUT_STEPS_WAIT_MS,
  PrincipalErasureRunner,
  type OrgErasureSteps,
} from '../../../src/modules/agent-harness/application/principal-erasure';
import { ImmediateOrgErasureSteps } from '../../../src/modules/agent-harness/application/org-erasure-steps';
import { MongoOrgData } from '../../../src/libs/repositories/agent-harness/principal-erasure/mongo';
import { dataKeyRing } from './support/data-protection';
import { PrincipalErasureJob, PrincipalErasureStep } from '../../../src/modules/agent-harness/domain/principal-erasure';
import { erasureBackoffMs } from '../../../src/modules/agent-harness/domain/session-erasure';
import { DAY_MS } from '../../../src/modules/agent-harness/domain/session-retention';

const NOW = 1_900_000_000_000;

const orgJob = (overrides: Partial<PrincipalErasureJob> = {}): PrincipalErasureJob => ({
  kind: 'org',
  orgId: 'org-1',
  userId: null,
  by: 'p_admin',
  step: 'cancel_runs',
  requestedAt: NOW,
  auditRetentionDays: 365,
  auditRemoveAfter: null,
  cursor: null,
  transfers: [],
  journaled: 0,
  counts: {},
  extension: null,
  failures: 0,
  nextAttemptAt: NOW,
  lastErrorType: null,
  ...overrides,
});

/** A job store over one stored job, and an org steps fake that answers what `answers` gives per step. */
function harness(
  job: PrincipalErasureJob,
  orgSteps: OrgErasureSteps | null,
  options: { removedAt?: PrincipalErasureStep; warn?: sinon.SinonStub } = {},
) {
  let stored: PrincipalErasureJob | null = { ...job };
  const jobs = {
    now: () => Promise.resolve(NOW),
    due: () => Promise.resolve(stored === null ? [] : [stored]),
    writeProgress: sinon.stub().callsFake(async (next: PrincipalErasureJob, expected: PrincipalErasureStep) => {
      if (stored === null || stored.step !== expected) return false;
      if (options.removedAt === next.step) {
        stored = null;
        return false;
      }
      stored = { ...next };
      return true;
    }),
    remove: sinon.stub().callsFake(async () => {
      stored = null;
    }),
  };
  const runner = new PrincipalErasureRunner({
    jobs: jobs as never,
    orgSteps,
    recorder: null,
    unsent: null,
    transaction: (work) => work(undefined),
    ...(options.warn !== undefined
      ? { logger: { info: sinon.stub(), warn: options.warn, error: sinon.stub(), debug: sinon.stub() } as never }
      : {}),
  });
  return { runner, stored: () => stored };
}

describe('CLEANUP-G1: an org job runs its org steps', () => {
  it('with none bound, an org job is left at its step and looked at again a day later, logged', async () => {
    const warn = sinon.stub();
    const h = harness(orgJob(), null, { warn });
    expect(await h.runner.tick(NOW)).to.equal(0);
    expect(h.stored()).to.deep.include({ step: 'cancel_runs', nextAttemptAt: NOW + ORG_JOB_WITHOUT_STEPS_WAIT_MS, failures: 0 });
    expect(ORG_JOB_WITHOUT_STEPS_WAIT_MS).to.equal(DAY_MS);
    expect(warn.firstCall.args[0]).to.equal('principal_erasure.no_org_steps');
    expect(warn.firstCall.args[1]).to.deep.include({ orgId: 'org-1', step: 'cancel_runs' });
  });

  it('the steps run in the stored order, their counts add up, a wait stops the pass, and the last step removes the job', async () => {
    const seen: string[] = [];
    const until = NOW + 30 * DAY_MS;
    const steps: OrgErasureSteps = {
      run: async (job, now) => {
        seen.push(job.step);
        if (job.step === 'org_event' && now < until) return { kind: 'wait', at: until };
        return { kind: 'done', counts: { [job.step]: 1 } };
      },
    };
    const h = harness(orgJob(), steps);
    expect(await h.runner.tick(NOW)).to.equal(2);
    expect(seen).to.deep.equal(['cancel_runs', 'end_sandboxes', 'org_event']);
    expect(h.stored()).to.deep.include({ step: 'org_event', nextAttemptAt: until });
    expect(h.stored()?.counts).to.deep.equal({ cancel_runs: 1, end_sandboxes: 1 });
    await h.runner.tick(NOW + 30 * DAY_MS);
    expect(h.stored()).to.equal(null);
    expect(seen.at(-1)).to.equal('audit');
  });

  it('a failing step waits with backoff and is retried at that step', async () => {
    let fail = true;
    const steps: OrgErasureSteps = {
      run: async () => {
        if (fail) throw new TypeError('down');
        return { kind: 'wait', at: NOW + DAY_MS };
      },
    };
    const h = harness(orgJob(), steps);
    await h.runner.tick(NOW);
    expect(h.stored()).to.deep.include({ step: 'cancel_runs', failures: 1, nextAttemptAt: NOW + erasureBackoffMs(1), lastErrorType: 'TypeError' });
    fail = false;
    await h.runner.tick(NOW + erasureBackoffMs(1));
    expect(h.stored()).to.deep.include({ step: 'cancel_runs', failures: 0 });
  });

  it('a job removed while a pass ran stops the pass', async () => {
    const seen: string[] = [];
    const h = harness(orgJob(), { run: async (job) => { seen.push(job.step); return { kind: 'done' }; } }, { removedAt: 'org_event' });
    await h.runner.tick(NOW);
    expect(seen).to.deep.equal(['cancel_runs', 'end_sandboxes']);
    expect(h.stored()).to.equal(null);
  });
});

describe("CLEANUP-G1 (a): destroying an org's data keys drops them from this replica's key cache too", () => {
  it("this replica cannot open the org's content at once; another org stays readable", async () => {
    const ring = dataKeyRing();
    const context = { streamId: 's' };
    const sealedA = await ring.cipher.seal('org-1', 'journal.data', context, Buffer.from('secret'));
    const sealedB = await ring.cipher.seal('org-2', 'journal.data', context, Buffer.from('other'));
    expect((await ring.cipher.open('org-1', 'journal.data', context, sealedA)).toString()).to.equal('secret');

    expect(await new MongoOrgData(ring.keys).destroyDataKeys('org-1')).to.equal(1);

    const refused = await ring.cipher.open('org-1', 'journal.data', context, sealedA).catch((error: unknown) => error);
    expect((refused as { reason?: string }).reason).to.equal('shredded');
    expect((await ring.cipher.open('org-2', 'journal.data', context, sealedB)).toString()).to.equal('other');
  });
});

/** Every dependency recorded, so a whole job can be walked step by step over fakes. */
function fullWorld(job: PrincipalErasureJob) {
  let stored: PrincipalErasureJob | null = { ...job };
  const calls: Array<{ name: string; args: unknown[] }> = [];
  const record = (name: string, value?: unknown) =>
    sinon.stub().callsFake(async (...args: unknown[]) => {
      calls.push({ name, args });
      return typeof value === 'function' ? (value as (...a: unknown[]) => unknown)(...args) : value;
    });
  const jobs = {
    now: () => Promise.resolve(NOW),
    due: () => Promise.resolve(stored === null ? [] : [stored]),
    writeProgress: sinon.stub().callsFake(async (next: PrincipalErasureJob, expected: PrincipalErasureStep) => {
      if (stored === null || stored.step !== expected) return false;
      stored = { ...next };
      return true;
    }),
    remove: sinon.stub().callsFake(async () => {
      stored = null;
    }),
  };
  const deps = {
    jobs,
    orgSteps: null,
    recorder: { ownerChanged: record('ownerChanged') },
    unsent: {
      uploads: {
        unsent: record('unsent', (_org: unknown, _before: unknown, _limit: unknown, after: unknown) =>
          after === null
            ? {
                uploads: [
                  { recordId: 'up-1', ownerId: 'u-gone', conversationId: null },
                  { recordId: 'up-2', ownerId: 'u-other', conversationId: null },
                ],
                remaining: true,
                next: 'up-2',
              }
            : { uploads: [{ recordId: 'up-3', ownerId: 'u-gone', conversationId: 'c' }], remaining: false, next: null }),
      },
      eraser: {
        eraseAttachments: record('eraseAttachments', (_org: unknown, items: unknown[]) => ({
          attachments: items.length,
          blobs: 0,
          refused: 0,
          unrecorded: 0,
          removed: [],
        })),
      },
    },
    controlPlane: { eraseUser: record('eraseUserCp', { installations: 1, bindings: 2, grants: 1 }) },
    costs: { eraseUser: record('eraseUserCosts', 3) },
    engineChoices: { eraseUser: record('eraseUserEngineChoices', 1) },
    transaction: <T>(work: (tx: unknown) => Promise<T>) => work('tx'),
  };
  const runner = new PrincipalErasureRunner(deps as never);
  const named = (name: string) => calls.filter((call) => call.name === name);
  return { runner, named, stored: () => stored };
}

describe('CLEANUP-G2: every step of the user job, over fakes', () => {
  it("a user job journals each move once, erases only the user's unsent uploads, then their control-plane data, cost rows (C9.06) and coding-engine choice (WP114-FU-ERASE)", async () => {
    const w = fullWorld(
      orgJob({
        kind: 'user',
        userId: 'u-gone',
        step: 'journal_transfers',
        transfers: [{ sessionId: 's-shared', to: 'admin-1' }],
      }),
    );
    expect(await w.runner.tick(NOW)).to.equal(5);
    expect(w.stored()).to.equal(null);
    expect(w.named('ownerChanged')).to.have.length(1);
    expect(w.named('ownerChanged')[0]!.args.slice(0, 3)).to.deep.equal([
      'org-1',
      's-shared',
      { by: 'p_admin', fromUserId: 'u-gone', toUserId: 'admin-1' },
    ]);
    const erased = w
      .named('eraseAttachments')
      .flatMap((call) => (call.args[1] as Array<{ recordId: string }>).map((item) => item.recordId));
    expect(erased).to.deep.equal(['up-1', 'up-3']);
    expect(w.named('eraseUserCp')[0]!.args).to.deep.equal(['org-1', 'u-gone']);
    expect(w.named('eraseUserCosts')[0]!.args).to.deep.equal(['org-1', 'u-gone']);
    expect(w.named('eraseUserEngineChoices')[0]!.args).to.deep.equal(['org-1', 'u-gone']);
  });

});

describe('CLEANUP-G1: the built-in org steps erase a deleted org at once', () => {
  function orgWorld() {
    const calls: string[] = [];
    const log = (name: string, value: unknown) => async () => {
      calls.push(name);
      return value;
    };
    let pages = 0;
    const enqueued: Array<{ orgId: string; sessionId: string; reason: string }> = [];
    const parts = {
      jobs: {} as never,
      org: {
        activeRuns: log('activeRuns', [{ runId: 'r-1', status: 'running', revision: 3 }]),
        sessionsAfter: async (_org: string, after: string | null) => {
          calls.push('sessionsAfter');
          pages += 1;
          return after === null ? ['s-1', 's-2'] : [];
        },
        markSessionsDeleted: log('markSessionsDeleted', undefined),
        pendingSessionErasures: log('pendingSessionErasures', 0),
        eraseStores: log('eraseStores', { agentJournalPayloads: 4, sessionEvents: 9 }),
        destroyDataKeys: log('destroyDataKeys', 1),
        eraseAudit: log('eraseAudit', 7),
      },
      sessions: {
        enqueue: async (request: { orgId: string; sessionId: string; reason: string }) => {
          enqueued.push(request);
        },
      },
      executions: { control: log('cancelRun', undefined) },
      reaper: { reap: log('reap', 2) },
      sandboxes: { eraseOrg: log('sandboxes', { sandboxes: 1, folders: 1 }) },
      queryService: {
        eraseOrg: log('queryService', { attachments: 1, artifacts: 0, blobs: 0, skillCandidates: 0, remaining: false }),
      },
      principals: [{ store: 'agentMemories', eraseOrg: log('memories', 3) }],
      redis: { eraseOrg: log('redis', 5) },
      announcer: { orgDeleted: log('announced', undefined) },
      transaction: <T>(work: (tx: unknown) => Promise<T>) => work('tx'),
      logger: { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() },
    };
    return { parts, calls, enqueued, pages: () => pages };
  }

  it("cancels its runs, erases every session through its own erasure job, then its documents, keys and audit chain, and removes the job", async () => {
    const w = orgWorld();
    const steps = new ImmediateOrgErasureSteps(w.parts as never);
    const h = harness(orgJob({ auditRetentionDays: 0 }), steps);
    for (let pass = 0; pass < 5 && h.stored() !== null; pass += 1) await h.runner.tick(NOW);
    expect(h.stored()).to.equal(null);
    expect(w.enqueued.map((request) => [request.sessionId, request.reason])).to.deep.equal([
      ['s-1', 'user_delete'],
      ['s-2', 'user_delete'],
    ]);
    for (const name of ['cancelRun', 'reap', 'markSessionsDeleted', 'queryService', 'sandboxes', 'memories', 'eraseStores', 'redis', 'destroyDataKeys', 'eraseAudit']) {
      expect(w.calls, name).to.include(name);
    }
    expect(w.calls.indexOf('destroyDataKeys')).to.be.lessThan(w.calls.indexOf('eraseAudit'));
    // The deletion request announced the org's deletion; the job does not announce it again.
    expect(w.calls).to.not.include('announced');
  });

  it('keeps the audit chain for the retention period the job names, then erases it', async () => {
    const w = orgWorld();
    const h = harness(orgJob({ auditRetentionDays: 2 }), new ImmediateOrgErasureSteps(w.parts as never));
    for (let pass = 0; pass < 5; pass += 1) await h.runner.tick(NOW);
    expect(h.stored()).to.deep.include({ step: 'audit_retention', nextAttemptAt: NOW + 2 * DAY_MS });
    expect(w.calls).to.not.include('eraseAudit');
    await h.runner.tick(NOW + 2 * DAY_MS);
    expect(h.stored()).to.equal(null);
    expect(w.calls).to.include('eraseAudit');
  });
});
