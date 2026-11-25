import type { RunCommand } from '../domain/commands';
import { HarnessRun } from '../domain/execution';
import type { TurnLabel } from '../domain/turn-labels';
import type { AccountedConversation } from '../domain/session-import';

export interface ConversationRef {
  orgId: string;
  sessionId: string;
}

export interface UserTurn {
  query: string;
  appliedFilters?: Record<string, unknown>;
  chatMode: string;
  attachments: Array<Record<string, unknown>>;
  /** A collaborator sent it (SS2): recorded so history knows whose turn it is. */
  senderId?: string;
  /** The /command `query` was expanded from (SLASH1); a regenerate asks the same text again. */
  command?: RunCommand;
}

export type ConversationStatus = 'Complete' | 'Failed';

/**
 * What a durable run leaves in its conversation, projected from its journal
 * (P1.8): the answer in the legacy `completion_data` shape, preceded by the
 * questions it asked (the legacy `tools[]` rows), or a failure message.
 */
export type RunTranscript = (
  | {
      kind: 'answer';
      completion: Record<string, unknown>;
      questions: Array<{ toolData: unknown }>;
    }
  | {
      kind: 'failure';
      code: string;
      message: string;
      /** `mcp_tool_withdrawn`: the servers' labels, which the web app's words name (WP-84); absent: none. */
      servers?: string[];
      /** `operator_stopped`: the operator control's scope and reason, which the web app words (ADR-OPS-01 D5). */
      stop?: { scope: string; reason: string };
      /** `out_of_memory`: the sandbox's memory limit in MB, which the web app names (R31GVISO-FU-3). */
      memoryLimitMb?: number;
      /** The run's steering messages, kept with the failure (C-21); absent: none. */
      parts?: Array<Record<string, unknown>>;
    }
) & {
  /**
   * The turn's confidentiality label (P2.5a), stored with the run and on its
   * answer. Absent: an unlabelled turn.
   */
  label?: TurnLabel;
};

/** Transcript projection owned by the conversation module; writes join `tx`. */
export interface ConversationWriter<Tx = unknown> {
  /**
   * Appends the user turn and returns its message key. `accounted`: the
   * session's counter and ledger as this transaction already holds them (its
   * creation, or its bind with the ledger the import planned); the session is
   * then not read again, and that ledger is saved with the turn.
   */
  appendUserTurn(
    ref: ConversationRef,
    turn: UserTurn,
    tx: Tx,
    accounted?: AccountedConversation,
  ): Promise<string>;

  /**
   * Whether `answerId` is an answer of this conversation in this org. Asked
   * before anything about the conversation's runs, so an id from elsewhere is
   * not found rather than told the conversation's state (QA-26).
   */
  hasAnswer(ref: ConversationRef, answerId: string, tx: Tx): Promise<boolean>;

  /**
   * Regenerate (UX3): when `answerId` is the conversation's last answer,
   * removes that turn (its question, the answer and any rows between) and
   * returns the question as stored, to be asked again. Null otherwise; nothing
   * is removed. Not an accounted harness write, so the next import realigns
   * the journal's history to the conversation (MG-2c).
   */
  retractLastAnswer(
    ref: ConversationRef,
    answerId: string,
    tx: Tx,
  ): Promise<UserTurn | null>;

  /**
   * Persists the final answer for a completed run. False when the run's
   * durable session no longer exists for its actor (the owner, or for a
   * collaborator's run the session it was admitted into, SS2).
   */
  saveCompletedConversation(
    run: HarnessRun,
    completion: Record<string, unknown>,
    tx: Tx,
  ): Promise<boolean>;

  /**
   * Harness major 2: writes the run's transcript in the commit transaction.
   * False when the run's durable session no longer exists for its actor.
   */
  saveRunTranscript(
    run: HarnessRun,
    transcript: RunTranscript,
    tx: Tx,
  ): Promise<boolean>;

  /** Whether the run's durable session still exists, the one `saveRunTranscript` writes (QA21-F4). */
  runSessionExists(run: HarnessRun): Promise<boolean>;

  setConversationStatus(
    run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'actorId' | 'sessionRole'>,
    status: ConversationStatus,
    now: number,
    tx: Tx,
  ): Promise<void>;
}
