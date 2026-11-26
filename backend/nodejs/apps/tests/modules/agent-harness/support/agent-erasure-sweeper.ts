/**
 * A retention sweeper whose only work is the agent erasure retry (H4, H5):
 * every other part answers nothing, so a tick reports what the retry did.
 * Without `deletions`, the query service knows no agent (every lookup
 * answers `absent`).
 */
import { RetentionSweeper } from '../../../../src/modules/agent-harness/application/retention-sweeper';
import { InMemoryErasureJobStore } from '../../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { InMemoryAgentDeletionLookup } from '../../../../src/modules/agent-harness/adapters/in-memory-agent-erasures';
import {
  AgentDeletionLookup,
  AgentErasureQueue,
} from '../../../../src/modules/agent-harness/ports/agent-erasures';
import { MemoryEraser } from '../../../../src/modules/agent-harness/ports/memory-store';
import { SessionErasureQueue } from '../../../../src/modules/agent-harness/ports/session-erasure';
import { AbandonedForks } from '../../../../src/modules/agent-harness/ports/session-forks';

export interface LoggedLine {
  level: 'debug' | 'info' | 'warn' | 'error';
  message: string;
  meta: Record<string, unknown>;
}

/** A retention sweeper whose only work is erasing abandoned forks (CLEANUP-G4), over the given job queue. */
export function sweeperWithAbandonedForks<Tx>(options: {
  forks: AbandonedForks<Tx>;
  erasures: SessionErasureQueue<Tx>;
  transaction: <T>(work: (tx: Tx) => Promise<T>) => Promise<T>;
  now: number;
}): RetentionSweeper<Tx> {
  return new RetentionSweeper<Tx>({
    store: {
      now: () => Promise.resolve(options.now),
      collectPayloads: () => Promise.resolve(0),
      expireCommandReceipts: () => Promise.resolve(0),
      expireAdmissionReceipts: () => Promise.resolve(0),
      harnessOrgs: () => Promise.resolve([]),
    },
    settings: { all: () => Promise.resolve([]) } as never,
    conversations: {
      idleSince: () => Promise.resolve([]),
      expire: () => Promise.resolve(false),
      awaitingErasure: () => Promise.resolve([]),
    },
    erasures: options.erasures,
    runs: { runsOf: () => Promise.resolve([]) },
    artifacts: null,
    abandonedForks: options.forks,
    transaction: options.transaction,
    lease: { tryAcquire: () => Promise.resolve(true), release: () => Promise.resolve() },
    holderId: 'h',
  });
}

export function sweeperWithAgentErasures(options: {
  queue: AgentErasureQueue;
  memories: Pick<MemoryEraser, 'eraseAgent'>;
  deletions?: AgentDeletionLookup;
  now: number;
}): { sweeper: RetentionSweeper<unknown>; logged: LoggedLine[] } {
  const logged: LoggedLine[] = [];
  const log =
    (level: LoggedLine['level']) =>
    (message: string, meta: Record<string, unknown> = {}) => {
      logged.push({ level, message, meta });
    };
  const sweeper = new RetentionSweeper<unknown>({
    store: {
      now: () => Promise.resolve(options.now),
      collectPayloads: () => Promise.resolve(0),
      expireCommandReceipts: () => Promise.resolve(0),
      expireAdmissionReceipts: () => Promise.resolve(0),
      harnessOrgs: () => Promise.resolve([]),
    },
    settings: { all: () => Promise.resolve([]) } as never,
    conversations: {
      idleSince: () => Promise.resolve([]),
      expire: () => Promise.resolve(false),
      awaitingErasure: () => Promise.resolve([]),
    },
    erasures: new InMemoryErasureJobStore(() => options.now),
    runs: { runsOf: () => Promise.resolve([]) },
    artifacts: null,
    agentErasures: {
      queue: options.queue,
      memories: options.memories,
      deletions: options.deletions ?? new InMemoryAgentDeletionLookup(),
    },
    transaction: (work) => work(undefined),
    lease: { tryAcquire: () => Promise.resolve(true), release: () => Promise.resolve() },
    holderId: 'h',
    logger: { debug: log('debug'), info: log('info'), warn: log('warn'), error: log('error') },
  });
  return { sweeper, logged };
}
