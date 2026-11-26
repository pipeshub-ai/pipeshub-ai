import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession, QueryFilter, Types } from 'mongoose';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../modules/enterprise_search/schema/chat.session.message.schema';
import {
  IChatSessionDocument,
  IChatSessionMessageDocument,
  IMessage,
} from '../../../../modules/enterprise_search/types/conversation.interfaces';
import { transactionWrites } from '../transaction-writes/mongo';
import {
  getMessages,
  historyTurns,
} from '../../../../modules/enterprise_search/utils/utils';
import { MongoTx } from '../../mongo';
import type { NewMessage } from '../../conversation/types';
import { ConversationRef } from '../../../../modules/agent-harness/ports/conversation-writer';
import { HistorySource } from '../../../../modules/agent-harness/ports/history-source';
import {
  AccountedConversation,
  ConversationHistory,
  extendLedger,
  HistoryLedger,
  HistoryMessage,
} from '../../../../modules/agent-harness/domain/session-import';
import {
  joinLabels,
  parseTurnLabel,
  type TurnLabel,
} from '../../../../modules/agent-harness/domain/turn-labels';

/**
 * A message as history: its `previousConversations` entries as they travel
 * (JSON), so the ledger digests what a model is sent.
 */
export const asHistory = (
  message: IMessage & { seq: number },
): HistoryMessage => ({
  seq: message.seq,
  turns: JSON.parse(JSON.stringify(historyTurns(message))) as Array<
    Record<string, unknown>
  >,
  provenance: messageProvenance(message),
});

/**
 * What reached a message's content, as the conversation knows it (UX2): a
 * user's own words and the files they attached; a durable answer's recorded
 * label. An answer legacy wrote recorded nothing of what its model read
 * (retrieved-but-uncited content, tool results), so it stays unknown.
 */
export function messageProvenance(message: IMessage): TurnLabel | null {
  if (message.messageType === 'user_query') {
    return parseTurnLabel({
      complete: true,
      sources: (message.attachments ?? []).map((attachment) => ({
        kind: 'attachment',
        recordId: attachment.recordId,
      })),
    });
  }
  if (message.messageType === 'bot_response')
    return parseTurnLabel(message.label);
  return null;
}

/**
 * What a conversation's messages read, joined (FG2): today's loop decides a follow-up turn's flow guard from it, as
 * a durable run does from its import's label. A message the model is sent nothing of adds nothing; one whose
 * provenance nobody recorded makes it unknown. The messages must be read `withLabels`.
 */
export const historyLabel = (messages: IMessage[]): TurnLabel =>
  joinLabels(
    messages
      .filter((message) => historyTurns(message).length > 0)
      .map(messageProvenance),
  );

interface LedgerRow {
  nextSeq?: number;
  harnessLedger?: HistoryLedger | null;
}

const ledgerRow = async (
  ref: ConversationRef,
  tx: ClientSession | null,
): Promise<LedgerRow | null> =>
  await ChatSession.findOne({ _id: ref.sessionId, orgId: ref.orgId })
    .select('+nextSeq +harnessLedger')
    .session(tx)
    .lean();

const plainLedger = (ledger: HistoryLedger): HistoryLedger => ({
  seq: ledger.seq,
  digest: ledger.digest,
  ...(ledger.diverged === true ? { diverged: true } : {}),
});

/** What the harness accounts for in a session row read with `+nextSeq +harnessLedger`. */
export const accountedOf = (row: LedgerRow | null): AccountedConversation => ({
  lastSeq: row?.nextSeq ?? 0,
  ledger: row?.harnessLedger ? plainLedger(row.harnessLedger) : null,
});

export class HarnessHistorySource implements HistorySource<Tx> {
  async read(
    ref: ConversationRef,
    txHandle: Tx | ClientSession,
    accounted?: AccountedConversation,
  ): Promise<ConversationHistory> {
    const tx = asMongoSession(txHandle);
    const known = accounted ?? accountedOf(await ledgerRow(ref, tx));
    // UX2: the labels say what the imported history read; only the import's label is derived from them.
    const messages = (await getMessages(
      ref.sessionId,
      { orgId: ref.orgId, withLabels: true },
      new MongoTx(tx),
    )) as Array<IMessage & { seq: number }>;
    return { messages: messages.map(asHistory), ...known };
  }
}

