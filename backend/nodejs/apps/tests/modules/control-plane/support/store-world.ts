/**
 * What every store contract is given: the store, its round-trip probe and a
 * control-plane transaction its writes can join (the in-memory one here; the
 * Mongo transaction on the replica set for the Mongo adapters).
 */
import { expect } from 'chai';
import type { ControlPlaneTransaction, CpTx } from '../../../../src/modules/control-plane';
import { ControlPlaneRevisionConflictError } from '../../../../src/modules/control-plane';
import { auditedControlPlane } from './in-memory';
import { ORG_A, rejection, storeChange } from './cp-objects';

export interface StoreWorld<S> {
  readonly store: S;
  /** Reads the store has made so far (one per round trip to its database). */
  roundTrips(): number;
  readonly transaction: ControlPlaneTransaction;
  /** The org's committed `config.changed` entries. */
  configEntries(orgId: string): Promise<number>;
}

export function inMemoryWorld<S>(store: S & { queries: number }): StoreWorld<S> {
  const controlPlane = auditedControlPlane();
  return {
    store,
    roundTrips: () => store.queries,
    transaction: controlPlane.transaction,
    configEntries: async (orgId) => controlPlane.configEntries(orgId).length,
  };
}

const ctx = { orgId: ORG_A, actor: 'user:u-admin' };

/** Runs `write` in a committed control-plane write. */
export async function committed<S>(world: StoreWorld<S>, write: (tx: CpTx) => Promise<unknown>): Promise<void> {
  await world.transaction.run(ctx, async (tx) => {
    await write(tx);
    return { result: null, change: storeChange('store-object', 'x') };
  });
}

/** Runs `write` in a control-plane write that then fails, so it rolls back. */
export async function rolledBack<S>(world: StoreWorld<S>, write: (tx: CpTx) => Promise<unknown>): Promise<void> {
  const boom = new Error('refused after the store write');
  const failed = await rejection(
    world.transaction.run(ctx, async (tx) => {
      await write(tx);
      throw boom;
    }),
  );
  expect(failed).to.equal(boom);
}

export async function expectConflict(write: Promise<unknown>, current: number): Promise<void> {
  const error = await rejection(write);
  expect(error).to.be.instanceOf(ControlPlaneRevisionConflictError);
  expect((error as ControlPlaneRevisionConflictError).current).to.equal(String(current));
  expect((error as ControlPlaneRevisionConflictError).code).to.equal('CP_REVISION_CONFLICT');
}

export async function expectRefused(write: Promise<unknown>, type: abstract new (...args: never[]) => Error): Promise<void> {
  expect(await rejection(write)).to.be.instanceOf(type);
}
