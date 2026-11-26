import type { ChatSessionRepository } from '../../../../modules/agent-harness/ports/chat-session-repository';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { HarnessChatSessionRepository } from './mongo';
import { PgHarnessChatSessionRepository } from './pg';

export const createChatSessionRepository = (): ChatSessionRepository<Tx> =>
  pickHarnessStore<ChatSessionRepository<Tx>>(
    'agent-harness/chat-session-repository',
    {
      mongo: () => new HarnessChatSessionRepository(),
      postgres: () => new PgHarnessChatSessionRepository(),
    },
  );
