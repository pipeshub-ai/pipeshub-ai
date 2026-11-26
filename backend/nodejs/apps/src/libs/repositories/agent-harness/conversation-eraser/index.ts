import type {
  AttachmentEraser,
  ConversationEraser,
  ConversationStateLookup,
} from '../../../../modules/agent-harness/ports/session-erasure';
import { pickHarnessStore } from '../stores';
import { HarnessConversationEraser } from './mongo';
import { PgHarnessConversationEraser } from './pg';

export const createConversationEraser = (
  attachments: AttachmentEraser,
): ConversationEraser & ConversationStateLookup =>
  pickHarnessStore<ConversationEraser & ConversationStateLookup>(
    'agent-harness/conversation-eraser',
    {
      mongo: () => new HarnessConversationEraser(attachments),
      postgres: () => new PgHarnessConversationEraser(attachments),
    },
  );
