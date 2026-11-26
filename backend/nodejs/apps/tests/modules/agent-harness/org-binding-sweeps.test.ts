/**
 * ADR-SEC-02 (WP-116): each sweep, scheduler and job starts with no org bound, as a detached timer does, and works every
 * row its cross-org read found as that row's org; a subscription's callback runs in the context it was registered in.
 * The fakes record the org bound (`getCurrentOrgId()`) at each per-row call.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  forOrg,
  getCurrentOrgId,
  spawnDetached,
} from '../../../src/libs/context/orgContext';
import { InMemoryJournalTail } from '../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { InMemoryLiveBus } from '../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { BrokerTriggerSource } from '../../../src/modules/agent-harness/adapters/messaging/broker-trigger-source';
import { AuditAnchorService } from '../../../src/modules/agent-harness/application/audit-anchoring';
import { AuditLinkWatch } from '../../../src/modules/agent-harness/application/audit-link-watch';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { MemoryService } from '../../../src/modules/agent-harness/application/memory-service';
import { NextRunService } from '../../../src/modules/agent-harness/application/next-run-service';
import {
  OperatorStopService,
  type OperatorStopsDeps,
} from '../../../src/modules/agent-harness/application/operator-controls';
import { PrincipalErasureRunner } from '../../../src/modules/agent-harness/application/principal-erasure';
import {
  RetentionSweeper,
  type RetentionSweeperDependencies,
} from '../../../src/modules/agent-harness/application/retention-sweeper';
import { RunJournalReader } from '../../../src/modules/agent-harness/application/run-journal-reader';
import { cancelOpenRuns } from '../../../src/modules/agent-harness/application/run-stopper';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { SessionErasureWorker } from '../../../src/modules/agent-harness/application/session-erasure';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import type {
  OperatorControl,
  OperatorScope,
} from '../../../src/modules/agent-harness/domain/operator-controls';
import type { LiveDelta } from '../../../src/modules/agent-harness/projection/frames';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import {
  contractClock,
  createContractHarness,
} from './support/execution-store-contract';
import { aFiring, aSchedule } from './support/schedules';

const ORG_A = '0000000000000000000000a1';
const ORG_B = '0000000000000000000000b2';
const ORG_C = '0000000000000000000000c3';

interface Seen {
  what: string;
  /** The org the row (or the call) names. */
  org: string;
  /** The org bound when the call was made. */
  bound: string | undefined;
}

/** Each per-row call, with the org it names and the org bound at the time. */
class Calls {
  readonly seen: Seen[] = [];

  at(what: string, org: string): void {
    this.seen.push({ what, org, bound: getCurrentOrgId() });
  }

  of(what: string): Seen[] {
    return this.seen.filter((s) => s.what === what);
  }

  /** Every recorded call ran as the org it names, and each of `whats` was made at least once. */
  expectBound(...whats: string[]): void {
    for (const what of whats) {
      expect(this.of(what), `${what} was called`).to.not.have.length(0);
    }
    for (const s of this.seen) {
      expect(s.bound, `${s.what} for ${s.org}`).to.equal(s.org);
    }
  }
}

/** As a detached timer starts its tick: no org bound. */
const detached = <T>(fn: () => Promise<T>): Promise<T> =>
  spawnDetached(() => {
    expect(getCurrentOrgId()).to.equal(undefined);
    return fn();
  });

const run = (orgId: string, runId: string, extra: Partial<HarnessRun> = {}): HarnessRun =>
  ({
    orgId,
    runId,
    sessionId: `s-${runId}`,
    actorId: 'actor',
    revision: 1,
    status: 'running',
    fence: 1,
    attemptId: null,
    workerId: null,
    ...extra,
  }) as unknown as HarnessRun;

const lease = { tryAcquire: async () => true, release: async () => undefined };

