import type { PolicyLayer } from '../domain/policy-alias';
import type { StoreTx } from './store-tx';

/**
 * Called for every policy layer a store persists, inside the write's
 * transaction: what derives a published copy from a layer hangs here, at
 * the one place every writer converges (the admin settings, the generic
 * layer editor, the legacy read-through and the migration all end in
 * `PolicyStore.write`).
 */
export type PolicyWriteObserver = (
  tx: StoreTx,
  scope: string,
  layer: PolicyLayer | null,
) => Promise<void>;

const observers = new Map<string, PolicyWriteObserver>();

/** Registers `observer` under `name` for this process (again: replaced). Returns its removal. */
export function observePolicyWrites(
  name: string,
  observer: PolicyWriteObserver,
): () => void {
  observers.set(name, observer);
  return () => {
    if (observers.get(name) === observer) observers.delete(name);
  };
}

/** Whether an observer is registered under `name`. */
export const policyWritesObserved = (name: string): boolean =>
  observers.has(name);

/** Removes every observer: a test process composes many containers, each registering its own. */
export function clearPolicyWriteObservers(): void {
  observers.clear();
}

/** Every persisting store calls this once per layer it writes; a failing observer fails the write. */
export async function policyWritten(
  tx: StoreTx,
  scope: string,
  layer: PolicyLayer | null,
): Promise<void> {
  for (const observer of observers.values()) {
    await observer(tx, scope, layer);
  }
}
