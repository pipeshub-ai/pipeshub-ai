import { FakeChatAttachments } from './support/chat-attachments';
import { InMemoryConversations } from '../../../src/modules/agent-harness/adapters/in-memory-conversations';
import { SeedConversation, seedInMemoryConversations } from './support/conversation-seeds';
import {
  ConversationRemains,
  describeConversationEraserContract,
  describeConversationRetentionContract,
  describeConversationStateLookupContract,
} from './support/conversation-erasure-contract';

const world = () => {
  const attachments = new FakeChatAttachments();
  const conversations = new InMemoryConversations(() => 5_000, attachments);
  const seed = (seeded: SeedConversation[]): Promise<void> => {
    seedInMemoryConversations(conversations, seeded);
    return Promise.resolve();
  };
  return { conversations, seed, attachments };
};

const remainsIn =
  (conversations: InMemoryConversations) =>
  (orgId: string, sessionId: string): Promise<ConversationRemains> => {
    const found = conversations.get(sessionId);
    return Promise.resolve(
      found?.orgId !== orgId
        ? { sessions: 0, messages: 0, citations: 0, legacyCopies: 0 }
        : {
            sessions: 1,
            messages: found.counts?.chatSessionMessages ?? 0,
            citations: found.counts?.citations ?? 0,
            legacyCopies: found.counts?.legacyCopies ?? 0,
          },
    );
  };

describeConversationStateLookupContract('InMemoryConversations', async () => {
  const { conversations, seed } = world();
  return { lookup: conversations, seed };
});

const removedAttachmentsIn =
  (conversations: InMemoryConversations) =>
  (orgId: string, sessionId: string): Promise<string[]> => {
    const found = conversations.get(sessionId);
    return Promise.resolve(
      found?.orgId !== orgId
        ? []
        : (found.attachments ?? []).filter((a) => a.removed === true).map((a) => a.recordId).sort(),
    );
  };

describeConversationEraserContract('InMemoryConversations', async () => {
  const { conversations, seed, attachments } = world();
  return {
    eraser: conversations,
    seed,
    remains: remainsIn(conversations),
    attachments,
    removedIn: removedAttachmentsIn(conversations),
  };
});

describeConversationRetentionContract<unknown>('InMemoryConversations', async () => {
  const { conversations, seed } = world();
  return { retention: conversations, seed, inTransaction: (work) => work(undefined) };
});
