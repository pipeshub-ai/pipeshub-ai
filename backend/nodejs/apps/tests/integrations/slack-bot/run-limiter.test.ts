import { expect } from 'chai';
import {
  RunLimiter,
  runLimiterFromEnv,
} from '../../../src/integrations/slack-bot/src/utils/run-limiter';

const deferred = () => {
  let resolve!: () => void;
  let reject!: (e: Error) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};
const tick = () => new Promise((r) => setImmediate(r));

describe('RunLimiter', () => {
  it('runs at most maxRunning tasks at once', async () => {
    const limiter = new RunLimiter(2, 10);
    const gates = [deferred(), deferred(), deferred()];
    const started: number[] = [];
    const results = gates.map((g, i) =>
      limiter.run(async () => {
        started.push(i);
        await g.promise;
        return i;
      }),
    );
    await tick();
    expect(limiter.running).to.equal(2);
    expect(limiter.waiting).to.equal(1);
    expect(started).to.deep.equal([0, 1]);
    gates[0].resolve();
    await tick();
    expect(started).to.deep.equal([0, 1, 2]);
    gates[1].resolve();
    gates[2].resolve();
    expect(await Promise.all(results)).to.deep.equal([
      { status: 'ran', value: 0 },
      { status: 'ran', value: 1 },
      { status: 'ran', value: 2 },
    ]);
    expect(limiter.running).to.equal(0);
  });

  it('starts waiting tasks in FIFO order', async () => {
    const limiter = new RunLimiter(1, 10);
    const gates = [deferred(), deferred(), deferred(), deferred()];
    const started: number[] = [];
    const results = gates.map((g, i) =>
      limiter.run(async () => {
        started.push(i);
        await g.promise;
      }),
    );
    for (const g of gates) {
      await tick();
      g.resolve();
    }
    await Promise.all(results);
    expect(started).to.deep.equal([0, 1, 2, 3]);
  });

  it('answers busy when the waiting line is full', async () => {
    const limiter = new RunLimiter(1, 1);
    const gate = deferred();
    const first = limiter.run(() => gate.promise);
    const second = limiter.run(async () => 'b');
    let called = false;
    const third = await limiter.run(async () => {
      called = true;
    });
    expect(third).to.deep.equal({ status: 'busy' });
    expect(called).to.equal(false);
    gate.resolve();
    expect(await first).to.deep.equal({ status: 'ran', value: undefined });
    expect(await second).to.deep.equal({ status: 'ran', value: 'b' });
  });

  it('frees the slot when a task throws', async () => {
    const limiter = new RunLimiter(1, 1);
    const gate = deferred();
    const first = limiter.run(() => gate.promise);
    const second = limiter.run(async () => 'next');
    await tick();
    gate.reject(new Error('boom'));
    let caught: Error | undefined;
    try {
      await first;
    } catch (e) {
      caught = e as Error;
    }
    expect(caught?.message).to.equal('boom');
    expect(await second).to.deep.equal({ status: 'ran', value: 'next' });
    expect(limiter.running).to.equal(0);
  });

  it('reads and clamps the env limits', () => {
    const read = (env: NodeJS.ProcessEnv) => {
      const l = runLimiterFromEnv(env) as any;
      return [l.maxRunning, l.maxWaiting];
    };
    expect(read({})).to.deep.equal([50, 500]);
    expect(
      read({ SLACK_BOT_MAX_CONCURRENT_RUNS: '0', SLACK_BOT_MAX_WAITING_RUNS: '99999' }),
    ).to.deep.equal([1, 5000]);
    expect(
      read({ SLACK_BOT_MAX_CONCURRENT_RUNS: 'abc', SLACK_BOT_MAX_WAITING_RUNS: 'abc' }),
    ).to.deep.equal([50, 500]);
  });
});
