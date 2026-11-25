import { createHash } from 'node:crypto';
import { canonicalJsonBytes } from '../contracts/canonical-json';
import type { JournalEventInput } from './journal';
import { joinLabels, type TurnLabel } from './turn-labels';

/**
 * Legacy session import (P1b T3, REPORT 6.1.2 `session.imported`).
 *
 * The conversation (`chatSessions`) is what the user sees and what legacy
 * sends the model. A durable run gives the model the session's journal, so
 * every turn the journal does not hold has to be imported into it, at the
 * place the run starts: the legacy turns of a session that began on legacy,
 * and the turns legacy wrote while the org was rolled back or a chat mode
 * stayed legacy.
 *
 * What the harness has accounted for is a ledger on the conversation: the
 * last message seq it covers and a digest chained over every covered
 * message's history entries (what the model sees of it), whoever wrote it.
 * Harness writes extend it; anything else that writes leaves it behind.
 * At the next durable admission:
 * - nothing after the ledger: nothing to import;
 * - messages after it, none before it changed: import just those (a delta);
 * - the covered messages no longer digest to the ledger (legacy regenerated
 *   an answer, a message is gone), or another writer wrote while the harness
 *   did (`diverged`), or the session ran durably before there was a ledger:
 *   the journal no longer describes the conversation. The import then carries
 *   the whole conversation and replaces the history before it, so the model
 *   sees the conversation as it is now. Nothing is refused: the conversation
 *   is the truth the user sees, and the journal realigns to it.
 */

/** One conversation message: its seq and its `previousConversations` entries (none for an error or a question row). */
export interface HistoryMessage {
  seq: number;
  turns: Array<Record<string, unknown>>;
  /**
   * What reached the message's content, as far as the conversation knows (UX2): a user's turn names its
   * attachments; an answer a durable run wrote carries its recorded label; null for anything else (an answer
   * legacy wrote), which is unknown. Never part of the ledger's digest.
   */
  provenance?: TurnLabel | null;
}

export interface HistoryLedger {
  /** The last message seq the harness accounted for. */
  seq: number;
  /** Chained over the covered messages' history entries. */
  digest: string;
  /** Another writer wrote while the harness did: the next run re-imports. */
  diverged?: boolean;
}

export interface ConversationHistory {
  messages: HistoryMessage[];
  /** The last message seq allocated in the conversation. */
  lastSeq: number;
  ledger: HistoryLedger | null;
}

/** A conversation's counter and ledger as a transaction that wrote its session holds them. */
export type AccountedConversation = Pick<
  ConversationHistory,
  'lastSeq' | 'ledger'
>;

export type ImportMismatch = 'changed' | 'interleaved' | 'untracked';

export interface ImportPlan {
  upToMessageSeq: number;
  turns: Array<Record<string, unknown>>;
  replacesHistory: boolean;
  mismatch?: ImportMismatch;
  /** The imported messages' labels joined (UX2): unknown as soon as one message's provenance is. */
  label: TurnLabel;
}

/** What admission reports, for the log lines: counts and a digest, no content. */
export interface SessionImportSummary {
  upToMessageSeq: number;
  messages: number;
  digest: string;
  replacesHistory: boolean;
  sealed: boolean;
  mismatch?: ImportMismatch;
}

export const EMPTY_LEDGER: HistoryLedger = {
  seq: 0,
  digest: `sha256:${createHash('sha256').digest('hex')}`,
};

const chain = (digest: string, message: HistoryMessage): string =>
  `sha256:${createHash('sha256')
    .update(digest)
    .update(canonicalJsonBytes({ seq: message.seq, turns: message.turns }))
    .digest('hex')}`;

/** `ledger` extended over the messages after it (in seq order). */
export function extendLedger(
  ledger: HistoryLedger,
  messages: HistoryMessage[],
  upTo: number,
): HistoryLedger {
  let digest = ledger.digest;
  for (const message of messages) {
    if (
      message.seq > ledger.seq &&
      message.seq <= upTo &&
      message.turns.length > 0
    ) {
      digest = chain(digest, message);
    }
  }
  return { seq: Math.max(ledger.seq, upTo), digest };
}

const turnsOf = (
  messages: HistoryMessage[],
  after: number,
): Array<Record<string, unknown>> =>
  messages.filter((m) => m.seq > after).flatMap((m) => m.turns);

/** What the model will read of the imported messages, labelled; a message it reads nothing of adds nothing. */
export const importLabel = (
  messages: HistoryMessage[],
  after: number,
): TurnLabel =>
  joinLabels(
    messages
      .filter((m) => m.seq > after && m.turns.length > 0)
      .map((m) => m.provenance ?? null),
  );

function mismatch(
  history: ConversationHistory,
  journalHasHistory: boolean,
): ImportMismatch | null {
  const { ledger } = history;
  if (ledger === null) return journalHasHistory ? 'untracked' : null;
  if (ledger.diverged === true) return 'interleaved';
  const now = extendLedger(EMPTY_LEDGER, history.messages, ledger.seq);
  return now.digest === ledger.digest ? null : 'changed';
}

/**
 * The import a durable admission journals before its run (null: none), and
 * the ledger once it is journaled: every message so far accounted for.
 * `journalHasHistory`: a durable run was admitted in the session before.
 */
export function planSessionImport(
  history: ConversationHistory,
  journalHasHistory: boolean,
): { plan: ImportPlan | null; ledger: HistoryLedger } {
  const ledger = extendLedger(EMPTY_LEDGER, history.messages, history.lastSeq);
  const found = mismatch(history, journalHasHistory);
  const after = found === null ? (history.ledger?.seq ?? 0) : 0;
  const turns = turnsOf(history.messages, after);
  if (turns.length === 0 && found === null) return { plan: null, ledger };
  return {
    plan: {
      upToMessageSeq: history.lastSeq,
      turns,
      replacesHistory: found !== null,
      ...(found !== null ? { mismatch: found } : {}),
      label: importLabel(history.messages, after),
    },
    ledger,
  };
}

/**
 * A transcript of this many bytes travels in its event (`inline:`, as a
 * steering message does); a longer one is sealed content the worker reads.
 */
export const IMPORT_INLINE_MAX_BYTES = 128 * 1024;

export interface ImportContentRef {
  ref: string;
  hash: string;
  size: number;
}

export function sessionImportedEvent(
  plan: ImportPlan,
  transcript: ImportContentRef,
): JournalEventInput {
  return {
    type: 'session.imported',
    schemaVersion: 1,
    critical: true,
    visibility: 'owner',
    data: {
      upToMessageSeq: plan.upToMessageSeq,
      transcriptDigest: transcript.hash,
      transcriptRef: transcript,
      messages: plan.turns.length,
      ...(plan.replacesHistory ? { replacesHistory: true } : {}),
    },
  };
}

/**
 * The imported history's label (UX2, `x.pipeshub.history.labelled`), journaled
 * with its `session.imported`: what the worker's ledger counts the import as.
 * Without it (an older Node) the import is unknown context.
 */
export function historyLabelledEvent(
  transcript: Pick<ImportContentRef, 'hash'>,
  label: TurnLabel,
): JournalEventInput {
  return {
    type: 'x.pipeshub.history.labelled',
    schemaVersion: 1,
    critical: false,
    visibility: 'owner',
    data: { transcriptDigest: transcript.hash, label },
  };
}
