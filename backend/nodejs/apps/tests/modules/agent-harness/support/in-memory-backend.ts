/** The in-memory `ContractBackend`: the unit contract suite's and the run models'. */
import { InMemoryExecutionStore } from '../../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryJournalStore, InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { HarnessRun, StoreClock } from '../../../../src/modules/agent-harness/domain/execution';
import { AdmissionQuotas } from '../../../../src/modules/agent-harness/domain/admission-quotas';
import { RunTranscript } from '../../../../src/modules/agent-harness/ports/conversation-writer';
import { InMemoryBudgetLedger } from '../../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { committingTx, InMemoryHeadRevisions } from '../../control-plane/support/head-revisions-contract';
import { testCipher } from './data-protection';
import { ContractBackend } from './execution-store-contract';

/** Plays the conversation module the way the Mongo contract's real writer does. */
export class RecordingTranscripts {
  readonly saved = new Map<string, Array<Record<string, unknown>>>();
  private failNext = false;
  private readonly deleted = new Set<string>();

  failNextWrite(): void {
    this.failNext = true;
  }

  deleteConversation(run: HarnessRun): Promise<void> {
    this.deleted.add(run.sessionId);
    return Promise.resolve();
  }

  sessionExists(run: HarnessRun): Promise<boolean> {
    return Promise.resolve(!this.deleted.has(run.sessionId));
  }

  record(run: HarnessRun, transcript: RunTranscript): Promise<boolean> {
    if (this.failNext) {
      this.failNext = false;
      return Promise.reject(new Error('conversation write failed'));
    }
    if (this.deleted.has(run.sessionId)) return Promise.resolve(false);
    const list = this.saved.get(run.runId) ?? [];
    if (transcript.kind === 'failure') {
      list.push({
        messageType: 'error',
        content: transcript.message,
        ...(transcript.parts !== undefined ? { parts: transcript.parts } : {}),
      });
    } else {
      for (const question of transcript.questions) {
        list.push({ messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: question.toolData }] });
      }
      const completion = transcript.completion;
      list.push({
        messageType: completion['answerMatchType'] === 'Error' ? 'error' : 'bot_response',
        content: completion['answer'],
        confidence: completion['confidence'],
        parts: completion['parts'],
        ...(completion['status'] === 'stopped' ? { status: 'stopped' } : {}),
      });
    }
    this.saved.set(run.runId, list);
    return Promise.resolve(true);
  }

  written(run: HarnessRun): Promise<Array<Record<string, unknown>>> {
    return Promise.resolve(this.saved.get(run.runId) ?? []);
  }
}

export function inMemoryBackend(
  clock: StoreClock,
  options: { quotas?: AdmissionQuotas; budgets?: InMemoryBudgetLedger } = {},
): ContractBackend & { executionStore: InMemoryExecutionStore; journal: InMemoryJournalStore } {
  const transcripts = new RecordingTranscripts();
  const payloads = new InMemoryPayloadStore();
  const store = new InMemoryExecutionStore(
    clock,
    undefined,
    options.quotas,
    transcripts,
    new JournalService(payloads, testCipher()),
    options.budgets,
  );
  const heads = new InMemoryHeadRevisions();
  store.controlPlaneHeads = (orgId) => heads.current(orgId);
  return {
    controlPlane: {
      heads: async (orgId) => heads.current(orgId),
      commit: async (orgId) => void heads.commit(orgId),
      moveDeployment: () => heads.move(committingTx('any')),
    },
    executionStore: store,
    transcripts,
    journal: store.journal,
    payloads,
    deletePayload: async (_orgId, ref) => {
      payloads.objects.delete(ref);
    },
    admissionStore: store,
    childRuns: store,
    ...(options.budgets !== undefined ? { budgets: options.budgets } : {}),
    dispatches: async (orgId, runId) =>
      store.dispatches
        .filter((item) => item.orgId === orgId && item.runId === runId)
        .map(({ commandId, kind }) => ({ commandId, kind })),
  };
}
