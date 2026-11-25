/**
 * A policy-write observer is process-wide (production composes one
 * container). A test that composes the app may register one over a store it
 * never connects, and every later policy write in the same worker would fail
 * on it; so each test starts with none. Loaded by `.mocharc.yaml`.
 */
import { clearPolicyWriteObservers } from '../../src/modules/control-plane/ports/policy-write-observers';

export const mochaHooks = {
  beforeEach(): void {
    clearPolicyWriteObservers();
  },
};
