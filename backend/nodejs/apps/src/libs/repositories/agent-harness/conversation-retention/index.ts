import type { ConversationRetention } from '../../../../modules/agent-harness/ports/session-retention';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { HarnessConversationRetention } from './mongo';
import { PgHarnessConversationRetention } from './pg';

export const createConversationRetention = (): ConversationRetention<Tx> =>
  pickHarnessStore<ConversationRetention<Tx>>(
    'agent-harness/conversation-retention',
    {
      mongo: () => new HarnessConversationRetention(),
      postgres: () => new PgHarnessConversationRetention(),
    },
  );
