import { expect } from 'chai';
import sinon from 'sinon';
import { AuditAnchorService } from '../../../src/modules/agent-harness/application/audit-anchoring';
import { RunSweeper, SWEEP_INTERVAL_MS } from '../../../src/modules/agent-harness/application/run-sweeper';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { SessionErasureWorker } from '../../../src/modules/agent-harness/application/session-erasure';
import {
  AUDIT_ANCHOR_INTERVAL_MS,
  AUDIT_ANCHOR_LEASE_MS,
  AUDIT_ANCHOR_RENEW_MS,
} from '../../../src/modules/agent-harness/domain/audit';
import { SCHEDULE_TICK_MS } from '../../../src/modules/agent-harness/domain/schedules';
import { ERASURE_TICK_MS } from '../../../src/modules/agent-harness/domain/session-erasure';
import { silentHarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';

interface Loop {
  start(intervalMs?: number): void;
  stop(): Promise<void>;
  tick(): Promise<unknown>;
}

const leaseStub = () => ({
  tryAcquire: sinon.stub().resolves(true),
  release: sinon.stub().resolves(),
});

/**
 * The harness's background loops (erasure, audit anchoring, run sweeping,
 * schedules): each ticks on its interval, a second start keeps one timer,
 * and stop ends the ticking and gives up the leader lease it may hold.
 */
const leased: Array<[string, number, (lease: ReturnType<typeof leaseStub>) => Loop, atStart?: number]> = [
  [
    'SessionErasureWorker',
    ERASURE_TICK_MS,
    (lease) => new SessionErasureWorker({
      jobs: {} as never,
      runner: {} as never,
      lease: lease as never,
      holderId: 'holder-1',
    }) as unknown as Loop,
  ],
  [
    'RunSweeper',
    SWEEP_INTERVAL_MS,
    (lease) => new RunSweeper({
      executions: {} as never,
      lease: lease as never,
      liveness: {} as never,
      logger: silentHarnessLogger,
      holderId: 'holder-1',
    }) as unknown as Loop,
    // OPS1-1-1: runs wait on this leadership, so a starting replica asks for it at once.
    1,
  ],
];

describe('agent-harness background loops', () => {
  let clock: sinon.SinonFakeTimers;

  beforeEach(() => {
    clock = sinon.useFakeTimers();
  });

  afterEach(() => {
    clock.restore();
    sinon.restore();
  });

  for (const [name, interval, create, atStart = 0] of leased) {
    it(`${name}: ticks ${atStart === 1 ? 'at start and ' : ''}once per interval, one timer however often started; stop ends it and releases the lease`, async () => {
      const lease = leaseStub();
      const loop = create(lease);
      const tick = sinon.stub(loop, 'tick').resolves(0);
      loop.start();
      expect(tick.callCount).to.equal(atStart);
      loop.start();
      await clock.tickAsync(interval * 3);
      expect(tick.callCount).to.equal(atStart + 3);
      await loop.stop();
      expect(lease.release.calledOnce).to.equal(true);
      expect(lease.release.firstCall.args[1]).to.equal('holder-1');
      await clock.tickAsync(interval * 3);
      expect(tick.callCount).to.equal(atStart + 3);
    });
  }

  it('AuditAnchorService: renews its lease every minute, anchors on taking it and once per interval; stop ends it and releases the lease', async () => {
    const lease = leaseStub();
    const loop = new AuditAnchorService({
      chain: {} as never,
      sink: {} as never,
      lease: lease as never,
      holderId: 'holder-1',
    });
    const pass = sinon
      .stub(loop as unknown as { anchorPass: () => Promise<number> }, 'anchorPass')
      .resolves(0);
    loop.start();
    loop.start();
    await clock.tickAsync(AUDIT_ANCHOR_RENEW_MS);
    expect(pass.callCount).to.equal(1);
    await clock.tickAsync(AUDIT_ANCHOR_INTERVAL_MS * 2);
    expect(pass.callCount).to.equal(3);
    expect(lease.tryAcquire.callCount).to.equal(
      1 + (AUDIT_ANCHOR_INTERVAL_MS * 2) / AUDIT_ANCHOR_RENEW_MS,
    );
    expect(lease.tryAcquire.firstCall.args[2]).to.equal(AUDIT_ANCHOR_LEASE_MS);
    await loop.stop();
    expect(lease.release.calledOnce).to.equal(true);
    expect(lease.release.firstCall.args[1]).to.equal('holder-1');
    await clock.tickAsync(AUDIT_ANCHOR_INTERVAL_MS * 3);
    expect(pass.callCount).to.equal(3);
  });

  it('AuditAnchorService: a replica that is not the leader never anchors, and anchors at once when it takes over', async () => {
    const lease = leaseStub();
    lease.tryAcquire.resolves(false);
    const loop = new AuditAnchorService({
      chain: {} as never,
      sink: {} as never,
      lease: lease as never,
      holderId: 'holder-2',
    });
    const pass = sinon
      .stub(loop as unknown as { anchorPass: () => Promise<number> }, 'anchorPass')
      .resolves(0);
    loop.start();
    await clock.tickAsync(AUDIT_ANCHOR_INTERVAL_MS * 2);
    expect(pass.callCount).to.equal(0);
    lease.tryAcquire.resolves(true);
    await clock.tickAsync(AUDIT_ANCHOR_RENEW_MS);
    expect(pass.callCount).to.equal(1);
    await loop.stop();
  });

  it('ScheduleFirer: never starts a pass while the last is still running', async () => {
    const firer = new ScheduleFirer({
      store: {} as never,
      admission: {} as never,
      actors: {} as never,
      setting: {} as never,
    });
    let finish: () => void = () => undefined;
    const tick = sinon.stub(firer, 'tick').callsFake(
      () => new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    firer.start();
    firer.start();
    await clock.tickAsync(SCHEDULE_TICK_MS * 3);
    expect(tick.callCount).to.equal(1);
    finish();
    await clock.tickAsync(SCHEDULE_TICK_MS);
    expect(tick.callCount).to.equal(2);
    finish();
    await firer.stop();
    await clock.tickAsync(SCHEDULE_TICK_MS * 3);
    expect(tick.callCount).to.equal(2);
  });
});