describe('ADR-SEC-02: sweeps and jobs work each row as its org', () => {
  afterEach(() => {
    sinon.restore();
  });

  it('the next-run drain drains each session as its org', async () => {
    const calls = new Calls();
    const service = new NextRunService({
      store: {
        drainable: async () => [
          { orgId: ORG_A, sessionId: 's-1' },
          { orgId: ORG_B, sessionId: 's-2' },
        ],
        nextPending: async (orgId: string) => {
          calls.at('nextPending', orgId);
          return null;
        },
      },
      readmission: {},
    } as unknown as ConstructorParameters<typeof NextRunService>[0]);
    await detached(() => service.drainDue(10));
    calls.expectBound('nextPending');
    expect(calls.of('nextPending').map((s) => s.org)).to.deep.equal([ORG_A, ORG_B]);
  });

  it('the memory settle reads each pending run as its org', async () => {
    const calls = new Calls();
    const service = new MemoryService({
      store: {
        pendingRuns: async () => [
          { orgId: ORG_A, runId: 'r-1' },
          { orgId: ORG_B, runId: 'r-2' },
        ],
      },
      settings: {},
      access: {},
      runs: {
        readEnding: async (orgId: string) => {
          calls.at('readEnding', orgId);
          return null;
        },
      },
    } as unknown as ConstructorParameters<typeof MemoryService>[0]);
    await detached(() => service.settleDue(10));
    calls.expectBound('readEnding');
    expect(calls.of('readEnding')).to.have.length(2);
  });

});

describe('ADR-SEC-02: operator controls and the run stopper', () => {
  const control = (scope: OperatorScope, orgId: string): OperatorControl => ({
    id: `${scope.kind}:${orgId}`,
    scope,
    orgId: ['session', 'agent', 'org'].includes(scope.kind) ? orgId : null,
    reason: 'security',
    note: null,
    setBy: 'p_admin',
    setAt: 1,
  });

  const service = (
    calls: Calls,
    open: (filter: { orgId?: string }) => HarnessRun[],
  ): OperatorStopService =>
    new OperatorStopService({
      runs: {
        openRuns: async (filter: { orgId?: string; suspendedFor?: string }) => {
          if (filter.orgId !== undefined) calls.at('openRuns', filter.orgId);
          return open(filter).map((r) => ({ run: r, engine: null }));
        },
        get: async () => null,
      },
      executions: {
        control: async (command: { orgId: string }) => {
          calls.at('control', command.orgId);
          return {};
        },
      },
      deploymentKillSwitch: false,
    } as unknown as OperatorStopsDeps);

  it("an org's control reads that org's runs as that org and controls each as its org", async () => {
    const calls = new Calls();
    const controls = service(calls, () => [run(ORG_A, 'r-1')]);
    await detached(() => controls.enforce(control({ kind: 'org' }, ORG_A)));
    calls.expectBound('openRuns', 'control');
  });

  it("an instance-wide control set from another org's request controls each run as the run's org", async () => {
    const calls = new Calls();
    let passes = 0;
    const controls = service(calls, () => {
      passes += 1;
      return passes === 1 ? [run(ORG_A, 'r-1'), run(ORG_C, 'r-2')] : [];
    });
    await detached(() =>
      forOrg(ORG_B, 'test-request', () => controls.enforce(control({ kind: 'instance' }, ORG_B))),
    );
    calls.expectBound('control');
    expect(calls.of('control').map((s) => s.org)).to.deep.equal([ORG_A, ORG_C]);
  });

  it("the run stopper cancels each run as the run's org, whatever the caller has bound", async () => {
    const calls = new Calls();
    let passes = 0;
    const executions = {
      control: async (command: { orgId: string }) => {
        calls.at('control', command.orgId);
        return {} as never;
      },
    } as unknown as Parameters<typeof cancelOpenRuns>[0];
    await detached(() =>
      forOrg(ORG_B, 'test-request', () =>
        cancelOpenRuns(
          executions,
          async () => {
            passes += 1;
            return passes === 1 ? [run(ORG_A, 'r-1'), run(ORG_C, 'r-2')] : [];
          },
          { commandId: (r) => `stop:${r.runId}`, by: 'p_admin' },
        ),
      ),
    );
    calls.expectBound('control');
    expect(calls.of('control')).to.have.length(2);
  });
});

