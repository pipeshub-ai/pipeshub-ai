import type { TurnLabel } from '../domain/turn-labels';
import { randomUUID } from 'node:crypto';
import {
  ForkCitationData,
  ForkedMessageFields,
  ForkKey,
  forkRecordId,
  ForkSourceMessage,
} from '../domain/session-forks';
import { SessionParticipants } from '../domain/session-roles';
import { SessionExecutionScope } from '../ports/chat-session-repository';
import {
  ForkAlreadyCommittedError,
  ForkCommit,
  ForkRecord,
  ForkSource,
  SessionForkStore,
} from '../ports/session-forks';

/** A conversation as the in-memory store keeps it. */
export interface InMemoryConversation {
  orgId: string;
  participants: SessionParticipants;
  sessionType: 'chat' | 'agent';
  agentKey: string | null;
  title: string;
  modelInfo: Record<string, unknown> | null;
  /** Runs on the durable harness (`harnessVersion: durable-v1`); absent: not. */
  durable?: boolean;
  deleted: boolean;
  messages: InMemoryMessage[];
}

export interface InMemoryMessage {
  messageType: string;
  senderId: string | null;
  content: string;
  /** Ids into `citations`. */
  citations: Array<{
    citationId: string;
    relevanceScore?: number;
    excerpt?: string;
    context?: string;
  }>;
  fields: ForkedMessageFields;
  foreignTurn?: boolean;
  /** Copied from another conversation: it names none of its runs (FORKFIX). */
  copied?: boolean;
  /** A durable run's answer: its turn's label (P2.5a). */
  label?: TurnLabel;
  /** Whatever else a source message held (tool rows, reasoning, …); never copied. */
  extra?: Record<string, unknown>;
}

interface StoredRecord extends ForkRecord {
  key: ForkKey;
}

/** The in-memory transaction: commits are staged and applied only when the work succeeds. */
export interface InMemoryForkTx {
  staged: Array<() => void>;
}

/** Conversations, their citations and fork records in memory, for the fork contract. */
export class InMemorySessionForkStore implements SessionForkStore<InMemoryForkTx> {
  readonly conversations = new Map<string, InMemoryConversation>();
  readonly citations = new Map<string, ForkCitationData>();
  readonly records = new Map<string, StoredRecord>();

  async transaction<T>(work: (tx: InMemoryForkTx) => Promise<T>): Promise<T> {
    const tx: InMemoryForkTx = { staged: [] };
    const result = await work(tx);
    for (const apply of tx.staged) apply();
    return result;
  }

  addCitation(data: ForkCitationData): string {
    const id = randomUUID();
    this.citations.set(id, structuredClone(data));
    return id;
  }

  find(key: ForkKey): Promise<ForkRecord | null> {
    const record = this.records.get(forkRecordId(key));
    return Promise.resolve(record === undefined ? null : view(record));
  }

  begin(key: ForkKey, newSessionId: string): Promise<ForkRecord> {
    const id = forkRecordId(key);
    const record = this.records.get(id) ?? {
      key,
      forkSessionId: newSessionId,
      result: null,
    };
    this.records.set(id, record);
    return Promise.resolve(view(record));
  }

  source(scope: SessionExecutionScope): Promise<ForkSource | null> {
    const conversation = this.conversations.get(scope.sessionId);
    const agentKey =
      scope.agentKey !== undefined && scope.agentKey.length > 0
        ? scope.agentKey
        : null;
    if (
      conversation === undefined ||
      conversation.deleted ||
      conversation.orgId !== scope.orgId ||
      conversation.agentKey !== agentKey
    ) {
      return Promise.resolve(null);
    }
    return Promise.resolve(
      structuredClone({
        participants: conversation.participants,
        sessionType: conversation.sessionType,
        agentKey: conversation.agentKey,
        title: conversation.title,
        modelInfo: conversation.modelInfo,
        durable: conversation.durable === true,
        messages: conversation.messages.map((message): ForkSourceMessage => ({
          messageType: message.messageType,
          senderId: message.senderId,
          content: message.content,
          citations: message.citations.flatMap((link) => {
            const data = this.citations.get(link.citationId);
            if (data === undefined) return [];
            const { citationId: _id, ...use } = link;
            return [{ ...use, data }];
          }),
          fields: message.fields,
          label: message.label ?? null,
        })),
      }),
    );
  }

  commit(fork: ForkCommit, tx: InMemoryForkTx): Promise<void> {
    const id = forkRecordId(fork.key);
    const record = this.records.get(id);
    if (record?.result) return Promise.reject(new ForkAlreadyCommittedError());
    if (this.conversations.has(fork.forkSessionId)) {
      return Promise.reject(new Error('The fork session already exists'));
    }
    tx.staged.push(() => {
      const messages = fork.messages.map((message): InMemoryMessage => ({
        messageType: message.messageType,
        senderId: null,
        content: message.content,
        citations: message.citations.map(({ data, ...use }) => ({
          ...use,
          citationId: this.addCitation(data),
        })),
        fields: structuredClone(message.fields),
        ...(message.foreignTurn ? { foreignTurn: true } : { copied: true }),
      }));
      this.conversations.set(fork.forkSessionId, {
        orgId: fork.key.orgId,
        participants: {
          ownerId: fork.key.actorId,
          isShared: false,
          shares: [],
        },
        sessionType: fork.source.sessionType,
        agentKey: fork.source.agentKey,
        title: fork.source.title,
        modelInfo: structuredClone(fork.source.modelInfo),
        ...(fork.source.durable ? { durable: true } : {}),
        deleted: false,
        messages,
      });
      this.records.set(id, {
        key: fork.key,
        forkSessionId: fork.forkSessionId,
        result: structuredClone(fork.result),
      });
    });
    return Promise.resolve();
  }
}

const view = (record: StoredRecord): ForkRecord => ({
  forkSessionId: record.forkSessionId,
  result: record.result === null ? null : structuredClone(record.result),
});
