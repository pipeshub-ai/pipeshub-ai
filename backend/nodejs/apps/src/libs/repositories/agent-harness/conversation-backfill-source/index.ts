import type { BackfillSource } from '../../../../modules/agent-harness/ports/erasure-backfill';
import { pickHarnessStore } from '../stores';
import { HarnessConversationBackfillSource } from './mongo';
import { PgHarnessConversationBackfillSource } from './pg';

export const createConversationBackfillSource = (): BackfillSource =>
  pickHarnessStore<BackfillSource>(
    'agent-harness/conversation-backfill-source',
    {
      mongo: () => new HarnessConversationBackfillSource(),
      postgres: () => new PgHarnessConversationBackfillSource(),
    },
  );