/** A schedule store whose per-org calls record the org bound; the cross-org reads answer `rows`. */
const scheduleStore = (calls: Calls, rows: Record<string, unknown[]>): unknown =>
  new Proxy(
    {},
    {
      get: (_target, name) => {
        if (name === 'then' || typeof name !== 'string') return undefined;
        return async (...args: unknown[]): Promise<unknown> => {
          if (name === 'now') return Date.parse('2026-07-20T08:00:00.000Z');
          if (name in rows) return rows[name];
          const first = args[0];
          const org =
            typeof first === 'string'
              ? first
              : (first as { orgId?: unknown } | null)?.orgId;
          if (typeof org === 'string') calls.at(name, org);
          return null;
        };
      },
    },
  );

describe('ADR-SEC-02: the schedule firer', () => {
  const firer = (calls: Calls, rows: Record<string, unknown[]>): ScheduleFirer =>
    new ScheduleFirer({
      store: scheduleStore(calls, rows),
      admission: {},
      actors: {},
      setting: {},
      runs: {
        status: async (orgId: string) => {
          calls.at('status', orgId);
          return 'running';
        },
      },
    } as unknown as ConstructorParameters<typeof ScheduleFirer>[0]);

  it("a due cron slot is claimed as its schedule's org", async () => {
    const calls = new Calls();
    const early = Date.parse('2026-07-01T00:00:00.000Z');
    const a = aSchedule({ orgId: ORG_A }, early);
    const b = aSchedule({ orgId: ORG_B }, early);
    await detached(() => firer(calls, { dueCron: [a, b] }).fireDue());
    calls.expectBound('claimCronSlot');
    expect(calls.of('claimCronSlot').map((s) => s.org)).to.deep.equal([ORG_A, ORG_B]);
  });

  it("a stale firing is fired again as its org", async () => {
    const calls = new Calls();
    const stale = [aFiring(aSchedule({ orgId: ORG_A })), aFiring(aSchedule({ orgId: ORG_B }))];
    await detached(() => firer(calls, { takeStale: stale }).retryStale());
    expect(new Set(calls.seen.map((s) => s.org))).to.deep.equal(new Set([ORG_A, ORG_B]));
    calls.expectBound();
  });

  it("a watched firing's run is read as its org", async () => {
    const calls = new Calls();
    const watched = [
      { ...aFiring(aSchedule({ orgId: ORG_A })), runId: 'r-1' },
      { ...aFiring(aSchedule({ orgId: ORG_B })), runId: 'r-2' },
    ];
    await detached(() => firer(calls, { watched }).watchRuns());
    calls.expectBound('markChecked', 'status');
    expect(calls.of('status').map((s) => s.org)).to.deep.equal([ORG_A, ORG_B]);
  });

  it("a connector event is delivered as the event's org", async () => {
    const calls = new Calls();
    const source = new BrokerTriggerSource({
      createConsumer: () => {
        throw new Error('not started');
      },
      streams: () => [],
      clock: async () => 1_000,
      deliver: async (event) => {
        calls.at('deliver', event.orgId);
        return [];
      },
    });
    await detached(() =>
      source.handle({
        value: { eventType: 'newRecord', payload: { orgId: ORG_A, connectorId: 'c-1' } },
      } as unknown as Parameters<BrokerTriggerSource['handle']>[0]),
    );
    calls.expectBound('deliver');
  });
});

