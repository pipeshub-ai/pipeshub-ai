import { getConversationRepository } from '../../conversation';
import { EMPTY_LEDGER } from '../../../../modules/agent-harness/domain/session-import';
import type { Tx } from '../../uow';
import type { HarnessSessionView, HarnessVersion } from './mongo';

/** The agent harness's own fields of a chat session on PostgreSQL: columns of the conversation repository's session row. */
export async function pgHarnessSessionView(
  sessionId: string,
  orgId: string,
  tx?: Tx,
): Promise<HarnessSessionView> {
  const row = await getConversationRepository().findHarnessSession(
    sessionId,
    orgId,
    {},
    tx,
  );
  return {
    harnessVersion: row?.harnessVersion,
    metadata: row?.session.metadata,
  };
}

export async function pgPinForkExecutor(
  forkId: string,
  orgId: string,
  sourceVersion: HarnessVersion | undefined,
  tx?: Tx,
): Promise<void> {
  if (sourceVersion !== 'durable-v1') return;
  await getConversationRepository().setHarnessFields(
    forkId,
    orgId,
    { harnessVersion: 'durable-v1', harnessLedger: { ...EMPTY_LEDGER } },
    tx,
  );
}

export async function pgMarkErasureRequested(
  sessionId: string,
  orgId: string,
  at: number,
  tx?: Tx,
): Promise<void> {
  await getConversationRepository().setHarnessFields(
    sessionId,
    orgId,
    { erasureRequestedAt: at },
    tx,
  );
}
