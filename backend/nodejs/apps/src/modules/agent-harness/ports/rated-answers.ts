/** What a rating is linked to: the run that wrote the answer and the agent version it ran. */
export interface FeedbackLink {
  runId: string;
  agentKey: string | null;
  agentVersion: number | null;
}

/** A rating as the conversation module saved it, with what an extension may read of the rated answer. */
export interface RatedAnswer {
  orgId: string;
  userId: string;
  sessionId: string;
  messageId: string;
  /** The run the answer names, if it is this conversation's; null otherwise. */
  link: FeedbackLink | null;
  /** The request body as the route validated it. */
  body: Readonly<Record<string, unknown>>;
  /** The conversation's last question before the rated answer, read only when called. */
  question(): Promise<string>;
  answer: string;
}

/**
 * Told of every rating, before it is stored, so a retried request tells it
 * again: an extension point (bound at `AGENT_HARNESS_RATED_ANSWER_SINK`).
 * Unbound: `noRatedAnswerSink`, which keeps nothing.
 */
export interface RatedAnswerSink {
  rated(rating: RatedAnswer): Promise<void>;
}

export const noRatedAnswerSink: RatedAnswerSink = Object.freeze({
  rated: () => Promise.resolve(),
});