describe('ADR-SEC-02: the retention sweep', () => {
  it('each part works every org, session, fork and erasure it found as its org', async () => {
    const calls = new Calls();
    const deps = {
      store: {
        now: async () => 1_000_000_000_000,
        collectPayloads: async () => 0,
        expireCommandReceipts: async () => 0,
        expireAdmissionReceipts: async () => 0,
        harnessOrgs: async () => [ORG_A, ORG_B],
      },
      settings: {
        all: async () => [
          { orgId: ORG_A, days: 1 },
          { orgId: ORG_B, days: 1 },
        ],
      },
      conversations: {
        idleSince: async (orgId: string) => {
          calls.at('idleSince', orgId);
          return [`s-${orgId}`];
        },
        expire: async (orgId: string) => {
          calls.at('expire', orgId);
          return true;
        },
        awaitingErasure: async () => [
          { orgId: ORG_A, sessionId: 's-3', by: null, requestedAt: 1 },
          { orgId: ORG_B, sessionId: 's-4', by: 'u-1', requestedAt: 2 },
        ],
      },
      erasures: {
        enqueue: async (job: { orgId: string }) => {
          calls.at('enqueue', job.orgId);
          return true;
        },
        enqueueMany: async (jobs: Array<{ orgId: string }>) => {
          for (const job of jobs) calls.at('enqueue', job.orgId);
          return jobs.length;
        },
      },
      runs: {
        runsOf: async (orgId: string) => {
          calls.at('runsOf', orgId);
          return [];
        },
      },
      artifacts: {
        collectDiscarded: async (orgId: string) => {
          calls.at('collectDiscarded', orgId);
          return { artifacts: 0 };
        },
      },
      agentErasures: {
        queue: {
          due: async () => [
            { orgId: ORG_B, agentKey: 'agent-1', confirmed: true, failures: 0, requestedAt: 0 },
          ],
          settle: async (orgId: string) => {
            calls.at('settle', orgId);
          },
        },
        memories: {
          eraseAgent: async (orgId: string) => {
            calls.at('eraseAgent', orgId);
            return 0;
          },
        },
        deletions: { state: async () => 'deleted' },
      },
      abandonedForks: {
        list: async () => [{ orgId: ORG_A, forkSessionId: 'f-1' }],
        dropMany: async (orgId: string, forks: unknown[]) => {
          calls.at('drop', orgId);
          return forks;
        },
      },
      unsentAttachments: {
        carriers: {
          orgs: async () => [ORG_B],
          carriedBy: async (orgId: string) => {
            calls.at('carriedBy', orgId);
            return new Map<string, string>();
          },
        },
        uploads: {
          unsent: async (orgId: string) => {
            calls.at('unsent', orgId);
            return { uploads: [{ recordId: 'rec-1', ownerId: 'u-1' }], remaining: false, next: null };
          },
          markSent: async () => undefined,
        },
        eraser: {
          eraseAttachments: async (orgId: string) => {
            calls.at('eraseAttachments', orgId);
            return { attachments: 1 };
          },
        },
      },
      snapshots: {
        index: {
          orgs: async () => [ORG_A],
          takenBefore: async (orgId: string) => {
            calls.at('takenBefore', orgId);
            return [];
          },
          forget: async (orgId: string) => {
            calls.at('forget', orgId);
            return 0;
          },
        },
        ttl: {
          days: async (orgId: string) => {
            calls.at('days', orgId);
            return 1;
          },
        },
        objects: { purge: async () => undefined },
      },
      transaction: async <T>(work: (tx: unknown) => Promise<T>, scope?: { orgId: string }) => {
        if (scope !== undefined) calls.at('transaction', scope.orgId);
        return work({});
      },
      lease,
      holderId: 'test',
    } as unknown as RetentionSweeperDependencies<unknown>;
    const report = await detached(() => new RetentionSweeper(deps).tick());
    expect(report).to.not.equal(null);
    calls.expectBound(
      'idleSince',
      'runsOf',
      'expire',
      'transaction',
      'enqueue',
      'collectDiscarded',
      'eraseAgent',
      'settle',
      'drop',
      'unsent',
      'carriedBy',
      'eraseAttachments',
      'days',
      'takenBefore',
      'forget',
    );
    // The lost-enqueue pass enqueued both, each as its own org.
    expect(calls.of('enqueue').map((s) => s.org)).to.include.members([ORG_A, ORG_B]);
  });
});

