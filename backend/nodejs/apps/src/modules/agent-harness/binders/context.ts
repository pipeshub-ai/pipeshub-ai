import { Container, interfaces } from 'inversify';
import type { ControlPlaneTransaction } from '../../control-plane';
import { Logger } from '../../../libs/services/logger.service';
import type { AgentHarnessDependencies, HarnessJournal } from '../composition';
import type { HarnessBudgetLedger } from '../../../libs/repositories/agent-harness/budget-ledger';
import type { ChildRunStore } from '../ports/child-runs';
import type { DispatchOutbox } from '../ports/dispatch-outbox';
import type { StoreSession } from '../../../libs/repositories/agent-harness/durable-transaction';
import type { JournalTail } from '../ports/journal-tail';
import type { HarnessAdmissionStore } from '../../../libs/repositories/agent-harness/run-admission-store';
import { RunKeyDigest } from '../domain/run-key-digest';

/**
 * What every binder shares: the stores composition builds once per call, so
 * two features never hold two copies of one store.
 */
export interface HarnessBindingContext {
  readonly container: Container;
  readonly dependencies: AgentHarnessDependencies;
  readonly logger: Logger | undefined;
  readonly journal: HarnessJournal;
  readonly controlPlane: ControlPlaneTransaction;
  readonly dispatches: DispatchOutbox<StoreSession>;
  readonly admissionStore: HarnessAdmissionStore;
  readonly budgets: HarnessBudgetLedger;
  readonly childRuns: ChildRunStore;
  /** What a run stores for its idempotency key: admission, child runs and erasure agree on it. */
  readonly runKeys: RunKeyDigest;
  /** One change stream per process serves every follower of every run. */
  readonly journalTail: () => JournalTail;
}

/**
 * Binds one feature's tokens, keeping any binding made before it (a test
 * double or another module). A new feature or store is one more binder in
 * `AGENT_HARNESS_BINDERS`; none of the others changes.
 */
export type HarnessBinder = (context: HarnessBindingContext) => void;

export const boundOrNull = <T>(
  container: interfaces.Container,
  token: interfaces.ServiceIdentifier<T>,
): T | null => (container.isBound(token) ? container.get<T>(token) : null);
