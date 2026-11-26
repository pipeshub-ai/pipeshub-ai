import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { parseTurnLabel } from '../../../../modules/agent-harness/domain/turn-labels';
import { ClientSession, Types } from 'mongoose';
import {
  EXCLUDE_AGENT,
  ONLY_AGENT,
} from '../../../../modules/enterprise_search/constants/constants';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../../modules/enterprise_search/schema/citation.schema';
import {
  sessionParticipantsOf,
  ParticipantFields,
} from '../session-roles/mongo';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { AgentSessionForkModel } from './schema/session-forks.schema';
import {
  ForkCitationData,
  ForkCitationLink,
  ForkKey,
  forkRecordId,
  ForkResult,
  ForkSourceMessage,
} from '../../../../modules/agent-harness/domain/session-forks';
import { EMPTY_LEDGER } from '../../../../modules/agent-harness/domain/session-import';
import { SessionExecutionScope } from '../../../../modules/agent-harness/ports/chat-session-repository';
import {
  ForkAlreadyCommittedError,
  ForkCommit,
  ForkRecord,
  ForkSource,
  SessionForkStore,
} from '../../../../modules/agent-harness/ports/session-forks';

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const COPIED_FIELDS = [
  'contentFormat',
  'status',
  'confidence',
  'followUpQuestions',
  'modelInfo',
  'appliedFilters',
  'metadata',
] as const;

/** A stored citation's own content, without the document's bookkeeping. */
function citationData(stored: unknown): ForkCitationData | null {
  if (!isObject(stored)) return null;
  const metadata = isObject(stored['metadata'])
    ? { ...stored['metadata'] }
    : {};
  delete metadata['_id'];
  return {
    content: typeof stored['content'] === 'string' ? stored['content'] : '',
    chunkIndex:
      typeof stored['chunkIndex'] === 'number' ? stored['chunkIndex'] : 0,
    citationType:
      typeof stored['citationType'] === 'string' ? stored['citationType'] : '',
    metadata,
  };
}

export function sourceMessage(message: Json): ForkSourceMessage {
  const fields: Json = {};
  for (const field of COPIED_FIELDS) {
    if (message[field] !== undefined && message[field] !== null) {
      fields[field] = message[field];
    }
  }
  const citations: ForkCitationLink[] = [];
  for (const entry of Array.isArray(message['citations'])
    ? (message['citations'] as unknown[])
    : []) {
    if (!isObject(entry)) continue;
    const data = citationData(entry['citationId']);
    // A citation whose stored document is gone has nothing to copy.
    if (data === null) continue;
    citations.push({
      data,
      ...(typeof entry['relevanceScore'] === 'number'
        ? { relevanceScore: entry['relevanceScore'] }
        : {}),
      ...(typeof entry['excerpt'] === 'string'
        ? { excerpt: entry['excerpt'] }
        : {}),
      ...(typeof entry['context'] === 'string'
        ? { context: entry['context'] }
        : {}),
    });
  }
  // An ObjectId or a string: either prints as the id.
  const sender = message['senderId'] as
    { toString(): string } | null | undefined;
  return {
    messageType: String(message['messageType']),
    senderId: sender == null ? null : String(sender),
    content: typeof message['content'] === 'string' ? message['content'] : '',
    citations,
    fields: fields,
    label: parseTurnLabel(message['label']),
  };
}

const citationIdAt = (
  created: ReadonlyArray<{ _id: unknown }>,
  index: number,
): unknown => {
  const citation = created[index];
  if (citation === undefined)
    throw new Error('A copied citation was not written');
  return citation._id;
};

const recordOf = (document: {
  forkSessionId: string;
  result: ForkResult | null;
}): ForkRecord => ({
  forkSessionId: document.forkSessionId,
  result: document.result ?? null,
});

/**
 * The conversation side of "continue privately" (SS3) on MongoDB: reads the
 * source conversation with its citations, and in the fork's transaction
 * writes the new conversation (owned by the forker, not shared, no project),
 * its messages and new citation documents, and marks the request done.
 */
export class HarnessSessionForkStore implements SessionForkStore<Tx> {
  async find(key: ForkKey): Promise<ForkRecord | null> {
    const record = await AgentSessionForkModel.findById(forkRecordId(key))
      .read('primary')
      .lean();
    return record === null ? null : recordOf(record);
  }

