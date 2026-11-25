import { HarnessRun } from '../domain/execution';

/**
 * The conversation as the legacy stream's final `RUN_FINISHED` carried it
 * (the session with its newest answer, citations populated). `null` when the
 * run's session no longer exists.
 */
export interface ConversationReader {
  finishedConversation(
    run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'actorId' | 'agentKey'>,
  ): Promise<Record<string, unknown> | null>;
}
