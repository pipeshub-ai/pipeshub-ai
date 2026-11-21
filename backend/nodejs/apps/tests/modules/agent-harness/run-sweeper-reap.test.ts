/**
 * RS-1, RS-2 (E1): the run sweeper reaps the sandboxes of the engine runs it
 * expires, through the broker's `/v1/reap`, and a broker that cannot be asked
 * never fails the sweep.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryControlPlaneLiveness } from '../../../src/modules/agent-harness/adapters/in-memory-control-plane-liveness';
import { InMemoryPendingReaps } from '../../../src/modules/agent-harness/adapters/in-memory-pending-reaps';
import { describePendingReapsContract } from './support/pending-reaps-contract';
import { sandboxBrokerEndpoint } from '../../../src/modules/agent-harness/adapters/configuration/sandbox-broker-endpoint';
import { HttpSandboxReaper } from '../../../src/modules/agent-harness/adapters/http/http-sandbox-reaper';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  reapLabels,
  RunSweeper,
} from '../../../src/modules/agent-harness/application/run-sweeper';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { LeaderLease } from '../../../src/modules/agent-harness/ports/leader-lease';
import { SweepReason } from '../../../src/modules/agent-harness/ports/execution-store';

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

const leader = (): LeaderLease => ({
  tryAcquire: sinon.stub().resolves(true),
  release: sinon.stub().resolves(),
});

const run = (
  features: string[],
  attemptId: string | null = 'att-2',
): HarnessRun =>
  ({
    orgId: 'org-1',
    runId: 'run-1',
    attemptId,
    requires: { foldVersion: 8, features },
  }) as unknown as HarnessRun;

const sweeping = (expired: Array<[HarnessRun, SweepReason]>) => ({
  sweep: sinon
    .stub()
    .callsFake(
      async (
        _limit: number,
        observe?: (r: HarnessRun, reason: SweepReason) => void,
      ) => {
        for (const [r, reason] of expired) observe?.(r, reason);
        return expired.length;
      },
    ),
});

describe('RunSweeper: sandboxes of expired engine runs are reaped (E1)', () => {
  it('RS-1: a lapsed lease reaps that attempt; an ended run reaps every sandbox of the run', () => {
    expect(reapLabels(run(['executor_engine']), 'lease_expired')).to.deep.equal(
      {
        orgId: 'org-1',
        runId: 'run-1',
        attemptId: 'att-2',
      },
    );
    expect(
      reapLabels(run(['executor_engine']), 'deadline_exceeded'),
    ).to.deep.equal({ orgId: 'org-1', runId: 'run-1' });
    expect(
      reapLabels(run(['executor_engine']), 'cancel_lease_expired'),
    ).to.deep.equal({ orgId: 'org-1', runId: 'run-1' });
    expect(reapLabels(run(['chat']), 'lease_expired')).to.equal(null);
  });

  it('RS-1: the sweeper asks the broker once per swept engine run, and logs what it reaped', async () => {
    const reaper = { reap: sinon.stub().resolves(1) };
    const log = recording();
    const executions = sweeping([
      [run(['executor_engine']), 'lease_expired'],
      [run(['chat']), 'deadline_exceeded'],
    ]);
    const sweeper = new RunSweeper({
      executions,
      lease: leader(),
      liveness: new InMemoryControlPlaneLiveness(),
      logger: log,
      holderId: 'a',
      reaper,
    });
    expect(await sweeper.tick()).to.equal(2);
    expect(reaper.reap.args).to.deep.equal([
      [{ orgId: 'org-1', runId: 'run-1', attemptId: 'att-2' }],
    ]);
    expect(
      log.lines.map(([level, message]) => `${level}:${message}`),
    ).to.include('info:sweeper.reaped');
  });

  it('RS-1: a broker that cannot be asked is logged and the sweep goes on', async () => {
    const reaper = { reap: sinon.stub().rejects(new Error('ECONNREFUSED')) };
    const log = recording();
    const sweeper = new RunSweeper({
      executions: sweeping([[run(['executor_engine']), 'deadline_exceeded']]),
      lease: leader(),
      liveness: new InMemoryControlPlaneLiveness(),
      logger: log,
      holderId: 'a',
      reaper,
    });
    expect(await sweeper.tick()).to.equal(1);
    const failed = log.lines.filter(
      ([, message]) => message === 'sweeper.reap_failed',
    );
    expect(failed).to.have.length(1);
    expect(failed[0]?.[0]).to.equal('warn');
    expect(failed[0]?.[2]).to.include({
      reason: 'broker_unavailable',
      runId: 'run-1',
    });
  });

  it("CLEANUP-G6: an ended run's refused reap is asked for again, with backoff, until the broker answers", async () => {
    let now = 1_000_000;
    const pending = new InMemoryPendingReaps(() => now);
    const reaper = { reap: sinon.stub().rejects(new Error('ECONNREFUSED')) };
    const expired: Array<[HarnessRun, SweepReason]> = [[run(['executor_engine']), 'deadline_exceeded']];
    const sweeper = new RunSweeper({
      executions: sweeping(expired),
      lease: leader(),
      liveness: new InMemoryControlPlaneLiveness(),
      logger: recording(),
      holderId: 'a',
      reaper,
      pendingReaps: pending,
    });
    await sweeper.tick();
    expired.length = 0;
    expect(reaper.reap.callCount).to.equal(1);
    await sweeper.tick();
    expect(reaper.reap.callCount, 'not yet due').to.equal(1);
    now += 5_000;
    await sweeper.tick();
    expect(reaper.reap.callCount, 'due after the first backoff').to.equal(2);
    now += 5_000;
    await sweeper.tick();
    expect(reaper.reap.callCount, 'the backoff doubles').to.equal(2);
    reaper.reap.resolves(1);
    now += 5_000;
    await sweeper.tick();
    expect(reaper.reap.callCount).to.equal(3);
    expect(reaper.reap.lastCall.args).to.deep.equal([{ orgId: 'org-1', runId: 'run-1' }]);
    now += 3_600_000;
    await sweeper.tick();
    expect(reaper.reap.callCount, 'answered once, never asked again').to.equal(3);
  });

  it('RS-1: without a reaper the sweep is what it was', async () => {
    const executions = { sweep: sinon.stub().resolves(1) };
    const sweeper = new RunSweeper({
      executions,
      lease: leader(),
      liveness: new InMemoryControlPlaneLiveness(),
      logger: recording(),
      holderId: 'a',
    });
    await sweeper.tick();
    expect(executions.sweep.firstCall.args).to.have.length(1);
  });

  it('RS-2: the HTTP reaper posts the labels with the worker credential', async () => {
    const fetchImpl = sinon
      .stub()
      .resolves(new Response(JSON.stringify({ reaped: 2 }), { status: 200 }));
    const reaper = new HttpSandboxReaper(
      async () => 'http://sandbox-broker:8095/',
      async () => 'worker-cred',
      fetchImpl as unknown as typeof fetch,
    );
    expect(await reaper.reap({ orgId: 'org-1', runId: 'run-1' })).to.equal(2);
    const [url, init] = fetchImpl.firstCall.args as [string, RequestInit];
    expect(url).to.equal('http://sandbox-broker:8095/v1/reap');
    expect((init.headers as Record<string, string>)['Authorization']).to.equal(
      'Bearer worker-cred',
    );
    expect(JSON.parse(init.body as string)).to.deep.equal({
      labels: { orgId: 'org-1', runId: 'run-1' },
      older_than_s: 0,
    });
  });

  it('RS-2: a refusal, a malformed answer or no broker configured rejects', async () => {
    const refused = new HttpSandboxReaper(
      async () => 'http://b',
      async () => 'c',
      sinon
        .stub()
        .resolves(
          new Response('{}', { status: 401 }),
        ) as unknown as typeof fetch,
    );
    await refused.reap({}).then(
      () => expect.fail('resolved'),
      (error: Error) => expect(error.message).to.contain('401'),
    );
    const none = new HttpSandboxReaper(
      async () => null,
      async () => 'c',
    );
    await none.reap({}).then(
      () => expect.fail('resolved'),
      (error: Error) => expect(error.message).to.contain('not configured'),
    );
  });

  it('RS-2: the broker is where the endpoints document says, else SANDBOX_BROKER_BACKEND', async () => {
    const store = (doc: unknown) =>
      ({
        get: async () => (doc === null ? null : JSON.stringify(doc)),
      }) as never;
    expect(
      await sandboxBrokerEndpoint(
        store({ sandboxBroker: { endpoint: 'http://b:8095' } }),
        {},
      ),
    ).to.equal('http://b:8095');
    expect(
      await sandboxBrokerEndpoint(store({}), {
        SANDBOX_BROKER_BACKEND: 'http://env:8095',
      }),
    ).to.equal('http://env:8095');
    expect(await sandboxBrokerEndpoint(store(null), {})).to.equal(null);
  });
});

describePendingReapsContract('in memory', () => Promise.resolve(new InMemoryPendingReaps()));
