/**
 * Contract every UnitOfWork implementation must meet. Store-neutral: the store
 * under test supplies a fixture with a `put` that writes through the given `tx`.
 * Aggregate repositories follow the same shape (`describeXContract(fixture)`),
 * one `*.itest.ts` per store runs it against a real server.
 */
import { expect } from 'chai';
import { UniqueConstraintError } from '../../../src/libs/repositories/errors';
import type { Tx, UnitOfWork } from '../../../src/libs/repositories/uow';

export interface UnitOfWorkFixture {
  uow: UnitOfWork;
  /** True when a failed run rolls back (replica set); false on the standalone path. */
  transactional: boolean;
  /** Inserts a row with a unique `key`. Rejects with the store's raw duplicate error. */
  put(tx: Tx, key: string): Promise<void>;
  has(key: string): Promise<boolean>;
}

export function describeUnitOfWorkContract(fx: UnitOfWorkFixture): void {
  let n = 0;
  const key = (): string => `uow-${process.pid}-${Date.now()}-${n++}`;

  it('returns the callback result and keeps its writes', async () => {
    const k = key();
    const out = await fx.uow.run(async (tx) => {
      await fx.put(tx, k);
      return 42;
    });
    expect(out).to.equal(42);
    expect(await fx.has(k)).to.equal(true);
  });

  it('rethrows the callback error unchanged', async () => {
    const boom = new Error('boom');
    let caught: unknown;
    try {
      await fx.uow.run(async () => {
        throw boom;
      });
    } catch (e) {
      caught = e;
    }
    expect(caught).to.equal(boom);
  });

  it(
    fx.transactional
      ? 'rolls back every write when the callback fails part-way'
      : 'applies writes one by one (no rollback) when the store has no transactions',
    async () => {
      const [a, b] = [key(), key()];
      try {
        await fx.uow.run(async (tx) => {
          await fx.put(tx, a);
          await fx.put(tx, b);
          throw new Error('fail after two writes');
        });
      } catch {
        /* expected */
      }
      expect([await fx.has(a), await fx.has(b)]).to.deep.equal(
        fx.transactional ? [false, false] : [true, true],
      );
    },
  );

  it('maps a duplicate key to UniqueConstraintError{key} without leaking the value', async () => {
    const k = key();
    await fx.uow.run((tx) => fx.put(tx, k));
    let caught: unknown;
    try {
      await fx.uow.run((tx) => fx.put(tx, k));
    } catch (e) {
      caught = e;
    }
    expect(caught).to.be.instanceOf(UniqueConstraintError);
    const err = caught as UniqueConstraintError;
    expect(err.statusCode).to.equal(409);
    expect(err.metadata).to.deep.equal({ key: 'key' });
    expect(JSON.stringify(err.toJSON())).to.not.contain(k);
  });
}