describe('ADR-SEC-02: erasure jobs', () => {
  it('the principal erasure tick runs each job as its org', async () => {
    const calls = new Calls();
    const service = new PrincipalErasureRunner({
      jobs: { due: async () => [{ orgId: ORG_A }, { orgId: ORG_B }] },
    } as unknown as ConstructorParameters<typeof PrincipalErasureRunner>[0]);
    sinon.stub(service, 'run').callsFake(async (job: { orgId: string }) => {
      calls.at('run', job.orgId);
      return 0;
    });
    await detached(() => service.tick(1_000));
    calls.expectBound('run');
    expect(calls.of('run')).to.have.length(2);
  });

  it('the session erasure worker runs each claimed job as its org', async () => {
    const calls = new Calls();
    const claimed = [{ orgId: ORG_A, sessionId: 's-1' }, { orgId: ORG_B, sessionId: 's-2' }];
    const worker = new SessionErasureWorker({
      jobs: { claimDue: async () => claimed.shift() ?? null },
      runner: {
        run: async (job: { orgId: string }) => {
          calls.at('run', job.orgId);
          return 'completed';
        },
      },
      lease,
      holderId: 'test',
    } as unknown as ConstructorParameters<typeof SessionErasureWorker>[0]);
    expect(await detached(() => worker.tick(5))).to.equal(2);
    calls.expectBound('run');
  });
});

describe('ADR-SEC-02: the audit jobs', () => {
  it("the anchor pass writes and marks each org's head as its org", async () => {
    const calls = new Calls();
    const anchoring = new AuditAnchorService({
      chain: {
        unanchored: async () => [
          { orgId: ORG_A, seq: 1, hash: 'h-1' },
          { orgId: ORG_B, seq: 2, hash: 'h-2' },
        ],
        unlinked: async () => [],
        markAnchored: async (orgId: string) => {
          calls.at('markAnchored', orgId);
        },
      },
      sink: {
        name: 'test',
        write: async (anchor: { orgId: string }) => {
          calls.at('write', anchor.orgId);
        },
      },
      lease,
      holderId: 'test',
    } as unknown as ConstructorParameters<typeof AuditAnchorService>[0]);
    expect(await detached(() => anchoring.tick())).to.equal(2);
    calls.expectBound('write', 'markAnchored');
  });

  it('the link watch links each unlinked org as its org', async () => {
    const calls = new Calls();
    let reads = 0;
    const watch = new AuditLinkWatch({
      chain: {
        unlinked: async () => {
          reads += 1;
          return reads === 1
            ? [
                { orgId: ORG_A, appends: 1, oldestAt: 0 },
                { orgId: ORG_B, appends: 2, oldestAt: 0 },
              ]
            : [];
        },
        head: async (orgId: string) => {
          calls.at('head', orgId);
          return null;
        },
      },
      metrics: { unlinked: () => undefined },
    } as unknown as ConstructorParameters<typeof AuditLinkWatch>[0]);
    await detached(() => watch.tick());
    calls.expectBound('head');
    expect(calls.of('head')).to.have.length(2);
  });
});

