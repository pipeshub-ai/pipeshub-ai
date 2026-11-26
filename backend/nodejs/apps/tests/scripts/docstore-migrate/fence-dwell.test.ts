import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  FenceOpsTimeoutError,
  casMongoFence,
  fenceTuning,
  setMongoFence,
  sleep,
} from '../../../src/scripts/docstore-migrate/fence';
import { fenceCutoverWaitMs } from '../../../src/libs/repositories/fence';

// short client options keep the tests fast: 2 x (100 + 100 + 100) + 2500 ms
const SHORT = {
  waitQueueTimeoutMS: 100,
  connectTimeoutMS: 100,
  serverSelectionTimeoutMS: 100,
  retryWrites: true,
};
const fenceDwellMs = (): number => fenceCutoverWaitMs(SHORT);

type Doc = { _id: string; mode?: string; epoch?: number; at?: Date };

/** One-document stand-in for the fence collection. */
const fakeRt = (doc?: Doc) => {
  let cur: Doc | undefined = doc;
  const col = {
    findOne: async () => cur,
    countDocuments: async () => (cur ? 1 : 0),
    insertOne: async (d: Doc) => void (cur = d),
    replaceOne: async (_f: unknown, d: Doc) => void (cur = d),
    updateOne: async (
      f: { mode: { $in: string[] } },
      u: { $set: Partial<Doc> },
    ) => {
      if (!cur || !f.mode.$in.includes(cur.mode ?? 'active'))
        return { matchedCount: 0 };
      cur = { ...cur, ...u.$set };
      return { matchedCount: 1 };
    },
  };
  return {
    rt: { db: () => ({ collection: () => col }) } as never,
    mode: () => cur?.mode,
  };
};

