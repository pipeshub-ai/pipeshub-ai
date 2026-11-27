import type { HarnessRun } from '../domain/execution';
import type {
  EndedRunFact,
  ListedJob,
  ListedRun,
  PersonRunQuery,
} from '../domain/run-lists';

/**
 * ADR-UI-01 D1, D2 (G1): a person's runs across their conversations, and the run state of the conversations on their
 * chat list. Every read is bound to the caller's org and, for a person's runs, to the caller as the actor: a person
 * reads only their own (an admin's view of the org's is `OperatorRuns`).
 */
export interface RunLists {
  /** `query`'s runs, newest first (ties by run id, descending), at most `query.limit`. */
  personRuns(query: PersonRunQuery): Promise<ListedRun[]>;
  /** The runs of these conversations that have not ended, any person's, at most `MAX_SESSION_OPEN_RUNS`. */
  openRunsOfSessions(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<HarnessRun[]>;
  /**
   * UI-B5 (ADR-UI-01 D5, D6): the conversation's jobs (runs with a parent), anyone's, newest first (ties by run id,
   * descending), at most `limit`, each with its engine and the earlier job it continues. The caller checked who may
   * read the conversation.
   */
  sessionJobs(
    orgId: string,
    sessionId: string,
    limit: number,
  ): Promise<ListedJob[]>;
  /** Per conversation, the actor's run that ended last (by its last change); a conversation with none is left out. */
  lastEndedRuns(
    orgId: string,
    actorId: string,
    sessionIds: readonly string[],
  ): Promise<EndedRunFact[]>;
}

/**
 * When a person last opened a conversation (ADR-UI-01 D2): one mark per person and conversation, kept on the
 * conversation itself so it goes with it. A mark only moves forward.
 */
export interface ConversationSeenMarks {
  /** Moves the person's mark to `at` unless it is already later; resolves the mark held, or null: no such conversation. */
  mark(
    orgId: string,
    sessionId: string,
    userId: string,
    at: number,
  ): Promise<number | null>;
  /** The person's marks on these conversations; one they never marked is left out. */
  read(
    orgId: string,
    userId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, number>>;
}