describe('ADR-SEC-02: the run sweep', () => {
  it('the unclaimed, stranded and orphaned runs and the swept callbacks are each worked as their org', async () => {
    const calls = new Calls();
    const store = new InMemoryExecutionStore();
    const h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads: new InMemoryPayloadStore(),
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
        childRuns: {
          orphanedChildren: async () => [run(ORG_C, 'child-1', { parent: { runId: 'p-1' } } as Partial<HarnessRun>)],
        },
      } as unknown as Parameters<typeof createContractHarness>[0],
      contractClock(),
    );
    const service: ExecutionService = h.service;
    const internals = service as unknown as Record<string, unknown>;
    const swept = run(ORG_A, 'swept-1');
    sinon.stub(store, 'sweep').callsFake(async (_limit, transcripts) => {
      await transcripts?.openCalls?.(swept);
      await transcripts?.project(swept, []);
      transcripts?.expired?.(swept, 'failed', 'deadline_exceeded');
      return 0;
    });
    sinon.stub(store, 'promoteInline').resolves(0);
    sinon.stub(store, 'unclaimedRuns').resolves([run(ORG_B, 'unclaimed-1')]);
    sinon.stub(store, 'strandedRuns').resolves([
      { run: run(ORG_A, 'stranded-1'), pinned: false },
      { run: run(ORG_B, 'stranded-2'), pinned: true },
    ] as never);
    sinon.stub(store, 'relaxRequirement').callsFake(async (orgId) => {
      calls.at('relaxRequirement', orgId);
      return true;
    });
    internals['failRun'] = async (r: HarnessRun) => {
      calls.at('failRun', r.orgId);
    };
    internals['control'] = async (command: { orgId: string }) => {
      calls.at('control', command.orgId);
      return {};
    };
    internals['openCalls'] = async (r: HarnessRun) => {
      calls.at('openCalls', r.orgId);
      return [];
    };
    internals['transcriptOf'] = async (r: HarnessRun) => {
      calls.at('transcriptOf', r.orgId);
      return undefined;
    };
    internals['ended'] = (_before: unknown, after: HarnessRun) => {
      calls.at('ended', after.orgId);
    };
    service.watchWorkerFeatures(
      { live: async () => false, metrics: { stranded: () => undefined } } as unknown as Parameters<
        ExecutionService['watchWorkerFeatures']
      >[0],
      ['feature-x'] as unknown as Parameters<ExecutionService['watchWorkerFeatures']>[1],
    );
    await detached(() => service.sweep(100));
    calls.expectBound('openCalls', 'transcriptOf', 'ended', 'control', 'relaxRequirement', 'failRun');
    expect(calls.of('failRun').map((s) => s.org)).to.deep.equal([ORG_B, ORG_B]);
  });
});

describe('ADR-SEC-02: subscriptions are told in the context they were registered in', () => {
  it('a journal tail watcher', async () => {
    const tail = new InMemoryJournalTail();
    const told: Array<string | undefined> = [];
    forOrg(ORG_A, 'test-stream', () => {
      tail.watch(ORG_A, 'r-1', () => {
        told.push(getCurrentOrgId());
      });
    });
    await detached(async () => {
      tail.changed(ORG_A, 'r-1', 3);
    });
    expect(told).to.deep.equal([ORG_A]);
  });

  it('a live bus follower', async () => {
    const bus = new InMemoryLiveBus();
    const told: Array<string | undefined> = [];
    forOrg(ORG_A, 'test-stream', () => {
      bus.follow(
        'r-1',
        '0',
        () => {
          told.push(getCurrentOrgId());
        },
        () => undefined,
      );
    });
    await detached(async () => {
      bus.publish({
        runId: 'r-1',
        fence: 1,
        modelAttempt: 1,
        n: 1,
        kind: 'text_delta',
        data: { text: 'a' },
      } as LiveDelta);
    });
    expect(told).to.deep.equal([ORG_A]);
  });

  it('a run journal follower', async () => {
    const event = { seq: 1, runId: 'r-1', type: 'turn.started', data: {} } as unknown as JournalEvent;
    const reader = new RunJournalReader({
      journal: {
        readRun: async (_orgId: string, _runId: string, afterSeq: number) => (afterSeq === 0 ? [event] : []),
      },
      journals: { resolve: async (events: JournalEvent[]) => events },
    } as unknown as ConstructorParameters<typeof RunJournalReader>[0]);
    const told: Array<string | undefined> = [];
    forOrg(ORG_A, 'test-stream', () => {
      reader.follow(
        ORG_A,
        'r-1',
        () => 0,
        () => {
          told.push(getCurrentOrgId());
        },
      );
    });
    await detached(async () => {
      await reader.read(ORG_A, 'r-1');
    });
    expect(told).to.deep.equal([ORG_A]);
  });
});
