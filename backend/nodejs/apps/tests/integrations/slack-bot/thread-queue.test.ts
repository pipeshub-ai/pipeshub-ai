import { expect } from 'chai';
import sinon from 'sinon';
import {
  MemoryThreadQueueStore,
  QueuedSlackMessage,
  ThreadQueue,
  ThreadQueueStore,
  threadQueueKey,
} from '../../../src/integrations/slack-bot/src/utils/thread-queue';
import { RunLimiter } from '../../../src/integrations/slack-bot/src/utils/run-limiter';

const deferred = () => {
  let resolve!: () => void;
  let reject!: (e: Error) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};
const tick = async (n = 5) => {
  for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r));
};

const msg = (ts: string, user = 'U1'): QueuedSlackMessage => ({
  kind: 'mention',
  ts,
  threadTs: '1.0',
  channel: 'C1',
  user,
  text: `text ${ts}`,
  files: [],
  receivedAt: 0,
});

/** A run whose every call blocks until released, recording each batch's ts list. */
const gatedRun = () => {
  const batches: string[][] = [];
  const gates: Array<ReturnType<typeof deferred>> = [];
  const run = async (batch: QueuedSlackMessage[]) => {
    batches.push(batch.map((m) => m.ts));
    const g = deferred();
    gates.push(g);
    await g.promise;
  };
  const gate = (i: number) => {
    const g = gates[i];
    if (!g) throw new Error(`run ${i} has not started`);
    return g;
  };
  return { run, batches, gates, gate };
};

