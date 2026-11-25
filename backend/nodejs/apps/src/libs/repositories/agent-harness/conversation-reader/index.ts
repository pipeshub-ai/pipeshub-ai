import type { ConversationReader } from '../../../../modules/agent-harness/ports/conversation-reader';
import { pickHarnessStore } from '../stores';
import { HarnessConversationReader } from './mongo';
import { PgHarnessConversationReader } from './pg';

export const createConversationReader = (): ConversationReader =>
  pickHarnessStore<ConversationReader>('agent-harness/conversation-reader', {
    mongo: () => new HarnessConversationReader(),
    postgres: () => new PgHarnessConversationReader(),
  });
