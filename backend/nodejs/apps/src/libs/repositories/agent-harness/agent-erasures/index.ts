import type { AgentErasureQueue } from '../../../../modules/agent-harness/ports/agent-erasures';
import { pickHarnessStore } from '../stores';
import { MongoAgentErasureQueue } from './mongo';
import { PgAgentErasureQueue } from './pg';

export const createAgentErasureQueue = (): AgentErasureQueue =>
  pickHarnessStore<AgentErasureQueue>('agent-harness/agent-erasures', {
    mongo: () => new MongoAgentErasureQueue(),
    postgres: () => new PgAgentErasureQueue(),
  });