describe('slack-bot/utils/thread-queue', () => {
  let warn: sinon.SinonStub;
  let error: sinon.SinonStub;
  beforeEach(() => {
    warn = sinon.stub(console, 'warn');
    error = sinon.stub(console, 'error');
  });
  afterEach(() => sinon.restore());

  const KEY = 'slackq:{b:C1:1.0:U1}';
  const make = (store: ThreadQueueStore = new MemoryThreadQueueStore()) =>
    new ThreadQueue(store, new RunLimiter(50, 500), { owner: 'pod:1' });

  it('runs the first message at once', async () => {
    const q = make();
    const r = gatedRun();
    const p = q.submit(KEY, msg('1'), r.run);
    await tick();
    expect(r.batches).to.deep.equal([['1']]);
    r.gate(0).resolve();
    expect(await p).to.equal('ran');
  });

  it('queues a second message while the first runs and answers it in one batch after', async () => {
    const q = make();
    const r = gatedRun();
    const p1 = q.submit(KEY, msg('1'), r.run);
    await tick();
    expect(await q.submit(KEY, msg('2'), r.run)).to.equal('queued');
    expect(await q.submit(KEY, msg('3'), r.run)).to.equal('queued');
    r.gate(0).resolve();
    await tick();
    expect(r.batches).to.deep.equal([['1'], ['2', '3']]);
    r.gate(1).resolve();
    expect(await p1).to.equal('ran');
    expect(r.batches).to.have.length(2);
  });

  it('refuses the eleventh pending message', async () => {
    const q = make();
    const r = gatedRun();
    const p1 = q.submit(KEY, msg('0'), r.run);
    await tick();
    const ten = Array.from({ length: 10 }, (_, i) => String(i + 1));
    for (const ts of ten) expect(await q.submit(KEY, msg(ts), r.run)).to.equal('queued');
    expect(await q.submit(KEY, msg('11'), r.run)).to.equal('full');
    r.gate(0).resolve();
    await tick();
    expect(r.batches[1]).to.deep.equal(ten);
    r.gate(1).resolve();
    expect(await p1).to.equal('ran');
  });

  it('keeps people apart', async () => {
    const q = make();
    const r = gatedRun();
    const k1 = threadQueueKey({ botId: 'b', channel: 'C1', threadTs: '1.0', user: 'U1' });
    const k2 = threadQueueKey({ botId: 'b', channel: 'C1', threadTs: '1.0', user: 'U2' });
    const p1 = q.submit(k1, msg('1', 'U1'), r.run);
    const p2 = q.submit(k2, msg('2', 'U2'), r.run);
    await tick();
    expect(r.batches).to.deep.equal([['1'], ['2']]);
    r.gates.forEach((g) => g.resolve());
    expect(await Promise.all([p1, p2])).to.deep.equal(['ran', 'ran']);
  });

  it('drains after a run that throws', async () => {
    const q = make();
    const r = gatedRun();
    const p1 = q.submit(KEY, msg('1'), r.run);
    await tick();
    await q.submit(KEY, msg('2'), r.run);
    r.gate(0).reject(new Error('boom'));
    await tick();
    expect(r.batches).to.deep.equal([['1'], ['2']]);
    r.gate(1).resolve();
    expect(await p1).to.equal('ran');
    expect(error.called).to.equal(true);
  });

  it('never strands a message submitted during hand-off', async () => {
    const store = new MemoryThreadQueueStore();
    const q = make(store);
    const r = gatedRun();
    const gap = deferred();
    let inGap!: () => void;
    const reachedGap = new Promise<void>((res) => (inGap = res));
    const take = store.takePendingOrRelease.bind(store);
    let calls = 0;
    sinon.stub(store, 'takePendingOrRelease').callsFake(async (k, o) => {
      // The first take hands the first batch over; yield on the release after it.
      if (++calls === 2) {
        inGap();
        await gap.promise;
      }
      return take(k, o);
    });
    const p1 = q.submit(KEY, msg('1'), r.run);
    await tick();
    r.gate(0).resolve();
    await reachedGap;
    const p2 = q.submit(KEY, msg('2'), r.run);
    await tick();
    gap.resolve();
    let settled = false;
    const all = Promise.all([p1, p2]).finally(() => (settled = true));
    while (!settled) {
      r.gates.forEach((g) => g.resolve());
      await tick();
    }
    // The lock is still held during the gap, so '2' queues and the owner's release finds it.
    expect(await all).to.deep.equal(['ran', 'queued']);
    expect(r.batches).to.deep.equal([['1'], ['2']]);
  });

  it('takes over an expired lock', async () => {
    let now = 1_000;
    const store = new MemoryThreadQueueStore(() => now);
    const limiter = new RunLimiter(50, 500);
    const stalled = new ThreadQueue(store, limiter, { owner: 'pod:a' });
    sinon.stub(store, 'renew').resolves(true); // renewal never reaches the store
    const r = gatedRun();
    const stalledDone = stalled.submit(KEY, msg('1'), r.run);
    await tick();
    const fresh = new ThreadQueue(store, limiter, { owner: 'pod:b' });
    expect(await fresh.submit(KEY, msg('2'), r.run)).to.equal('queued');
    expect(await fresh.submit(KEY, msg('3'), r.run)).to.equal('queued');
    now += 120_001;
    const p = fresh.submit(KEY, msg('4'), r.run);
    await tick();
    expect(r.batches[1]).to.deep.equal(['2', '3', '4']);
    r.gate(1).resolve();
    expect(await p).to.equal('ran');
    r.gate(0).resolve();
    expect(await stalledDone).to.equal('ran');
    expect(r.batches).to.have.length(2);
    expect(store.size).to.equal(0);
  });

  it('stops a stalled answer from draining after the same pod takes the thread over', async () => {
    let now = 1_000;
    const store = new MemoryThreadQueueStore(() => now);
    const acquire = sinon.spy(store, 'acquireOrEnqueue');
    const q = make(store); // renews never fire here: the 30 s timer is not reached, as in an outage
    const r = gatedRun();
    const p1 = q.submit(KEY, msg('1'), r.run);
    await tick();
    now += 120_001; // the first answer's lock expires
    const p2 = q.submit(KEY, msg('2'), r.run);
    await tick();
    expect(r.batches).to.deep.equal([['1'], ['2']]);
    const [first, second] = acquire.getCalls().map((c) => c.args[1]);
    expect(first).to.not.equal(second);
    expect(await store.renew(KEY, first!)).to.equal(false);
    expect(await q.submit(KEY, msg('3'), r.run)).to.equal('queued');
    r.gate(0).resolve();
    expect(await p1).to.equal('ran');
    // The stalled loop stopped; '3' waits for the current owner, not a second answer.
    expect(r.batches).to.have.length(2);
    r.gate(1).resolve();
    await tick();
    expect(r.batches[2]).to.deep.equal(['3']);
    r.gate(2).resolve();
    expect(await p2).to.equal('ran');
    expect(store.size).to.equal(0);
  });

  it('answers a message queued twice once', async () => {
    const q = make();
    const r = gatedRun();
    const p1 = q.submit(KEY, msg('1'), r.run);
    await tick();
    await q.submit(KEY, msg('2'), r.run);
    await q.submit(KEY, msg('3'), r.run);
    await q.submit(KEY, msg('2'), r.run); // a Slack retry handled by another pod
    r.gate(0).resolve();
    await tick();
    expect(r.batches[1]).to.deep.equal(['2', '3']);
    r.gate(1).resolve();
    expect(await p1).to.equal('ran');
  });

  it('stops draining when renew reports the lock lost', async () => {
    const clock = sinon.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const store = new MemoryThreadQueueStore();
    sinon.stub(store, 'renew').resolves(false);
    const take = sinon.spy(store, 'takePendingOrRelease');
    const q = make(store);
    const r = gatedRun();
    const p1 = q.submit(KEY, msg('1'), r.run);
    await tick();
    await q.submit(KEY, msg('2'), r.run);
    const takesBefore = take.callCount;
    clock.tick(30_000);
    await tick();
    r.gate(0).resolve();
    expect(await p1).to.equal('ran');
    expect(r.batches).to.deep.equal([['1']]);
    expect(take.callCount).to.equal(takesBefore);
    expect(clock.countTimers()).to.equal(0);
    // The entry stays: renew was stubbed false while the lock is still held, so '2' waits for a new owner.
    expect(store.size).to.equal(1);
  });

  it('answers directly when the store throws', async () => {
    const store = new MemoryThreadQueueStore();
    sinon.stub(store, 'acquireOrEnqueue').rejects(new Error('redis down'));
    const q = make(store);
    const batches: string[][] = [];
    const result = await q.submit(KEY, msg('1'), async (b) => {
      batches.push(b.map((m) => m.ts));
    });
    expect(result).to.equal('direct');
    expect(batches).to.deep.equal([['1']]);
    expect(warn.called).to.equal(true);
  });

  it('answers directly when the first hand-off throws', async () => {
    const store = new MemoryThreadQueueStore();
    sinon.stub(store, 'takePendingOrRelease').rejects(new Error('redis down'));
    const q = make(store);
    const batches: string[][] = [];
    const result = await q.submit(KEY, msg('1'), async (b) => {
      batches.push(b.map((m) => m.ts));
    });
    expect(result).to.equal('direct');
    expect(batches).to.deep.equal([['1']]);
  });

  it('tells the senders of a later batch the limiter refuses', async () => {
    const limiter = new RunLimiter(50, 500);
    const realRun = limiter.run.bind(limiter);
    sinon
      .stub(limiter, 'run')
      .onFirstCall()
      .callsFake(realRun)
      .onSecondCall()
      .resolves({ status: 'busy' });
    const q = new ThreadQueue(new MemoryThreadQueueStore(), limiter, { owner: 'pod:1' });
    const r = gatedRun();
    const onBusy = sinon.spy();
    const p1 = q.submit(KEY, msg('1'), r.run, onBusy);
    await tick();
    await q.submit(KEY, msg('2'), r.run, onBusy);
    await q.submit(KEY, msg('3'), r.run, onBusy);
    r.gate(0).resolve();
    expect(await p1).to.equal('ran');
    expect(r.batches).to.deep.equal([['1']]);
    expect(onBusy.callCount).to.equal(1);
    expect(onBusy.firstCall.args[0].map((m: QueuedSlackMessage) => m.ts)).to.deep.equal(['2', '3']);
  });

  it('tells the sender when the limiter refuses the first batch, and survives a failing notice', async () => {
    const limiter = new RunLimiter(50, 500);
    sinon.stub(limiter, 'run').resolves({ status: 'busy' });
    const q = new ThreadQueue(new MemoryThreadQueueStore(), limiter, { owner: 'pod:1' });
    const onBusy = sinon.stub().rejects(new Error('slack down'));
    expect(await q.submit(KEY, msg('1'), async () => {}, onBusy)).to.equal('busy');
    expect(onBusy.callCount).to.equal(1);
    expect(onBusy.firstCall.args[0].map((m: QueuedSlackMessage) => m.ts)).to.deep.equal(['1']);
  });

  it('returns busy when the limiter refuses the first batch, and still releases', async () => {
    const store = new MemoryThreadQueueStore();
    const q = new ThreadQueue(store, new RunLimiter(1, 0), { owner: 'pod:1' });
    const hold = deferred();
    const other = q.submit('other', msg('x'), () => hold.promise);
    await tick();
    expect(await q.submit(KEY, msg('1'), async () => {})).to.equal('busy');
    expect(store.size).to.equal(1);
    hold.resolve();
    await other;
    expect(store.size).to.equal(0);
  });

  it('leaves nothing behind', async () => {
    const clock = sinon.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const store = new MemoryThreadQueueStore();
    const q = make(store);
    const r = gatedRun();
    const p1 = q.submit(KEY, msg('1'), r.run);
    await tick();
    expect(clock.countTimers()).to.equal(1);
    await q.submit(KEY, msg('2'), r.run);
    r.gate(0).resolve();
    await tick();
    r.gate(1).resolve();
    expect(await p1).to.equal('ran');
    expect(store.size).to.equal(0);
    expect(clock.countTimers()).to.equal(0);
  });

  it('builds the hash-tagged key', () => {
    expect(
      threadQueueKey({ botId: 'b', channel: 'C1', threadTs: '1.2', user: 'U1' }, 'ns:'),
    ).to.equal('ns:slackq:{b:C1:1.2:U1}');
  });
});
