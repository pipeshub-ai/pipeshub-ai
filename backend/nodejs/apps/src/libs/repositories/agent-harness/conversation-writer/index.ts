import type { ConversationWriter } from '../../../../modules/agent-harness/ports/conversation-writer';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { HarnessConversationWriter } from './mongo';
import { PgHarnessConversationWriter } from './pg';

export const createConversationWriter = (): ConversationWriter<Tx> =>
  pickHarnessStore<ConversationWriter<Tx>>(
    'agent-harness/conversation-writer',
    {
      mongo: () => new HarnessConversationWriter(),
      postgres: () => new PgHarnessConversationWriter(),
    },
  );
