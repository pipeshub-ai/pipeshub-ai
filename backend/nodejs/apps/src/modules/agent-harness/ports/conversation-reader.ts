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

/**
 * A finished run's conversation as the person watching the run may read it:
 * the rows of other people's turns lose what that person cannot read, and a
 * hidden answer carries `answerHidden`, as a fetch of the conversation answers.
 */
export type RunConversationView = (
  viewer: { userId: string; orgId: string },
  conversation: Record<string, unknown>,
  /** `ownAnswer`: the run whose answer the watcher's own turn just produced. */
  opts: {
    ownAnswer?: string;
    citationsDecided: boolean;
    checkFailed: boolean;
  },
) => Promise<Record<string, unknown>>;
