/** R-8: lease and deadline predicates read "now" from the database, never this replica's clock. */
import { expect } from 'chai';
import mongoose from 'mongoose';
import sinon from 'sinon';
import {
  carriedServerTime,
  fixedMongoTime,
  mongoServerTime,
  SERVER_TIME_CARRY_MS,
} from '../../../src/libs/repositories/agent-harness/time/mongo';

/** `connection.db` is a plain field mongoose sets on connect, absent before: install it for one test. */
const withDb = (db: unknown): void => {
  Object.defineProperty(mongoose.connection, 'db', { configurable: true, get: () => db });
};

describe('agent harness: database time', () => {
  let original: PropertyDescriptor | undefined;
  beforeEach(() => {
    original = Object.getOwnPropertyDescriptor(mongoose.connection, 'db');
  });
  afterEach(() => {
    if (original) Object.defineProperty(mongoose.connection, 'db', original);
    else delete (mongoose.connection as unknown as Record<string, unknown>)['db'];
  });

  it('the server clock is $$NOW inside a statement', () => {
    expect(mongoServerTime.expr()).to.deep.equal({ $toDouble: '$$NOW' });
  });

  it('refuses to read the time without a connection, rather than falling back to the local clock', async () => {
    withDb(undefined);
    const failed = await carriedServerTime().read().then(
      () => null,
      (error: Error) => error,
    );
    expect(failed?.message).to.equal('MongoDB is not connected');
  });

  it("reads the server's own localTime, and refuses an answer without one", async () => {
    const command = sinon.stub().resolves({ localTime: new Date(1_700_000_000_000) });
    withDb({ admin: () => ({ command }) });
    expect(await carriedServerTime().read()).to.equal(1_700_000_000_000);
    expect(command.firstCall.args[0]).to.deep.equal({ hello: 1 });
    command.resolves({ localTime: '2026-10-07' });
    const failed = await carriedServerTime().read().then(
      () => null,
      (error: Error) => error,
    );
    expect(failed?.message).to.equal('MongoDB hello did not report localTime');
  });

  describe('WP88-FU-4/FU-5: one reading carried forward on the monotonic clock', () => {
    let mono = 0;
    const hello = sinon.stub();
    const time = () => carriedServerTime({ monotonicMs: () => mono, hello });
    beforeEach(() => {
      mono = 1_000;
      hello.reset();
      hello.resolves({ localTime: new Date(1_700_000_000_000) });
    });

    it('answers reads within the carry from one hello, moved on by the monotonic clock', async () => {
      const clock = time();
      expect(await clock.read()).to.equal(1_700_000_000_000);
      mono += 1_234.7;
      expect(await clock.read()).to.equal(1_700_000_001_234);
      expect(hello.callCount).to.equal(1);
    });

    it('asks the server again once the carry is over', async () => {
      const clock = time();
      await clock.read();
      mono += SERVER_TIME_CARRY_MS;
      hello.resolves({ localTime: new Date(1_700_000_009_000) });
      expect(await clock.read()).to.equal(1_700_000_009_000);
      expect(hello.callCount).to.equal(2);
    });

    it('readers of one instant share one hello', async () => {
      const clock = time();
      const read = await Promise.all(Array.from({ length: 16 }, () => clock.read()));
      expect(new Set(read)).to.deep.equal(new Set([1_700_000_000_000]));
      expect(hello.callCount).to.equal(1);
    });

    it("a jump of this replica's wall clock moves nothing (R-8)", async () => {
      const clock = time();
      await clock.read();
      const wall = sinon.useFakeTimers({ now: Date.now() + 3_600_000, toFake: ['Date'] });
      try {
        mono += 10;
        expect(await clock.read()).to.equal(1_700_000_000_010);
      } finally {
        wall.restore();
      }
    });

    it('a failed hello is not carried: the next read asks again', async () => {
      const clock = time();
      hello.rejects(new Error('down'));
      const failed = await clock.read().then(() => null, (error: Error) => error);
      expect(failed?.message).to.equal('down');
      hello.resolves({ localTime: new Date(1_700_000_000_000) });
      expect(await clock.read()).to.equal(1_700_000_000_000);
    });
  });

  it('the test clock answers the injected now, inside a statement and outside it', async () => {
    let now = 5;
    const time = fixedMongoTime(() => now);
    expect(time.expr()).to.deep.equal({ $literal: 5 });
    now = 9;
    expect(await time.read()).to.equal(9);
  });
});