/** What a harness write sets on the session besides the messages it appends. */
export interface SessionWrite {
  $set?: Record<string, unknown>;
  $push?: Record<string, unknown>;
}

/**
 * The ledger after the harness appended `written` to a session whose counter
 * stood at `nextSeq`: extended over them when nothing else wrote since the
 * harness last did, else marked diverged so the next durable run re-imports
 * the conversation as it is. Null: unchanged.
 */
export const accountedLedger = (
  ledger: HistoryLedger,
  nextSeq: number,
  written: HistoryMessage[],
): HistoryLedger | null => {
  const lastSeq = nextSeq + written.length;
  if (lastSeq === ledger.seq) return null;
  if (ledger.diverged === true || nextSeq !== ledger.seq) {
    return { ...ledger, diverged: true };
  }
  return extendLedger(ledger, written, lastSeq);
};

/**
 * Appends `messages` to the session `filter` matches and applies `write` to
 * it, accounted in the session's ledger (a session with no ledger, one that
 * ran durably before T3, is left for its next admission to re-import). One
 * read of the session and one write of it; in a transaction that batches its
 * inserts the messages and the session's update go with them (PERF-E2,
 * PERF-E3). The messages' `seq` follow the session's counter, read in this
 * transaction: a concurrent writer of the session conflicts on its update.
 * `known`: the session as the transaction already wrote it (its creation or
 * its bind), so it is not read again. Null when no session matches `filter`.
 */
export async function appendAccounted(
  filter: QueryFilter<IChatSessionDocument>,
  txHandle: Tx | ClientSession | null,
  messages: ReadonlyArray<IMessage | NewMessage>,
  write: SessionWrite = {},
  known?: { ref: ConversationRef; accounted: AccountedConversation },
): Promise<IChatSessionMessageDocument[] | null> {
  const tx = asMongoSession(txHandle);
  const before =
    known !== undefined
      ? {
          _id: new Types.ObjectId(known.ref.sessionId),
          orgId: new Types.ObjectId(known.ref.orgId),
          nextSeq: known.accounted.lastSeq,
          harnessLedger: known.accounted.ledger,
        }
      : ((await ChatSession.findOne(filter)
          .select('+nextSeq +harnessLedger')
          .session(tx)
          .lean()) as
          (LedgerRow & { _id: Types.ObjectId; orgId: Types.ObjectId }) | null);
  if (before === null) return null;
  const nextSeq = before.nextSeq ?? 0;
  const stored = messages.map(
    (message, index) =>
      new ChatSessionMessage({
        ...message,
        sessionId: before._id,
        orgId: before.orgId,
        seq: nextSeq + index + 1,
      }) as IChatSessionMessageDocument,
  );
  const ledger = before.harnessLedger
    ? accountedLedger(
        plainLedger(before.harnessLedger),
        nextSeq,
        // As a read of them gives them: the ledger digests what a later import reads.
        stored.map((message) =>
          asHistory(
            message.toObject({ flattenMaps: true }) as IMessage & {
              seq: number;
            },
          ),
        ),
      )
    : null;
  if (stored.length > 0) {
    await transactionWrites.insert(tx, ChatSessionMessage, stored, () =>
      ChatSessionMessage.insertMany(stored, { ordered: true, session: tx }),
    );
  }
  const $set = {
    ...write.$set,
    ...(ledger !== null ? { harnessLedger: ledger } : {}),
  };
  const $inc = {
    ...(stored.length > 0 ? { nextSeq: stored.length } : {}),
    // A pushed array moves the version, as the legacy writer's `save()` moved it.
    ...(write.$push !== undefined ? { __v: 1 } : {}),
  };
  const update = {
    ...(Object.keys($inc).length > 0 ? { $inc } : {}),
    ...(Object.keys($set).length > 0 ? { $set } : {}),
    ...(write.$push !== undefined ? { $push: write.$push } : {}),
  };
  await transactionWrites.update(
    tx,
    ChatSession,
    { name: 'updateOne', filter: { _id: before._id }, update },
    () =>
      ChatSession.updateOne({ _id: before._id }, update, {
        session: tx ?? undefined,
      }),
  );
  return stored;
}
