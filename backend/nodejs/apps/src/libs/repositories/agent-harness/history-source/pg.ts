import { NotFoundError } from '../../../errors/http.errors';
import {
  getConversationRepository,
  type HarnessSessionRow,
  type MessageDto,
  type NewMessage,
  type RunStateChange,
} from '../../conversation';
import type { Tx } from '../../uow';
import type { IMessage } from '../../../../modules/enterprise_search/types/conversation.interfaces';
import type { ConversationRef } from '../../../../modules/agent-harness/ports/conversation-writer';
import type { HistorySource } from '../../../../modules/agent-harness/ports/history-source';
import type {
  AccountedConversation,
  ConversationHistory,
} from '../../../../modules/agent-harness/domain/session-import';
import { accountedLedger, asHistory } from './mongo';

/** What the harness accounts for in a session read with its harness fields. */
export const accountedOfRow = (
  row: HarnessSessionRow | null,
): AccountedConversation => ({
  lastSeq: row?.nextSeq ?? 0,
  ledger: row?.harnessLedger ?? null,
});

/**
 * The session `ref` names, locked to the end of `tx` and accounted, when `match` admits it: the check holds for the
 * writes the transaction makes after it (READ COMMITTED reads the row as it stands under the lock). Null otherwise.
 */
export async function lockAccounted(
  ref: ConversationRef,
  tx: Tx,
  match: (row: HarnessSessionRow) => boolean = () => true,
): Promise<AccountedConversation | null> {
  const row = await getConversationRepository().findHarnessSession(
    ref.sessionId,
    ref.orgId,
    { forUpdate: true },
    tx,
  );
  return row !== null && match(row) ? accountedOfRow(row) : null;
}

/**
 * Appends `messages` to the session (already locked or bound in `tx`, as `accounted` says it stood) and applies
 * `state` to it, accounted in the session's ledger as the MongoDB writer accounts it (`appendAccounted`): extended
 * over the messages when nothing else wrote since the harness last did, else marked diverged; a session with no
 * ledger is left for its next admission to re-import. The messages' seq follow the session's counter, allocated
 * under its row lock.
 */
export async function appendAccountedPg(
  ref: ConversationRef,
  tx: Tx,
  accounted: AccountedConversation,
  messages: ReadonlyArray<IMessage | NewMessage>,
  state?: RunStateChange,
): Promise<MessageDto[]> {
  const repo = getConversationRepository();
  const stored = await repo.appendMessages(
    ref.sessionId,
    ref.orgId,
    messages as NewMessage[],
    tx,
  );
  const ledger = accounted.ledger
    ? accountedLedger(
        accounted.ledger,
        accounted.lastSeq,
        // As a read of them gives them: the ledger digests what a later import reads.
        stored.map((message) =>
          asHistory(message as unknown as IMessage & { seq: number }),
        ),
      )
    : null;
  if (state !== undefined) {
    const saved = await repo.recordRunState(ref.sessionId, state, tx);
    if (saved === null) {
      throw new NotFoundError('Chat session not found while saving the run');
    }
  }
  if (ledger !== null) {
    await repo.setHarnessFields(
      ref.sessionId,
      ref.orgId,
      { harnessLedger: ledger },
      tx,
    );
  }
  return stored;
}

/** The conversation as history on PostgreSQL, read through the conversation repository in the admission's unit. */
export class PgHarnessHistorySource implements HistorySource<Tx> {
  async read(
    ref: ConversationRef,
    tx: Tx,
    accounted?: AccountedConversation,
  ): Promise<ConversationHistory> {
    const repo = getConversationRepository();
    const known =
      accounted ??
      accountedOfRow(
        await repo.findHarnessSession(ref.sessionId, ref.orgId, {}, tx),
      );
    // UX2: the labels say what the imported history read; only the import's label is derived from them.
    const rows = await repo.listMessages(
      ref.sessionId,
      { orgId: ref.orgId, withLabels: true },
      tx,
    );
    return {
      messages: rows.map((row) =>
        asHistory(row.dto as unknown as IMessage & { seq: number }),
      ),
      ...known,
    };
  }
}
