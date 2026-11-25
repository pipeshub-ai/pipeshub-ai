import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryControlPlaneLiveness } from '../../../src/modules/agent-harness/adapters/in-memory-control-plane-liveness';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  controlPlaneGap,
  LIVENESS_GAP_MS,
  RunSweeper,
  SWEEP_BATCH_LIMIT,
  SWEEP_INTERVAL_MS,
  SWEEPER_LEASE_MS,
  SWEEPER_LEASE_NAME,
} from '../../../src/modules/agent-harness/application/run-sweeper';
import { LeaderLease } from '../../../src/modules/agent-harness/ports/leader-lease';
import {
  contractClock,
  ContractClock,
  createContractHarness,
} from './support/execution-store-contract';

type Line = [level: string, message: string, meta: Record<string, unknown>];

const recording = (): HarnessLogger & { lines: Line[] } => {
  const lines: Line[] = [];
  const at =
    (level: string) =>
    (message: string, meta: Record<string, unknown> = {}) => {
      lines.push([level, message, meta]);
    };
  return {
    lines,
    debug: at('debug'),
    info: at('info'),
    warn: at('warn'),
    error: at('error'),
  };
};

const acquired = (holder: string): Line => [
  'info',
  'sweeper.leader_acquired',
  { event: 'sweeper.leader_acquired', holder },
];

const leader = (holds = true): LeaderLease => ({
  tryAcquire: sinon.stub().resolves(holds),
  release: sinon.stub().resolves(),
});

