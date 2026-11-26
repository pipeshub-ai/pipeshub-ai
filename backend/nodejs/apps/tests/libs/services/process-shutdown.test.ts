import { expect } from 'chai';
import sinon from 'sinon';
import { processShutdown } from '../../../src/libs/services/process-shutdown';

const DEADLINE_MS = 20_000;

const rig = (stop: () => Promise<void>) => {
  const exits: number[] = [];
  const lines: string[] = [];
  const logger = {
    info: (message: string) => lines.push(message),
    error: (message: string) => lines.push(message),
  };
  const shutdown = processShutdown({
    stop,
    logger,
    exit: (code) => exits.push(code),
    deadlineMs: DEADLINE_MS,
  });
  return { shutdown, exits, lines };
};

const turn = () => new Promise((resolve) => setImmediate(resolve));

describe('processShutdown (OPS2)', () => {
  let clock: sinon.SinonFakeTimers;

  beforeEach(() => {
    clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  });

  afterEach(() => {
    clock.restore();
  });

  it('OPS2-3: stops once however many signals and faults arrive, and logs the fault once', async () => {
    let release!: () => void;
    const stop = sinon.stub().returns(
      new Promise<void>((resolve) => {
        release = resolve;
      }),
    );
    const { shutdown, exits, lines } = rig(stop);

    shutdown.fault('uncaughtException', new Error('ENOSPC: no space left on device, write'));
    for (let i = 0; i < 1000; i += 1) {
      shutdown.fault('uncaughtException', new Error('ENOSPC: no space left on device, write'));
      shutdown.signal('SIGTERM');
    }
    release();
    await turn();

    expect(stop.callCount).to.equal(1);
    expect(lines.filter((l) => l.startsWith('uncaughtException'))).to.have.length(1);
    expect(exits).to.deep.equal([1]);
  });

  it('OPS2-4: an operator signal exits 0', async () => {
    const { shutdown, exits } = rig(() => Promise.resolve());
    shutdown.signal('SIGTERM');
    await turn();
    expect(exits).to.deep.equal([0]);
  });

  it('OPS2-4: a fault exits non-zero', async () => {
    const { shutdown, exits } = rig(() => Promise.resolve());
    shutdown.fault('uncaughtException', new Error('ENOSPC'));
    await turn();
    expect(exits).to.deep.equal([1]);
  });

  it('OPS2-4: a fault exits non-zero also when it arrives during an operator stop', async () => {
    let release!: () => void;
    const { shutdown, exits } = rig(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    shutdown.signal('SIGTERM');
    shutdown.fault('uncaughtException', new Error('boom'));
    release();
    await turn();
    expect(exits).to.deep.equal([1]);
  });

  it('OPS2-4: a stop that fails exits non-zero', async () => {
    const { shutdown, exits } = rig(() => Promise.reject(new Error('dispose failed')));
    shutdown.signal('SIGINT');
    await turn();
    expect(exits).to.deep.equal([1]);
  });

  it('OPS2-5: a stop that never finishes still ends the process, non-zero, at the deadline', async () => {
    const { shutdown, exits } = rig(() => new Promise<void>(() => undefined));
    shutdown.signal('SIGTERM');
    clock.tick(DEADLINE_MS - 1);
    expect(exits).to.deep.equal([]);
    clock.tick(1);
    expect(exits).to.deep.equal([1]);
  });
});
