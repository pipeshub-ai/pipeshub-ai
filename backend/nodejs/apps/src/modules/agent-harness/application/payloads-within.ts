import {
  PayloadStore,
  TransactionalPayloadStore,
} from '../ports/payload-store';

const writesInTransaction = (
  store: PayloadStore,
): store is PayloadStore & TransactionalPayloadStore<unknown> =>
  'putIn' in store;

/**
 * `store` as seen from inside `tx`: its writes join the transaction when it
 * can, and stay outside it (a blob store) when it cannot. Without `tx`, the
 * store itself.
 */
export function payloadsWithin(store: PayloadStore, tx: unknown): PayloadStore {
  if (tx === undefined || !writesInTransaction(store)) return store;
  return {
    put: (orgId, hash, bytes, owner) =>
      store.putIn(tx, orgId, hash, bytes, owner),
    get: (orgId, ref) => store.get(orgId, ref),
  };
}