describe('the fence dwell before readonly or retired', () => {
  beforeEach(() => {
    sinon.stub(fenceTuning, 'fencedOpsRunning').resolves(0);
    sinon.stub(fenceTuning, 'clientOptions').returns(SHORT);
  });

  afterEach(() => {
    sinon.restore();
    delete process.env.DOC_STORE_FENCE_TRUST_MS;
  });

  it('is the visibility window plus what a client can hold a write, from the client options', () => {
    expect(fenceDwellMs()).to.equal(2500 + 600);
    expect(fenceCutoverWaitMs({ ...SHORT, retryWrites: false })).to.equal(
      2500 + 300,
    );
  });

  it('refuses a client with an unbounded wait queue: no time bounds a write that passed the fence', async () => {
    sinon.restore();
    sinon
      .stub(fenceTuning, 'clientOptions')
      .returns({ ...SHORT, waitQueueTimeoutMS: 0 });
    const f = fakeRt({
      _id: 'fence',
      mode: 'draining',
      at: new Date(Date.now() - 10_000_000),
    });
    await casMongoFence(f.rt, ['draining'], 'readonly').then(
      () => expect.fail('moved'),
      (e: Error) => expect(e.message).to.match(/unbounded wait queue/),
    );
    expect(f.mode()).to.equal('draining');
  });

  it('waits while a fenced write is still running, logs it, and moves after two clean polls', async () => {
    const f = fakeRt({
      _id: 'fence',
      mode: 'draining',
      at: new Date(Date.now() - 10_000),
    });
    sinon.stub(sleep, 'ms').resolves();
    const log = sinon.stub(fenceTuning, 'log');
    const ops = fenceTuning.fencedOpsRunning as unknown as sinon.SinonStub;
    ops.reset();
    ops.onCall(0).resolves(2);
    ops.onCall(1).resolves(1);
    ops.onCall(2).resolves(0);
    ops.onCall(3).resolves(0);
    await casMongoFence(f.rt, ['draining'], 'readonly');
    expect(ops.callCount).to.equal(4);
    expect(log.callCount).to.equal(2);
    expect(log.firstCall.args[0]).to.equal('fence_waiting_for_writes');
    expect(f.mode()).to.equal('readonly');
  });

  it('aborts at the operator timeout and leaves the store draining', async () => {
    const f = fakeRt({
      _id: 'fence',
      mode: 'draining',
      at: new Date(Date.now() - 10_000),
    });
    let clockMs = 0;
    sinon.stub(sleep, 'ms').callsFake(async () => void (clockMs += 600));
    const real = Date.now();
    sinon.stub(Date, 'now').callsFake(() => real + clockMs);
    sinon.stub(fenceTuning, 'log');
    (fenceTuning.fencedOpsRunning as unknown as sinon.SinonStub).resolves(1);
    sinon.stub(fenceTuning, 'opsTimeoutMs').value(1500);
    await setMongoFence(f.rt, 'readonly').then(
      () => expect.fail('moved'),
      (e: Error) => expect(e).to.be.instanceOf(FenceOpsTimeoutError),
    );
    expect(f.mode()).to.equal('draining');
  });

  it('a write that reappears between the two polls restarts the count', async () => {
    const f = fakeRt({
      _id: 'fence',
      mode: 'draining',
      at: new Date(Date.now() - 10_000),
    });
    sinon.stub(sleep, 'ms').resolves();
    sinon.stub(fenceTuning, 'log');
    const ops = fenceTuning.fencedOpsRunning as unknown as sinon.SinonStub;
    ops.reset();
    ops.onCall(0).resolves(0);
    ops.onCall(1).resolves(1);
    ops.resolves(0);
    await casMongoFence(f.rt, ['draining'], 'readonly');
    expect(ops.callCount).to.equal(4);
  });

  it('waits out the rest of the dwell, counted from when the store became draining', async () => {
    const f = fakeRt({ _id: 'fence', mode: 'draining', at: new Date() });
    const slept = sinon.stub(sleep, 'ms').resolves();
    expect(await casMongoFence(f.rt, ['draining'], 'readonly')).to.equal(true);
    expect(slept.callCount).to.equal(2); // the rest of the dwell, then the gap between the two clean polls
    expect(slept.secondCall.args[0]).to.equal(fenceTuning.pollMs);
    expect(slept.firstCall.args[0]).to.be.within(
      fenceDwellMs() - 1000,
      fenceDwellMs(),
    );
    expect(f.mode()).to.equal('readonly');
  });

  it('does not wait when the dwell has already passed', async () => {
    const f = fakeRt({
      _id: 'fence',
      mode: 'draining',
      at: new Date(Date.now() - fenceDwellMs() - 1),
    });
    const slept = sinon.stub(sleep, 'ms').resolves();
    await setMongoFence(f.rt, 'retired');
    expect(slept.callCount).to.equal(1); // only the gap between the two clean polls
    expect(slept.firstCall.args[0]).to.equal(fenceTuning.pollMs);
    expect(f.mode()).to.equal('retired');
  });

  it('refuses to skip draining: active to readonly or retired throws and changes nothing', async () => {
    const f = fakeRt({ _id: 'fence', mode: 'active' });
    for (const to of ['readonly', 'retired'] as const) {
      await setMongoFence(f.rt, to).then(
        () => expect.fail(to),
        (e: Error) => expect(e.message).to.match(/must be 'draining'/),
      );
      await casMongoFence(f.rt, ['active', 'draining'], to).then(
        () => expect.fail(to),
        (e: Error) => expect(e.message).to.match(/must be 'draining'/),
      );
    }
    expect(f.mode()).to.equal('active');
    const none = fakeRt();
    await setMongoFence(none.rt, 'readonly').then(
      () => expect.fail('no document'),
      (e: Error) => expect(e.message).to.match(/must be 'draining'/),
    );
  });

  it('moves from readonly to retired without another wait, and into draining or active without any', async () => {
    const slept = sinon.stub(sleep, 'ms').resolves();
    const a = fakeRt({ _id: 'fence', mode: 'readonly', at: new Date() });
    await setMongoFence(a.rt, 'retired');
    const b = fakeRt({ _id: 'fence', mode: 'active' });
    expect(
      await casMongoFence(b.rt, ['active', 'draining'], 'draining'),
    ).to.equal(true);
    expect(await casMongoFence(b.rt, ['draining'], 'active')).to.equal(true);
    expect(slept.called).to.equal(false);
  });
});
