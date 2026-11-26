/**
 * "Continue privately" (SS3, ARCHITECTURE 4.9 "Fork", F11 3.9): a
 * participant copies a shared session into a new one of their own. The copy
 * holds their own turns as they see them, another participant's turn whose
 * label passes for them (P2.5a) as its question and answer, and a stub for
 * every other one (SS2's rule), so the fork never shows more than its owner
 * could already read. Pure, with opaque ids.
 */
import { createHash } from 'node:crypto';
import { FOREIGN_TURN_STUB, projectTurns } from './session-audience';
import type { SessionRole } from './session-roles';
import type { LabelledTurn, TurnLabel } from './turn-labels';

/** The messages a turn keeps: its question and its final answer. */
export type ForkMessageType = 'user_query' | 'bot_response';

const TURN_MESSAGE_TYPES: ReadonlySet<string> = new Set<ForkMessageType>([
  'user_query',
  'bot_response',
]);

/** A stored citation in the finalizer's shape, as the fork copies it. */
export interface ForkCitationData {
  content: string;
  chunkIndex: number;
  citationType: string;
  metadata: Record<string, unknown>;
}

/** One citation of a message: the stored citation and how the message used it. */
export interface ForkCitationLink {
  data: ForkCitationData;
  relevanceScore?: number;
  excerpt?: string;
  context?: string;
}

/** What a copied message keeps besides its text and citations. */
export interface ForkedMessageFields {
  contentFormat?: string;
  status?: string;
  confidence?: string;
  followUpQuestions?: unknown[];
  modelInfo?: Record<string, unknown>;
  appliedFilters?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

/** A source conversation message as the fork reads it (tool rows, reasoning and the like left behind). */
export interface ForkSourceMessage {
  messageType: string;
  /** Who sent it (SS2); null: the session's owner. */
  senderId: string | null;
  content: string;
  /** Citations whose stored document still exists. */
  citations: ForkCitationLink[];
  fields: ForkedMessageFields;
  /** A durable run's answer: its turn's label (P2.5a); absent: unlabelled. */
  label?: TurnLabel | null;
}

export interface ForkedMessage {
  messageType: ForkMessageType;
  content: string;
  citations: ForkCitationLink[];
  fields: ForkedMessageFields;
  /** A stub standing for someone else's turn. */
  foreignTurn: boolean;
  /**
   * Copied from someone else's turn whose label passed for the forker
   * (P2.5a): its citations and artifacts are checked with the forker's
   * credential, whoever owns the session. Never stored.
   */
  passed?: boolean;
}

const stubPair = (): ForkedMessage[] =>
  (['user_query', 'bot_response'] as const).map((messageType) => ({
    messageType,
    content: FOREIGN_TURN_STUB,
    citations: [],
    fields: {},
    foreignTurn: true,
  }));

/**
 * The fork's transcript before its citations and artifacts are checked: the
 * forker's own questions and answers with only the fields a copy keeps, and
 * one stub pair for each run of someone else's messages.
 */
export function forkTranscript(
  messages: readonly ForkSourceMessage[],
  forkerId: string,
  ownerId: string,
  passing: ReadonlySet<string> = new Set(),
): ForkedMessage[] {
  const answerOf = turnAnswers(messages);
  const own = (message: ForkSourceMessage): boolean =>
    (message.senderId ?? ownerId) === forkerId;
  const turns = messages
    .map((message, index) => ({ message, answer: answerOf[index] }))
    .filter(({ message }) => TURN_MESSAGE_TYPES.has(message.messageType));
  return projectTurns<(typeof turns)[number], ForkedMessage>(
    turns,
    ({ message, answer }) => own(message) || passing.has(String(answer)),
    ({ message }) => ({
      messageType: message.messageType as ForkMessageType,
      content: message.content,
      citations: message.citations,
      fields: message.fields,
      foreignTurn: false,
      ...(own(message) ? {} : { passed: true }),
    }),
    stubPair,
  );
}

/**
 * For each message, the index of its turn's answer (the `bot_response` that
 * closes the turn it belongs to), or -1. A turn is a question and what follows
 * it up to the next question.
 */
export function turnAnswers(messages: readonly ForkSourceMessage[]): number[] {
  const answers = messages.map(() => -1);
  let start = 0;
  messages.forEach((message, index) => {
    if (message.messageType === 'user_query') start = index;
    if (message.messageType === 'bot_response') {
      for (let at = start; at <= index; at += 1) answers[at] = index;
      start = index + 1;
    }
  });
  return answers;
}

/** Other participants' answers and their labels, by message index (what the fork checks). */
export function foreignAnswers(
  messages: readonly ForkSourceMessage[],
  forkerId: string,
  ownerId: string,
): LabelledTurn[] {
  return messages.flatMap((message, index) =>
    message.messageType === 'bot_response' &&
    (message.senderId ?? ownerId) !== forkerId
      ? [{ id: String(index), label: message.label ?? null }]
      : [],
  );
}

/** Names one fork: who copies which session, under which request key. */
export interface ForkKey {
  orgId: string;
  sourceSessionId: string;
  actorId: string;
  requestKey: string;
}

export const SESSION_FORK_KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;

/** The fork record's id: one per (org, source, forker, request key). */
export const forkRecordId = (key: ForkKey): string =>
  `${key.orgId}:${key.sourceSessionId}:${key.actorId}:${createHash('sha256')
    .update(key.requestKey)
    .digest('hex')
    .slice(0, 32)}`;

export interface ForkCounts {
  messages: number;
  stubs: number;
  citations: number;
  citationsHidden: number;
  artifacts: number;
  artifactsHidden: number;
  files: number;
}

export interface ForkResult {
  sessionId: string;
  fromSessionId: string;
  /** The source stream's last seq the copy accounts for. */
  atSeq: number;
  role: SessionRole;
  counts: ForkCounts;
}

export interface ForkOutcome extends ForkResult {
  replayed: boolean;
}

/** Messages a fork copies at most; a longer conversation is refused, never cut. */
export const SESSION_FORK_MAX_MESSAGES = 5_000;
/** Artifact versions one copy request names (the query service's limit). */
export const SESSION_FORK_ARTIFACT_PAGE = 200;