  async begin(
    key: ForkKey,
    newSessionId: string,
    now: number,
  ): Promise<ForkRecord> {
    const record = await AgentSessionForkModel.findOneAndUpdate(
      { _id: forkRecordId(key) },
      {
        $setOnInsert: {
          orgId: key.orgId,
          sourceSessionId: key.sourceSessionId,
          actorId: key.actorId,
          forkSessionId: newSessionId,
          result: null,
          createdAt: now,
          completedAt: null,
        },
      },
      { upsert: true, returnDocument: 'after', ...MAJORITY_WRITE },
    ).lean();
    return recordOf(record);
  }

  async source(scope: SessionExecutionScope): Promise<ForkSource | null> {
    if (
      ![scope.sessionId, scope.orgId, scope.actorId].every((id) =>
        Types.ObjectId.isValid(id),
      )
    ) {
      return null;
    }
    const isAgent = scope.agentKey !== undefined && scope.agentKey.length > 0;
    const session = await ChatSession.findOne({
      _id: scope.sessionId,
      orgId: scope.orgId,
      isDeleted: false,
      ...(isAgent
        ? { agentKey: scope.agentKey, ...ONLY_AGENT }
        : EXCLUDE_AGENT),
    })
      .select(
        'userId isShared sharedWith title modelInfo agentKey +harnessVersion',
      )
      .read('primary')
      .lean<
        ParticipantFields & {
          title?: string;
          modelInfo?: Json;
          agentKey?: string;
          harnessVersion?: string;
        }
      >();
    if (session === null) return null;
    const messages = await ChatSessionMessage.find({
      sessionId: new Types.ObjectId(scope.sessionId),
      orgId: new Types.ObjectId(scope.orgId),
    })
      .select('+label')
      .sort({ seq: 1 })
      .populate({
        path: 'citations.citationId',
        model: 'citation',
        select: '-__v',
      })
      .read('primary')
      .lean<Json[]>();
    return {
      participants: sessionParticipantsOf(session),
      sessionType: isAgent ? 'agent' : 'chat',
      agentKey: isAgent ? (session.agentKey ?? null) : null,
      title: session.title ?? '',
      modelInfo: isObject(session.modelInfo) ? session.modelInfo : null,
      durable: session.harnessVersion === 'durable-v1',
      messages: messages.map(sourceMessage),
    };
  }

  async commit(fork: ForkCommit, txHandle: Tx | ClientSession): Promise<void> {
    const tx = asMongoSession(txHandle);
    const marked = await AgentSessionForkModel.updateOne(
      { _id: forkRecordId(fork.key), result: null },
      { $set: { result: fork.result, completedAt: fork.now } },
      { session: tx },
    );
    if (marked.matchedCount === 0) throw new ForkAlreadyCommittedError();
    const sessionId = new Types.ObjectId(fork.forkSessionId);
    const orgId = new Types.ObjectId(fork.key.orgId);
    const owner = new Types.ObjectId(fork.key.actorId);
    const isAgent = fork.source.sessionType === 'agent';
    await ChatSession.create(
      [
        {
          _id: sessionId,
          orgId,
          userId: owner,
          initiator: owner,
          title: fork.source.title,
          isShared: false,
          sharedWith: [],
          lastActivityAt: fork.now,
          status: 'Complete',
          nextSeq: fork.messages.length,
          ...(isAgent
            ? {
                agentKey: fork.source.agentKey ?? undefined,
                sessionType: 'agent',
                conversationSource: 'agent_chat',
              }
            : { sessionType: 'chat' }),
          ...(fork.source.modelInfo !== null
            ? { modelInfo: fork.source.modelInfo }
            : {}),
          // An empty ledger: the next durable admission imports the copied messages (session-import.ts).
          ...(fork.source.durable
            ? {
                harnessVersion: 'durable-v1',
                harnessLedger: EMPTY_LEDGER,
              }
            : {}),
        },
      ],
      { session: tx },
    );
    const datas = fork.messages.flatMap((message) =>
      message.citations.map((citation) => citation.data),
    );
    const created =
      datas.length === 0
        ? []
        : await Citation.insertMany(datas, { session: tx, ordered: true });
    let next = 0;
    await ChatSessionMessage.insertMany(
      fork.messages.map((message, index) => ({
        sessionId,
        orgId,
        seq: index + 1,
        messageType: message.messageType,
        content: message.content,
        ...message.fields,
        citations: message.citations.map(({ data: _data, ...use }) => ({
          ...use,
          citationId: citationIdAt(created, next++),
        })),
        // Copied turns name no run: the original's runs are read only through the original (FORKFIX).
        ...(message.foreignTurn ? { foreignTurn: true } : { copied: true }),
      })),
      { session: tx, ordered: true },
    );
  }
}