describe('RunSweeper', () => {
  it('sweeps one bounded page only while it holds the leader lease', async () => {
    const executions = { sweep: sinon.stub().resolves(3) };
    const lease = leader();
    const log = recording();
    const sweeper = new RunSweeper({
      executions,
      lease,
      liveness: new InMemoryControlPlaneLiveness(),
      logger: log,
      holderId: 'holder-a',
    });
    expect(await sweeper.tick()).to.equal(3);
    expect(
      (lease.tryAcquire as sinon.SinonStub).calledOnceWithExactly(
        SWEEPER_LEASE_NAME,
        'holder-a',
        SWEEPER_LEASE_MS,
      ),
    ).to.equal(true);
    expect(executions.sweep.calledOnceWithExactly(SWEEP_BATCH_LIMIT)).to.equal(
      true,
    );
    expect(log.lines).to.deep.equal([
      acquired('holder-a'),
      [
        'info',
        'sweeper.swept',
        { event: 'sweeper.swept', swept: 3, limit: SWEEP_BATCH_LIMIT },
      ],
    ]);
  });

  it('SS2 NR-8: the leader admits the next runs a run end did not drain, and logs the count', async () => {
    const nextRuns = { drainDue: sinon.stub().resolves(2) };
    const log = recording();
    const sweeper = new RunSweeper({
      executions: { sweep: sinon.stub().resolves(0) },
      lease: leader(),
      liveness: new InMemoryControlPlaneLiveness(),
      logger: log,
      holderId: 'holder-n',
      reaper: null,
      nextRuns,
    });
    await sweeper.tick();
    expect(nextRuns.drainDue.calledOnceWithExactly(SWEEP_BATCH_LIMIT)).to.equal(true);
    expect(log.lines).to.deep.equal([
      acquired('holder-n'),
      [
        'info',
        'sweeper.next_runs_admitted',
        { event: 'sweeper.next_runs_admitted', admitted: 2 },
      ],
    ]);
    const follower = { drainDue: sinon.stub().resolves(0) };
    await new RunSweeper({
      executions: { sweep: sinon.stub().resolves(0) },
      lease: leader(false),
      liveness: new InMemoryControlPlaneLiveness(),
      logger: recording(),
      holderId: 'holder-m',
      reaper: null,
      nextRuns: follower,
    }).tick();
    expect(follower.drainDue.called).to.equal(false);
  });

  it('FX4-7: the leader settles the memories of runs whose settlement a crash missed, and logs the count', async () => {
    const asked: number[] = [];
    const memories = {
      settleDue: async (limit: number): Promise<number> => {
        asked.push(limit);
        return 2;
      },
    };
    const log = recording();
    const sweeper = new RunSweeper({
      executions: { sweep: async () => 0 },
      lease: leader(),
      liveness: new InMemoryControlPlaneLiveness(),
      logger: log,
      holderId: 'holder-s',
      reaper: null,
      nextRuns: null,
      memories,
    });
    await sweeper.tick();
    expect(asked).to.deep.equal([SWEEP_BATCH_LIMIT]);
    expect(log.lines).to.deep.equal([
      acquired('holder-s'),
      [
        'info',
        'sweeper.memories_settled',
        { event: 'sweeper.memories_settled', runs: 2 },
      ],
    ]);
  });

  it('does nothing on a replica that is not the leader', async () => {
    const executions = { sweep: sinon.stub().resolves(1) };
    const sweeper = new RunSweeper({
      executions,
      lease: leader(false),
      liveness: new InMemoryControlPlaneLiveness(),
      logger: recording(),
      holderId: 'holder-b',
    });
    expect(await sweeper.tick()).to.equal(0);
    expect(executions.sweep.called).to.equal(false);
  });

  it('survives a failing tick and releases its lease on stop', async () => {
    const log = recording();
    const lease: LeaderLease = {
      tryAcquire: sinon.stub().rejects(new Error('mongo down')),
      release: sinon.stub().resolves(),
    };
    const sweeper = new RunSweeper({
      executions: { sweep: sinon.stub() },
      lease,
      liveness: new InMemoryControlPlaneLiveness(),
      logger: log,
      holderId: 'holder-c',
    });
    expect(await sweeper.tick()).to.equal(0);
    expect(
      log.lines.map(([level, message]) => `${level}:${message}`),
    ).to.deep.equal(['error:sweeper.tick_failed']);
    await sweeper.stop();
    expect(
      (lease.release as sinon.SinonStub).calledOnceWithExactly(
        SWEEPER_LEASE_NAME,
        'holder-c',
      ),
    ).to.equal(true);
  });

  describe('OPS1: leadership', () => {
    /** A lease on the test's clock: held until released or `ttlMs` after its last renewal. */
    const clockedLease = (now: () => number): LeaderLease => {
      let held: { holder: string; until: number } | null = null;
      return {
        tryAcquire: async (_name, holder, ttlMs) => {
          if (held !== null && held.holder !== holder && held.until > now()) {
            return false;
          }
          held = { holder, until: now() + ttlMs };
          return true;
        },
        release: async (_name, holder) => {
          if (held?.holder === holder) held = null;
        },
      };
    };
    const replica = (holderId: string, lease: LeaderLease) => {
      const log = recording();
      const sweeper = new RunSweeper({
        executions: { sweep: async () => 0 },
        lease,
        liveness: new InMemoryControlPlaneLiveness(),
        logger: log,
        holderId,
      });
      const leadership = (): string[] =>
        log.lines
          .map(([, message]) => message)
          .filter((message) => message.startsWith('sweeper.leader_'));
      return { sweeper, leadership };
    };

    it('OPS1-1-4: the leader lease is three sweep intervals, and the gap bound follows it', () => {
      expect(SWEEPER_LEASE_MS).to.equal(3 * SWEEP_INTERVAL_MS);
      expect(LIVENESS_GAP_MS).to.equal(SWEEPER_LEASE_MS + 2 * SWEEP_INTERVAL_MS);
    });

    it('OPS1-1-4: a leader that misses two renewals still leads; one that stops renewing is replaced within the lease plus one interval', async () => {
      const clock = contractClock();
      const lease = clockedLease(() => clock.now());
      const a = replica('a', lease);
      const b = replica('b', lease);
      await a.sweeper.tick();
      for (let missed = 1; missed <= 2; missed += 1) {
        clock.set(clock.now() + SWEEP_INTERVAL_MS);
        await b.sweeper.tick();
      }
      expect(b.leadership()).to.deep.equal([]);
      clock.set(clock.now() + SWEEP_INTERVAL_MS);
      await b.sweeper.tick();
      expect(b.leadership()).to.deep.equal(['sweeper.leader_acquired']);
    });

    it('OPS1-1-6: leadership is logged when it changes hands, not per tick', async () => {
      const clock = contractClock();
      const lease = clockedLease(() => clock.now());
      const a = replica('a', lease);
      const b = replica('b', lease);
      await a.sweeper.tick();
      await a.sweeper.tick();
      await b.sweeper.tick();
      expect(a.leadership()).to.deep.equal(['sweeper.leader_acquired']);
      expect(b.leadership()).to.deep.equal([]);

      // A stalls past its lease: B leads, and A learns it lost on its next tick.
      clock.set(clock.now() + SWEEPER_LEASE_MS);
      await b.sweeper.tick();
      await a.sweeper.tick();
      expect(a.leadership()).to.deep.equal(['sweeper.leader_acquired', 'sweeper.leader_lost']);
      expect(b.leadership()).to.deep.equal(['sweeper.leader_acquired']);
    });

    it('OPS1-1-2: a leader that stops hands over at once; a follower that stops says nothing', async () => {
      const clock = contractClock();
      const lease = clockedLease(() => clock.now());
      const a = replica('a', lease);
      const b = replica('b', lease);
      const c = replica('c', lease);
      await a.sweeper.tick();
      await c.sweeper.tick();
      await c.sweeper.stop();
      await a.sweeper.stop();
      await b.sweeper.tick();
      expect(a.leadership()).to.deep.equal(['sweeper.leader_acquired', 'sweeper.leader_released']);
      expect(b.leadership()).to.deep.equal(['sweeper.leader_acquired']);
      expect(c.leadership()).to.deep.equal([]);
    });
  });

  describe('O-6a / C-13: a control-plane gap is logged, not waited out', () => {
    it('a gap is a silence longer than a leader handover', () => {
      expect(
        controlPlaneGap({ now: 10, previousSeenAt: null, holdoffUntil: 0 }),
      ).to.equal(null);
      expect(
        controlPlaneGap({ now: LIVENESS_GAP_MS, previousSeenAt: 0, holdoffUntil: 0 }),
      ).to.equal(null);
      expect(
        controlPlaneGap({ now: 100_000, previousSeenAt: 0, holdoffUntil: 0 }),
      ).to.deep.equal({ gapMs: 100_000 });
    });

    const fleet = async (clock: ContractClock, runs: number) => {
      const store = new InMemoryExecutionStore(clock);
      const h = createContractHarness(
        {
          executionStore: store,
          journal: store.journal,
          payloads: new InMemoryPayloadStore(),
          admissionStore: store,
          dispatches: async () => [],
          deletePayload: async () => undefined,
        },
        clock,
      );
      const claimed = [];
      for (let index = 0; index < runs; index += 1) {
        const { run } = await h.admit();
        claimed.push(await h.claim(run, `worker-${String(index)}`, 1_000));
      }
      const queued = (await h.admit()).run;
      return { h, store, claimed, queued };
    };

    /** Ticks every SWEEP_INTERVAL_MS for `ms`, as the running sweeper does; total swept. */
    const tickFor = async (
      clock: ContractClock,
      sweeper: RunSweeper,
      ms: number,
    ): Promise<number> => {
      let swept = 0;
      for (let at = 0; at < ms; at += SWEEP_INTERVAL_MS) {
        clock.set(clock.now() + SWEEP_INTERVAL_MS);
        swept += await sweeper.tick();
      }
      return swept;
    };

    it('after a full outage the returning control plane expires what lapsed on its first tick', async () => {
      const clock = contractClock();
      const { h, claimed, queued } = await fleet(clock, 5);
      const liveness = new InMemoryControlPlaneLiveness(clock);
      const log = recording();
      const sweeper = new RunSweeper({
        executions: h.service,
        lease: leader(),
        liveness,
        logger: log,
        holderId: 'a',
      });
      expect(await sweeper.tick()).to.equal(0);

      // Node is down for 10 minutes: every lease and the queued run's deadline lapse.
      clock.set(clock.now() + 10 * 60_000);
      expect(await sweeper.tick()).to.equal(6);
      const gap = log.lines.filter(([, message]) => message === 'sweeper.gap');
      expect(gap).to.have.length(1);
      expect(gap[0]?.[0]).to.equal('warn');
      expect(gap[0]?.[2]).to.include({
        reason: 'control_plane_gap',
        gapMs: 10 * 60_000,
        holdoffMs: 0,
      });
      // Past their deadlines too, so failed; a lapsed lease alone would be re-queued.
      for (const run of [...claimed, queued]) {
        expect((await h.service.get(run.orgId, run.runId))?.status).to.equal(
          'failed',
        );
      }
      expect(await tickFor(clock, sweeper, SWEEP_INTERVAL_MS)).to.equal(0);
      expect(log.lines.filter(([, message]) => message === 'sweeper.gap')).to.have.length(1);
    });

    it('a normal leader handover logs no gap; a DB clock jump does, and sweeping goes on', async () => {
      const clock = contractClock();
      const liveness = new InMemoryControlPlaneLiveness(clock);
      const executions = { sweep: sinon.stub().resolves(0) };
      const log = recording();
      const a = new RunSweeper({ executions, lease: leader(), liveness, logger: log, holderId: 'a' });
      const b = new RunSweeper({ executions, lease: leader(), liveness, logger: log, holderId: 'b' });
      await a.tick();
      clock.set(clock.now() + SWEEPER_LEASE_MS + SWEEP_INTERVAL_MS);
      await b.tick();
      expect(executions.sweep.callCount).to.equal(2);
      expect(log.lines.map(([, message]) => message)).not.to.include('sweeper.gap');

      clock.set(clock.now() + 60 * 60_000);
      await b.tick();
      expect(executions.sweep.callCount).to.equal(3);
      expect(log.lines.map(([, message]) => message)).to.include('sweeper.gap');
    });

    it('an explicit holdoff in the shared record still holds, across a leader change', async () => {
      const clock = contractClock();
      const liveness = new InMemoryControlPlaneLiveness(clock);
      const executions = { sweep: sinon.stub().resolves(0) };
      const a = new RunSweeper({ executions, lease: leader(), liveness, logger: recording(), holderId: 'a' });
      const b = new RunSweeper({ executions, lease: leader(), liveness, logger: recording(), holderId: 'b' });
      await a.tick();
      await liveness.holdOff(clock.now() + 60_000);
      clock.set(clock.now() + SWEEPER_LEASE_MS);
      expect(await b.tick()).to.equal(0);
      expect(
        await tickFor(clock, b, 60_000 - SWEEPER_LEASE_MS - SWEEP_INTERVAL_MS),
      ).to.equal(0);
      expect(executions.sweep.callCount).to.equal(1);
      await tickFor(clock, b, SWEEP_INTERVAL_MS);
      expect(executions.sweep.callCount).to.equal(2);
    });
  });
});
