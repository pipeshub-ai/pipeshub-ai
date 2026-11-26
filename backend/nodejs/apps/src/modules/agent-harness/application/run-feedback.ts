import type { ExecutionStore } from '../ports/execution-store';
import {
  noRatedAnswerSink,
  type FeedbackLink,
  type RatedAnswer,
  type RatedAnswerSink,
} from '../ports/rated-answers';

export type { FeedbackLink, RatedAnswer } from '../ports/rated-answers';

/**
 * G-15: a rating linked to the run and agent version that answered, and
 * passed to the bound rated-answer sink (none by default).
 */
export class RunFeedbackService {
  constructor(
    private readonly runs: Pick<ExecutionStore, 'get'>,
    private readonly sink: RatedAnswerSink = noRatedAnswerSink,
  ) {}

  /** The run an answer names, if it is this conversation's: a run of another session links nothing. */
  async link(
    orgId: string,
    sessionId: string,
    runId: string | undefined,
  ): Promise<FeedbackLink | null> {
    if (runId === undefined || runId.length === 0) return null;
    const run = await this.runs.get(orgId, runId);
    if (run === null || run.sessionId !== sessionId) return null;
    return {
      runId: run.runId,
      agentKey:
        run.agentKey !== undefined && run.agentKey.length > 0
          ? run.agentKey
          : null,
      agentVersion: run.agentVersion ?? null,
    };
  }

  record(rating: RatedAnswer): Promise<void> {
    return this.sink.rated(rating);
  }
}
