/** The in-memory collaborators of the governed-memory contract: the execution store, its journal and payloads. */
import { InMemoryExecutionStore } from '../../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import type { StoreClock } from '../../../../src/modules/agent-harness/domain/execution';
import type { CollaboratorBackend } from './collaborator-turns-contract';
import { testCipher } from './data-protection';

export const inMemoryCollaborators = (
  clock: StoreClock,
): CollaboratorBackend => {
  const payloads = new InMemoryPayloadStore();
  const store = new InMemoryExecutionStore(
    clock,
    undefined,
    undefined,
    null,
    new JournalService(payloads, testCipher()),
  );
  return {
    executionStore: store,
    journal: store.journal,
    payloads,
    deletePayload: async (_orgId, ref) => {
      payloads.objects.delete(ref);
    },
    admissionStore: store,
    roles: store,
    share: async (orgId, sessionId, userId, accessLevel) => {
      store.shareSession(orgId, sessionId, userId, accessLevel);
    },
    unshare: async (orgId, sessionId, userId) => {
      store.unshareSession(orgId, sessionId, userId);
    },
    dispatches: async (orgId, runId) =>
      store.dispatches
        .filter((item) => item.orgId === orgId && item.runId === runId)
        .map(({ commandId, kind }) => ({ commandId, kind })),
  };
};
