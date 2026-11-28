// The reader check as it was before answers stored their closures, with the rules added to it since (the switch read
// per chat, project visibility recorded, files saved before the rules, an asker's own answers given as text, and
// where others read the chat only the answers from before them that every member could read then; the agents a share
// stays with, the shares it counts on, shared copies outside their chat or taken out of it, what may be shared of an
// agent's work, and the people's messages of the room a share reaches), kept whole: it walks every earlier version level by level. The closure property test judges each case with both and asks
// for the same verdict.
/**
 * Who may read an answer: the reader check every channel applies to every row it returns.
 *
 * An answer is made from its asker's own access, and its label lists everything its model calls were given.
 * A reader sees the answer only when they can open all of it now: every record and container in the label by
 * the same access check search uses, nobody's personal tool results but their own, the agents it used, the
 * chat uploads and projects it named, and every earlier answer it was given, at the version it was given. An
 * answer its asker shared with them, at the version shared, shows them its text alone.
 *
 * Anything the check cannot settle hides the answer: a label that is missing, incomplete, of an unknown
 * version or no longer matching the stored text leaves it to its asker alone, and a check that cannot answer
 * hides it as unchecked. An answer written before labels existed keeps the per-record view readers of the
 * chat had before (`redactMessagesFor`) for those who could already read the chat when it was written; for
 * anyone added since, it is its asker's alone.
 *
 * The check runs once per reader per read: one access request for every id the page's labels name, at most
 * `READ_IDS_MAX` per request, with the rows' earlier versions read in a handful of queries.
 */
import {
  LABEL_UNKNOWN_VERSION,
  labelContentHash,
  labelProblems,
  labelStateOf,
  type LabelState,
} from '../../../../../../../src/modules/enterprise_search/services/conversation/privacy/label';
import {
  LEGACY_LABEL_VER,
  type AnswerProvenance,
  type LabelParent,
} from '../../../../../../../src/modules/enterprise_search/services/conversation/privacy/label.types';
import {
  READER_REV,
  VerdictStore,
  epochDigestOf,
  verdictKey,
  type StoredVerdict,
} from '../../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store';
import {
  kbEpochKey,
  principalEpochKey,
  readEpochs,
  type EpochSource,
} from '../../../../../../../src/modules/enterprise_search/services/conversation/privacy/epochs';
import {
  ANSWER_LOST,
  ANSWER_UNCONFIRMED,
  newReadable,
  readableRecordIds,
  redactMessagesFor,
  startsTurn,
  type Readable,
  type ViewerMessage,
} from '../../../../../../../src/modules/enterprise_search/utils/viewer-redaction';
import type { AppConfig } from '../../../../../../../src/modules/tokens_manager/config/config';
import { splitQuotedSelection } from '../../../../../../../src/modules/enterprise_search/utils/quoted-selection';
import { getConversationRepository } from '../../../../../../../src/libs/repositories/conversation';

export { READER_REV };

// ---- the contract -------------------------------------------------------------------------------------

export type Verdict = 'visible' | 'consented' | 'hidden' | 'unchecked';

export type HiddenReason =
  | 'sources'
  | 'personal'
  | 'parent'
  | 'agent'
  | 'label_stale'
  | 'label_unknown_version'
  | 'label_incomplete'
  | 'label_missing'
  | 'check_failed'
  | 'deleted';

export interface RowVerdict {
  verdict: Verdict;
  reason?: HiddenReason;
  /** Epoch milliseconds: until when the verdict may be relied on. */
  validUntil: number;
  /**
   * What a shown answer was shown by: its label (`label`), being the reader's own with no label to rely on
   * (`asker`), or having been written before labels for a reader of the chat from then (`before_labels`). The
   * last two still pass the per-record view.
   */
  basis?: 'label' | 'asker' | 'before_labels';
  /** A question that answers a card the reader may not see: the question shows, the card's words in it do not. */
  cardWithheld?: true;
}

/** The identity of a read made on someone's behalf: the share check asks what its sharer may use as them. */
export const ON_BEHALF_OF_READER: unique symbol = Symbol('on behalf of the reader');

/** Who is reading. */
export interface Reader {
  userId: string;
  orgId: string;
  /** A person with no account (a Slack user's stand-in): in no permission graph, but their own personal results are theirs. */
  servicePrincipal?: boolean;
  /** Reads the chat now (passed its read guard, or is the sender of a turn in it). Required for any consent to cover them. */
  member?: boolean;
  /** When the reader's current membership began, in `memberSinceOf`'s form; a share with them names it. */
  memberSince?: string;
  /** What the agent check calls out as (the inbound request's identity); without it an agent rule cannot be settled. */
  identity?: unknown;
}

/** A row as the check reads it. Label fields are read from the store when the row does not carry them. */
export interface ReaderRow {
  _id?: unknown;
  sessionId?: unknown;
  orgId?: unknown;
  seq?: unknown;
  messageType?: unknown;
  requestedBy?: unknown;
  authorUserId?: unknown;
  runId?: unknown;
  labelVer?: unknown;
  provenance?: unknown;
  labelHistory?: unknown;
  content?: unknown;
  parts?: unknown;
  createdAt?: unknown;
  consent?: unknown;
  consentTo?: unknown;
  sharedFrom?: unknown;
  respondingAgentKey?: unknown;
  removedAt?: unknown;
  removedFrom?: unknown;
  again?: unknown;
  replyTo?: unknown;
  inReplyTo?: unknown;
  /** A question that answers a card: the card it was bound to when it was sent. */
  answersCard?: unknown;
}

/** The chat being read: who owns it and who it is shared with. */
export interface ReaderSession {
  _id?: unknown;
  orgId?: unknown;
  userId?: unknown;
  sharedWith?: unknown;
  projectId?: unknown;
  projectVisibility?: unknown;
  agentKey?: unknown;
  parentId?: unknown;
  agentRoomId?: unknown;
  aclVersion?: unknown;
  audienceEpoch?: unknown;
  /** When the chat first took on the privacy rules as a group chat; absent on a chat no such build has served. */
  privacyStampedAt?: unknown;
  /** When the chat last became visible to its project; absent while it is not, and on a change no build recorded. */
  projectVisibleAt?: unknown;
}

/** What the access request said: the ids the reader may open, the ids that are deleted, and until when. */
export interface ReadableAnswer {
  readable: ReadonlySet<string>;
  /** Ids asked and settled, readable or not; anything else could not be checked. */
  asked: ReadonlySet<string>;
  /** Asked ids whose record is gone or deleted: not readable, and said apart for the asker. */
  deleted: ReadonlySet<string>;
  failed: boolean;
  validUntil: number;
}

export interface VisibilityDeps {
  /** One access request per reader for these ids; never throws (a failure is `failed`). */
  readable(
    reader: Reader,
    ids: readonly string[],
    timeoutMs: number,
    known?: Readable,
    appConfig?: AppConfig,
  ): Promise<ReadableAnswer>;
  /** Stored rows by id with their label fields, in any chat of the org; removed rows included. */
  labels(
    orgId: string,
    rowIds: readonly string[],
  ): Promise<ReadonlyMap<string, ReaderRow>>;
  /**
   * The answers and errors of these chats that one of `runIds` wrote, at their current version or an archived one, with
   * their label fields; removed rows included. A row of another run may come back; it is not taken.
   */
  runs(
    orgId: string,
    sessionIds: readonly string[],
    runIds: readonly string[],
  ): Promise<readonly ReaderRow[]>;
  /** Whether the reader may use an agent; undefined when it cannot be said now. */
  canUseAgent(reader: Reader, agentKey: string): Promise<boolean | undefined>;
  /** Whether the reader may read a chat; undefined when it cannot be said now. */
  canReadConversation(
    reader: Reader,
    conversationId: string,
  ): Promise<boolean | undefined>;
  /** Whether the reader may read a project, or `deleted` when it is gone or deleted; undefined when it cannot be said now. */
  canReadProject(
    reader: Reader,
    projectId: string,
  ): Promise<boolean | 'deleted' | undefined>;
  /** Whether an agent runs on a service account, bound or not; undefined when it cannot be said now. */
  isServiceAgent(reader: Reader, agentKey: string): Promise<boolean | undefined>;
  /** Whether an agent has a skill now that the reader can't open, by its own record; undefined when it can't be said. */
  hasClosedSkill(reader: Reader, agentKey: string): Promise<boolean | undefined>;
  /** Whether the reader owns an agent, by their own grant to it; undefined when it can't be said. */
  ownsAgent(reader: Reader, agentKey: string): Promise<boolean | undefined>;
  /** An answer's text as a share shows it: no record ids, links, text fragments or images. */
  sharedText(content: string): string;
  /**
   * When the privacy rules started (epoch ms): the time this build first served, as its boot stamp records it, or
   * undefined while that is not recorded. Who could read a chat before then reads its answers from before labels as
   * they always did.
   */
  switchAt(orgId: string): Promise<number | undefined>;
  /**
   * Whether the org's answers are judged by their labels. When not (the default), every reader keeps the per-record
   * view a shared chat has always had.
   */
  labelRule(orgId: string): Promise<boolean>;
  /** Where the permission epochs are; none means verdicts are never stored. */
  epochs?: EpochSource;
  /**
   * Each chat's audience epoch as stored now, by id: a share of any of its answers, and a share's end, move it on. A
   * chat left out has none. A read that fails leaves no verdict stored or served.
   */
  audiences(
    orgId: string,
    chatIds: readonly string[],
  ): Promise<ReadonlyMap<string, number>>;
  store: VerdictStore;
  now(): number;
  /**
   * Chats by id, of the org: for an answer from before labels that a label names and that sits in another chat
   * than the one read (a private thread's room), who owns it, who it is shared with and since when, and when it
   * took on the privacy rules. A chat left out is judged for no one but its rows' askers.
   */
  chats(
    orgId: string,
    ids: readonly string[],
  ): Promise<ReadonlyMap<string, ReaderSession>>;
  /** A chat's rows up to place `seq`, newest first, at most `limit`, with their sources: an earlier answer's turn. */
  rowsUpTo(
    orgId: string,
    sessionId: string,
    seq: number,
    limit: number,
  ): Promise<ReaderRow[]>;
  /** Rows as the per-record view gives them to the reader: an answer it withholds carries `answerHidden`. */
  perRecordView(
    reader: Reader,
    rows: readonly ReaderRow[],
    ownerId: string | undefined,
    opts: {
      known?: Readable;
      timeoutMs: number;
      leadUnknown: boolean;
      appConfig?: AppConfig;
    },
  ): Promise<ReaderRow[]>;
}

// ---- configuration -------------------------------------------------------------------------------------

/** The longest a verdict is relied on, whatever else allows, so the longest a revoked access may still show. */
export const VERDICT_TTL_MS = 30_000;
/** An unchecked verdict is retried this soon. */
export const UNCHECKED_TTL_MS = 5_000;
/** Ids one access request may carry. */
export const READ_IDS_MAX = 10_000;
/** Earlier versions followed, at most, behind any one row; a deeper chain hides. */
const MAX_PARENT_DEPTH = 32;
/** Rows read to judge one page, at most; past it, what is left hides. */
const MAX_JUDGED_ROWS = 2_000;
/** Rows read before the oldest answer from before labels a page builds on, so its turn's question is in the read. */
const LEGACY_TURN_LEAD = 20;
/** Rows of one chat read for those answers' turns, at most; an answer whose turn is past it stays unchecked. */
const LEGACY_ROWS_MAX = 400;
/**
 * A read's access check gives up after this, unless the channel sets its own: inside a detail read's 3 s,
 * leaving the read the time to load and send.
 */
export const DEFAULT_CHECK_TIMEOUT_MS = 2_500;

let appConfigOf: (() => AppConfig | undefined) | undefined;

const failedAnswer = (now: number): ReadableAnswer => ({
  readable: new Set(),
  asked: new Set(),
  deleted: new Set(),
  failed: true,
  validUntil: now + UNCHECKED_TTL_MS,
});

const defaultDeps = (): VisibilityDeps => ({
  readable: async (reader, ids, timeoutMs, known, given) => {
    const appConfig = given ?? appConfigOf?.();
    if (!appConfig) return failedAnswer(Date.now());
    const settled = known ?? newReadable();
    const out = await readableRecordIds(
      appConfig,
      reader,
      [...ids],
      [],
      settled,
      timeoutMs,
    );
    return readableFrom(out, ids);
  },
  labels: (orgId, rowIds) =>
    getConversationRepository().listMessageLabels(orgId, [...rowIds]),
  runs: (orgId, sessionIds, runIds) =>
    getConversationRepository().listMessagesOfRuns(
      orgId,
      [...sessionIds],
      [...runIds],
    ),
  canUseAgent: () => Promise.resolve(undefined),
  canReadConversation: () => Promise.resolve(undefined),
  canReadProject: () => Promise.resolve(undefined),
  isServiceAgent: () => Promise.resolve(undefined),
  hasClosedSkill: () => Promise.resolve(undefined),
  ownsAgent: () => Promise.resolve(undefined),
  sharedText: () => '',
  switchAt: () => Promise.resolve(undefined),
  labelRule: () => Promise.resolve(false),
  // The edition that keeps verdicts wires one read of every chat (`ChatAccessLoader.audienceEpochs`); without it,
  // nothing is stored.
  audiences: () =>
    Promise.reject(
      new Error('no audience epochs are wired: verdicts are not stored'),
    ),
  store: new VerdictStore(),
  now: Date.now,
  chats: async (orgId, ids) => {
    const repo = getConversationRepository();
    const out = new Map<string, ReaderSession>();
    await Promise.all(
      ids.map(async (id) => {
        const chat = await repo.findById(id);
        if (chat && idOf(chat.orgId) === orgId) {
          out.set(id, chat as unknown as ReaderSession);
        }
      }),
    );
    return out;
  },
  rowsUpTo: async (orgId, sessionId, seq, limit) =>
    (
      await getConversationRepository().listMessages(sessionId, {
        orgId,
        maxSeq: seq,
        order: 'desc',
        limit,
        withCitations: true,
      })
    ).map((row) => row.dto as unknown as ReaderRow),
  perRecordView: async (reader, rows, ownerId, opts) =>
    (await redactMessagesFor(
      (opts.appConfig ?? appConfigOf?.()) as AppConfig,
      reader,
      rows as unknown as ViewerMessage[],
      (m) => rowAuthorOf(m as ReaderRow, ownerId),
      {
        known: opts.known,
        timeoutMs: opts.timeoutMs,
        leadUnknown: opts.leadUnknown,
      },
    )) as unknown as ReaderRow[],
});

let configured: VisibilityDeps = defaultDeps();

/**
 * Wired once at start: the access request's endpoint, and the edition's agent, chat and project checks. A check
 * left unwired cannot be settled, so a rule that needs it hides.
 */
export function configureVisibility(
  next: Partial<VisibilityDeps> & { appConfig?: () => AppConfig | undefined },
): void {
  const { appConfig, ...deps } = next;
  if (appConfig) appConfigOf = appConfig;
  configured = { ...configured, ...deps };
}

/** Back to the unwired defaults (tests). */
export function resetVisibility(): void {
  appConfigOf = undefined;
  configured = defaultDeps();
}

export const visibilityDeps = (): VisibilityDeps => configured;

/** What `readableRecordIds` settled, as the check reads it. */
export const readableFrom = (
  out: Readable,
  ids: readonly string[],
): ReadableAnswer => {
  const asked = new Set<string>();
  for (const id of ids) if (out.asked.has(`i:${id}`)) asked.add(id);
  return {
    readable: out.ids,
    asked,
    deleted: out.deleted ?? new Set(),
    failed: out.failed === true && ids.some((id) => !asked.has(id)),
    validUntil: out.validUntil ?? Date.now() + VERDICT_TTL_MS,
  };
};

// ---- rows ------------------------------------------------------------------------------------------------

type Plain = Record<string, unknown>;

const isPlain = (value: unknown): value is Plain =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/** An id as a string: an ObjectId, a string, or undefined. */
export const idOf = (value: unknown): string | undefined => {
  if (value === null || value === undefined) return undefined;
  if (typeof value === 'string') return value === '' ? undefined : value;
  if (typeof value === 'object' && 'toString' in value) {
    const text = (value as { toString(): string }).toString();
    return text === '' || text === '[object Object]' ? undefined : text;
  }
  return undefined;
};

const LABELLED = new Set(['bot_response', 'error']);
const HUMAN = new Set(['user_query', 'note', 'system', 'feedback']);
const SHARED_ANSWER = 'shared_answer';

const isLabelledType = (row: ReaderRow): boolean =>
  typeof row.messageType === 'string' && LABELLED.has(row.messageType);

/** The run that wrote a row (stamped on every row of a turn that held a lease). */
const runOf = (row: ReaderRow): string | undefined =>
  typeof row.runId === 'string' && row.runId !== '' ? row.runId : undefined;

/** The card a question answers, as it was bound when sent. */
const answeredCardOf = (row: ReaderRow): string | undefined =>
  row.messageType === 'user_query' ? idOf(row.answersCard) : undefined;

/** A question in the form the app answers a card with, stored before the card was recorded with it. */
const isUnboundCardAnswer = (row: ReaderRow): boolean =>
  row.messageType === 'user_query' &&
  row.answersCard == null &&
  typeof row.content === 'string' &&
  row.content.trimStart().startsWith('User selections:');

/** An answer to an approval card, as the app stores it: its decision alone, bound to no card. */
const isApprovalAnswer = (row: ReaderRow): boolean =>
  row.messageType === 'user_query' &&
  typeof row.content === 'string' &&
  // eslint-disable-next-line no-control-regex -- Python's `str.lstrip()` strips these too
  /^[\s\x1c-\x1f\x85]*Tool approval:/u.test(row.content);

/** A question bound to the card it answers, or in the words the app answers a card with. */
const answersACard = (row: ReaderRow): boolean =>
  row.messageType === 'user_query' &&
  (row.answersCard != null ||
    (typeof row.content === 'string' &&
      // eslint-disable-next-line no-control-regex -- Python's `str.lstrip()` strips these too
      /^[\s\x1c-\x1f\x85]*(?:User selections|Tool approval):/u.test(row.content)));

/** What a reader who may not see a card gets of a question that answers it: that it was answered, never with what. */
export const CARD_ANSWER_WITHHELD = 'Answered a question from the assistant.';

const withheldCardAnswer = <T extends object>(row: T): T => {
  const view: Plain = { ...(row as Plain), content: CARD_ANSWER_WITHHELD };
  return view as T;
};

/**
 * A person's message every reader of `room` reads in full: a question or a note of that chat, not taken out, opening
 * with no quoted excerpt, and no answer to a card (bound to one, or opening as the AI backend reads one, after what
 * Python strips as space).
 */
const readByTheRoom = (row: ReaderRow, room: string | undefined): boolean => {
  if (row.messageType !== 'note' && row.messageType !== 'user_query') return false;
  if (room === undefined || idOf(row.sessionId) !== room || row.removedAt != null) return false;
  if (typeof row.content !== 'string' || splitQuotedSelection(row.content).quoted) return false;
  if (row.messageType === 'note') return true;
  // eslint-disable-next-line no-control-regex -- Python's `str.lstrip()` strips these too
  const opening = row.content.replace(/^[\s\x1c-\x1f\x85]+/u, '');
  return row.answersCard == null && !opening.startsWith('User selections:') && !opening.startsWith('Tool approval:');
};

const versionOf = (row: ReaderRow): string =>
  typeof row.labelVer === 'string' && row.labelVer !== ''
    ? row.labelVer
    : LEGACY_LABEL_VER;

/** The run that wrote the file a parent names, when the parent is a file that names its run. */
const writingRunOf = (parent: Plain): string | undefined =>
  parent.id === '' &&
  typeof parent.artifactId === 'string' &&
  parent.artifactId !== '' &&
  typeof parent.runId === 'string' &&
  parent.runId !== ''
    ? parent.runId
    : undefined;

/** A file saved before the rules that a parent names with no run: its chat, and when the version read was written. */
interface LegacyFile {
  chatId: string;
  artifactId: string;
  writtenAt: number;
}

/** The parent as such a file, when its record said which chat it belongs to and when that version was written. */
const legacyFileOf = (parent: unknown): LegacyFile | undefined => {
  if (
    !isPlain(parent) ||
    parent.id !== '' ||
    writingRunOf(parent) !== undefined
  )
    return undefined;
  const { artifactId, conversationId, writtenAt } = parent;
  return typeof artifactId === 'string' &&
    artifactId !== '' &&
    typeof conversationId === 'string' &&
    conversationId !== '' &&
    typeof writtenAt === 'number' &&
    writtenAt > 0
    ? { chatId: conversationId, artifactId, writtenAt }
    : undefined;
};

/** One key per file version: two versions of a file, written at different times, are judged apart. */
const legacyFileKey = (file: LegacyFile): string =>
  JSON.stringify([file.chatId, file.artifactId, file.writtenAt]);

/** Who a row is: the person who wrote a question or note, or who asked for an answer, card or error. */
export const rowAuthorOf = (
  row: ReaderRow,
  ownerId: string | undefined,
): string | undefined => {
  const by =
    row.messageType === 'user_query' ||
    row.messageType === 'note' ||
    row.messageType === SHARED_ANSWER
      ? (idOf(row.authorUserId) ?? idOf(row.requestedBy))
      : idOf(row.requestedBy);
  return by ?? ownerId;
};

// ---- membership and consent ------------------------------------------------------------------------------

interface ShareRow {
  userId?: unknown;
  teamId?: unknown;
  addedAt?: unknown;
}

const shareRowsOf = (session: ReaderSession | undefined): ShareRow[] =>
  Array.isArray(session?.sharedWith)
    ? (session.sharedWith as unknown[]).filter(isPlain)
    : [];

const timeOf = (value: unknown): number | undefined => {
  if (value instanceof Date) return value.getTime();
  if (typeof value === 'string' || typeof value === 'number') {
    const at = new Date(value).getTime();
    return Number.isNaN(at) ? undefined : at;
  }
  return undefined;
};

/**
 * When `userId`'s current membership of the chat began, as a share with them records it: `owner`, or
 * `direct:<when their own share row was added>`. Undefined when they reach the chat only through a team or
 * the project, where no one row says when: a share with such a person is never trusted to cover them.
 */
export function memberSinceOf(
  session: ReaderSession,
  userId: string,
): string | undefined {
  if (idOf(session.userId) === userId) return 'owner';
  const own = shareRowsOf(session).find((r) => idOf(r.userId) === userId);
  if (!own) return undefined;
  const at = timeOf(own.addedAt);
  return `direct:${at === undefined ? '' : new Date(at).toISOString()}`;
}

const sameMembership = (
  stored: unknown,
  current: string | undefined,
): boolean => {
  if (current === undefined) return false;
  if (typeof stored === 'string' && stored === current) return true;
  // A share that recorded the membership's start as a time names the reader's own share row.
  const at = timeOf(stored);
  if (at === undefined || !current.startsWith('direct:')) return false;
  return timeOf(current.slice('direct:'.length)) === at;
};

interface ConsentRecord {
  ver?: unknown;
  scope?: unknown;
  by?: unknown;
  agents?: unknown;
  on?: unknown;
}

/** A row's version named by a share as one it counts on; a copy has version ''. */
interface Counted {
  row: string;
  ver: string;
}

/** The agents and the earlier shares a share asks of a reader beside its consent; undefined when they can't be read. */
const asksOf = (consent: ConsentRecord | undefined): { agents: string[]; on: Counted[] } | undefined => {
  if (!consent) return undefined;
  const agents = consent.agents ?? [];
  const on = countedIn(consent.on);
  if (!Array.isArray(agents) || agents.some((a) => typeof a !== 'string' || a === '') || !on) return undefined;
  return { agents: agents as string[], on };
};

const countedIn = (value: unknown): Counted[] | undefined => {
  const list = value ?? [];
  if (!Array.isArray(list)) return undefined;
  const out: Counted[] = [];
  for (const entry of list as unknown[]) {
    const row = isPlain(entry) ? idOf(entry.row) : undefined;
    if (!isPlain(entry) || row === undefined || typeof entry.ver !== 'string') return undefined;
    out.push({ row, ver: entry.ver });
  }
  return out;
};

/** What a copy was made from: live, as it says; taken out, as it kept it. */
const copiedFrom = (copy: ReaderRow): Counted | undefined => {
  const from = copy.removedAt == null ? copy.sharedFrom : copy.removedFrom;
  if (!isPlain(from)) return undefined;
  const row = idOf(from.messageId);
  return row === undefined ? undefined : { row, ver: typeof from.ver === 'string' ? from.ver : '' };
};

const copyCountsOn = (copy: ReaderRow): Counted[] | undefined =>
  countedIn(isPlain(copy.sharedFrom) ? copy.sharedFrom.on : undefined);

/** The agent thread a message shows its room an answer of: what it counts on may be shared there. */
const madeInThread = (copy: ReaderRow): string | undefined => {
  const from = isPlain(copy.sharedFrom) ? copy.sharedFrom : {};
  return from.fromAgentThread === true ? idOf(from.sessionId) : undefined;
};

/** The consent recorded for `ver` of a row: on the row for its current version, else archived with that version. */
const consentAt = (row: ReaderRow, ver: string): ConsentRecord | undefined => {
  let consent: unknown;
  if (ver === versionOf(row)) {
    consent = row.consent;
  } else if (Array.isArray(row.labelHistory)) {
    const entry = (row.labelHistory as unknown[]).find(
      (e) => isPlain(e) && e.ver === ver,
    );
    consent = isPlain(entry) ? entry.consent : undefined;
  }
  if (!isPlain(consent) || consent.ver !== ver) return undefined;
  return consent;
};

/**
 * Whether `ver` of `row` was shared with `viewer`: shared with everyone in the chat, or with them by name while
 * their membership is the one it was shared under, and they are a member now. A share with people covers only
 * the version current when it was made. A share is for the chat it was given in: read in `chats` (the chat read,
 * its room, its agent room), a row of any other chat (an answer a fork was built on) is shared with nobody here. A
 * row read with no chat id is one of the chat read.
 *
 * This is the one place that decides which chats a share covers: the reads, a turn's history and what a fork copies
 * all ask it, with the chats they read in (`Judge.chatsRead`).
 */
export function consentCovers(
  row: ReaderRow,
  ver: string,
  viewer: Reader,
  chats?: ReadonlySet<string>,
): boolean {
  if (viewer.member !== true) return false;
  const where = idOf(row.sessionId);
  if (chats !== undefined && where !== undefined && !chats.has(where))
    return false;
  const consent = consentAt(row, ver);
  if (!consent) return false;
  const scope = consent.scope ?? 'everyone';
  if (scope === 'everyone') return true;
  if (scope !== 'people' || ver !== versionOf(row)) return false;
  const to = Array.isArray(row.consentTo) ? (row.consentTo as unknown[]) : [];
  return to.some(
    (entry) =>
      isPlain(entry) &&
      idOf(entry.userId) === viewer.userId &&
      sameMembership(entry.memberSince, viewer.memberSince),
  );
}

/** The reader as a member of `session`: their membership from their own share row, or as the caller says. */
const asMember = (
  reader: Reader,
  session: ReaderSession | undefined,
  member: boolean | undefined,
): Reader => {
  if (!session) return reader;
  const since = memberSinceOf(session, reader.userId);
  return {
    ...reader,
    member: reader.member ?? (since !== undefined || member === true),
    memberSince: reader.memberSince ?? since,
  };
};

/** A share row from before the switch: an undated row counts as before. */
const sharedBefore = (row: ShareRow, switchAt: number): boolean => {
  const added = timeOf(row.addedAt);
  return added === undefined || added < switchAt;
};

/**
 * When the privacy rules started for the chat: the earlier of its own stamp and the time this build first served
 * (`switchAt`, as the boot stamp recorded it). A chat stamped first (a replica served it before the boot stamp ran)
 * started then; one that became a group chat only later is stamped later, after the rules had started for it.
 */
const switchOf = (
  session: ReaderSession,
  switchAt: number | undefined,
): number | undefined => {
  const stamped = timeOf(session.privacyStampedAt);
  if (stamped === undefined) return switchAt;
  return switchAt === undefined ? stamped : Math.min(stamped, switchAt);
};

/**
 * Visible to its project since before the switch: no build with the rules recorded it becoming so. Only such
 * a build records it, so a recorded time is never before the switch; compared with the switch times a build can read
 * (a stamp, the boot stamp's record), all written once it serves, a change made before them would pass as earlier.
 */
const projectVisibleBefore = (session: ReaderSession): boolean =>
  session.projectVisibility === 'project' &&
  (session.projectVisibleAt === undefined || session.projectVisibleAt === null);

/**
 * Whether the reader could already read the chat before the privacy rules started for it, so reads its answers from
 * before then as they always did: the owner; someone whose own share row is older than that (or carries no
 * date); with no row of their own, someone the chat reaches through a project it was visible to before then, or
 * through teams and whole-company rows all older than that. While nothing says when the rules started, every
 * member is.
 */
const readBeforeSwitch = (
  reader: Reader,
  session: ReaderSession | undefined,
  switchAt: number | undefined,
): boolean => {
  if (!session || reader.member !== true) return false;
  if (idOf(session.userId) === reader.userId) return true;
  const at = switchOf(session, switchAt);
  if (at === undefined) return true;
  const before = (r: ShareRow): boolean => sharedBefore(r, at);
  const rows = shareRowsOf(session);
  const own = rows.find((r) => idOf(r.userId) === reader.userId);
  if (own) return before(own);
  if (projectVisibleBefore(session)) return true;
  const others = rows.filter((r) => idOf(r.userId) === undefined);
  return others.length > 0 && others.every(before);
};

/**
 * Whether a row with no label was written after its chat took on the privacy rules. A build with the rules labels
 * every answer it writes, so such a row comes from a build without them (an older replica during a deploy) and is
 * not an answer from before labels. Its write time, else the second its id was made in (a row of the stamp's own
 * second counts as after); a row whose time cannot be read is not taken to be from before.
 */
const unlabelledUnderRules = (
  row: ReaderRow,
  session: ReaderSession | undefined,
): boolean => {
  const stamped = timeOf(session?.privacyStampedAt);
  if (stamped === undefined) return false;
  const written = timeOf(row.createdAt);
  if (written !== undefined) return written >= stamped;
  const id = idOf(row._id);
  if (id === undefined || !/^[0-9a-f]{24}$/i.test(id)) return true;
  return parseInt(id.slice(0, 8), 16) >= Math.floor(stamped / 1000);
};

// ---- labels ----------------------------------------------------------------------------------------------

const isLabel = (value: unknown): value is AnswerProvenance => isPlain(value);

interface Version {
  /** The label of that version, when it has one. */
  label?: AnswerProvenance;
  state: LabelState;
}

/** A label read as a parent's: its version as linked, with no check of the stored text. */
const parentStateOf = (label: unknown, ver: string): LabelState => {
  if (!isPlain(label)) return 'unknown_version';
  const problems = labelProblems(label);
  if (problems.some((p) => p.startsWith(LABEL_UNKNOWN_VERSION))) {
    return 'unknown_version';
  }
  if (problems.length > 0) return 'incomplete';
  if (label.ver !== ver) return 'stale';
  const reasons = label.incomplete;
  if (
    label.complete !== true ||
    (Array.isArray(reasons) && reasons.length > 0)
  ) {
    return 'incomplete';
  }
  return 'ok';
};

/** `ver` of a row: its current label (with the stored-text check) or an archived one. */
const versionAt = (row: ReaderRow, ver: string, own: boolean): Version => {
  if (own) {
    const state = labelStateOf(row);
    return {
      state,
      ...(state === 'ok' && { label: row.provenance as AnswerProvenance }),
    };
  }
  const current = versionOf(row);
  if (ver === LEGACY_LABEL_VER) {
    const archived =
      Array.isArray(row.labelHistory) &&
      (row.labelHistory as unknown[]).some(
        (e) => isPlain(e) && e.ver === LEGACY_LABEL_VER,
      );
    return {
      state: current === LEGACY_LABEL_VER || archived ? 'legacy' : 'missing',
    };
  }
  let label: unknown;
  if (ver === current) {
    label = row.provenance;
  } else if (Array.isArray(row.labelHistory)) {
    const entry = (row.labelHistory as unknown[]).find(
      (e) => isPlain(e) && e.ver === ver,
    );
    label = isPlain(entry) ? entry.provenance : undefined;
  }
  if (label === undefined || label === null) return { state: 'missing' };
  const state = parentStateOf(label, ver);
  return { state, ...(state === 'ok' && isLabel(label) && { label }) };
};

const HIDDEN_BY_STATE: Record<Exclude<LabelState, 'ok'>, HiddenReason> = {
  legacy: 'label_missing',
  missing: 'label_missing',
  unknown_version: 'label_unknown_version',
  stale: 'label_stale',
  incomplete: 'label_incomplete',
};

const listOf = <T>(value: unknown): T[] =>
  Array.isArray(value) ? (value as T[]) : [];

// ---- the judge -------------------------------------------------------------------------------------------

/** A settled outcome, or the reason it hides. */
type Outcome = 'pass' | 'unchecked' | HiddenReason;

/** Which reason a row hides for when several apply: the first settled denial, else unchecked. */
const PRIORITY: readonly HiddenReason[] = [
  'personal',
  'agent',
  'sources',
  'deleted',
  'parent',
  'label_unknown_version',
  'label_stale',
  'label_incomplete',
  'label_missing',
];

const combine = (outcomes: readonly Outcome[]): Outcome => {
  for (const reason of PRIORITY) if (outcomes.includes(reason)) return reason;
  if (outcomes.includes('unchecked') || outcomes.includes('check_failed')) {
    return 'unchecked';
  }
  return 'pass';
};

interface JudgeOptions {
  /** When the privacy rules started (`VisibilityDeps.switchAt`); read before judging any row from before labels. */
  switchAt?: number;
  /** Where the access request goes, when no wiring at start said. */
  appConfig?: AppConfig;
  /** Agents the caller already knows the reader may use. */
  usableAgents?: readonly string[];
  session?: ReaderSession;
  /** Whose rows with no author or asker stored are: the chat's owner when they were written. */
  ownerId?: string;
  timeoutMs?: number;
  known?: Readable;
  extraIds?: readonly string[];
  labelsLoaded?: boolean;
  /** The chats the read happens in, when not the session's (`VisibleRowsOptions.chatsRead`). */
  chatsRead?: readonly string[];
  /** A share's check: every original of a copy is read, and these agents are asked about with those that answered. */
  sharing?: readonly string[];
}

/**
 * One reader's judgement of a set of rows: the rows and every earlier version they were given are read first,
 * then every id is asked in one request, then each row is judged from what was read.
 */
class Judge {
  readonly rows = new Map<string, ReaderRow>();
  private readonly memo = new Map<string, Outcome>();
  private readonly inProgress = new Set<string>();
  private readable: ReadableAnswer;
  private readonly agentUse = new Map<string, boolean | undefined>();
  private readonly chats = new Map<string, boolean | undefined>();
  private readonly projects = new Map<string, boolean | 'deleted' | undefined>();
  /** Rows whose earlier versions were not all read (too deep or too many): they hide. */
  private readonly cut = new Set<string>();
  /** For each run a file parent names, the row versions it wrote in the chats read; empty when none was found. */
  private readonly writers = new Map<string, Array<{ id: string; ver: string }>>();
  private readonly sessionIds: Set<string>;
  private readonly sharesRead: ReadonlySet<string> | undefined;
  /** The chats whose uploads a member reads without a check of that chat: those this read happens in. */
  private readonly uploadChats: ReadonlySet<string>;
  /** Answers from before labels named as `text` parents of someone else's asking, by id: how each fares for the reader. */
  private readonly legacyText = new Map<string, Outcome>();
  /** Files written before the rules, named with no run, by artifact id: how each fares for the reader. */
  private readonly legacyFiles = new Map<string, Outcome>();

  constructor(
    readonly reader: Reader,
    private readonly deps: VisibilityDeps,
    private readonly opts: JudgeOptions,
  ) {
    this.readable = failedAnswer(deps.now());
    const s = opts.session;
    this.sessionIds = new Set(
      [idOf(s?._id), idOf(s?.parentId), idOf(s?.agentRoomId)].filter(
        (id): id is string => id !== undefined,
      ),
    );
    this.sharesRead =
      opts.chatsRead !== undefined
        ? new Set(opts.chatsRead)
        : idOf(s?._id) === undefined
          ? undefined
          : this.sessionIds;
    this.uploadChats =
      opts.chatsRead !== undefined ? new Set(opts.chatsRead) : this.sessionIds;
  }

  get validUntil(): number {
    return Math.min(this.readable.validUntil, this.deps.now() + VERDICT_TTL_MS);
  }

  get checkFailed(): boolean {
    return this.readable.failed;
  }

  /**
   * The chats this read happens in, for `consentCovers`: those the caller names, else the session's own (the chat
   * read, its room, its agent room). None named (a read of several chats) leaves every share as it is.
   */
  get chatsRead(): ReadonlySet<string> | undefined {
    return this.sharesRead;
  }

  private ownerOfSession(row: ReaderRow): string | undefined {
    const s = this.opts.session;
    if (!s) return undefined;
    const rowSession = idOf(row.sessionId);
    const sessionId = idOf(s._id);
    // A row read without its chat id is one of the chat being read; a chat given without an id stands for every
    // chat of the rows (all of one owner's, as a search of their own chats reads them).
    if (
      rowSession !== undefined &&
      sessionId !== undefined &&
      rowSession !== sessionId
    ) {
      return undefined;
    }
    return this.opts.ownerId ?? idOf(s.userId);
  }

  askerOf(row: ReaderRow): string | undefined {
    return idOf(row.requestedBy) ?? this.ownerOfSession(row);
  }

  /** Reads `rows` (and their label fields when they do not carry them), every version behind them, and every id. */
  async prepare(rows: readonly ReaderRow[]): Promise<void> {
    await this.settle(await this.loadTree(rows));
  }

  /** Reads `rows` with their label fields and every version behind them; returns the labels read. */
  async loadTree(rows: readonly ReaderRow[]): Promise<AnswerProvenance[]> {
    const orgId = this.reader.orgId;
    const toLoad: string[] = [];
    for (const row of rows) {
      const id = idOf(row._id);
      if (id === undefined) continue;
      this.rows.set(id, row);
      if (isLabelledType(row) && this.opts.labelsLoaded !== true)
        toLoad.push(id);
    }
    if (toLoad.length > 0) {
      const loaded = await this.load(orgId, toLoad);
      for (const id of toLoad) {
        const stored = loaded.get(id);
        const given = this.rows.get(id) as ReaderRow;
        // The label, and the text it is checked against, are the stored ones, never what a caller passes in: a
        // read may have reshaped the row it shows.
        this.rows.set(
          id,
          stored
            ? {
                ...given,
                content: stored.content,
                parts: stored.parts,
                labelVer: stored.labelVer,
                provenance: stored.provenance,
                labelHistory: stored.labelHistory,
                consent: stored.consent,
                consentTo: stored.consentTo,
                ...(stored.requestedBy !== undefined && {
                  requestedBy: stored.requestedBy,
                }),
                ...(stored.respondingAgentKey !== undefined && {
                  respondingAgentKey: stored.respondingAgentKey,
                }),
              }
            : { ...given, provenance: undefined, labelHistory: undefined },
        );
      }
    }
    // Every earlier version the rows were given, a level at a time.
    let frontier: Array<{ id: string; ver: string; own: boolean }> = [];
    for (const row of rows) {
      const id = idOf(row._id);
      if (id !== undefined && (isLabelledType(row) || row.messageType === SHARED_ANSWER)) {
        frontier.push({
          id,
          ver: versionOf(this.rows.get(id) as ReaderRow),
          own: true,
        });
      }
    }
    const labels: AnswerProvenance[] = [];
    const seen = new Set<string>();
    for (let depth = 0; frontier.length > 0; depth += 1) {
      const next: Array<{ id: string; ver: string; own: boolean }> = [];
      const unread: string[] = [];
      const runs = new Set<string>();
      for (const { id, ver, own } of frontier) {
        const key = `${id}@${ver}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const row = this.rows.get(id);
        if (!row) continue;
        // Beside its label: what its share counts on; for a copy, what stands for it and what it was made from.
        if (depth + 1 < MAX_PARENT_DEPTH && this.rows.size < MAX_JUDGED_ROWS) {
          for (const ref of this.besides(row, ver)) {
            next.push({ id: ref.row, ver: ref.ver, own: false });
            if (!this.rows.has(ref.row)) unread.push(ref.row);
          }
        }
        if (row.messageType === SHARED_ANSWER) continue;
        const { label } = versionAt(row, ver, own);
        if (!label) continue;
        labels.push(label);
        for (const parent of listOf<LabelParent>(label.parents)) {
          if (!isPlain(parent) || typeof parent.id !== 'string') continue;
          if (parent.id === '') {
            // A file the run read: read below by the run that wrote it, at the version that run wrote.
            const runId = writingRunOf(parent);
            if (runId === undefined || this.writers.has(runId)) continue;
            if (
              depth + 1 >= MAX_PARENT_DEPTH ||
              this.rows.size >= MAX_JUDGED_ROWS
            ) {
              this.cut.add(`${id}@${ver}`);
              continue;
            }
            runs.add(runId);
            continue;
          }
          if (
            depth + 1 >= MAX_PARENT_DEPTH ||
            this.rows.size >= MAX_JUDGED_ROWS
          ) {
            this.cut.add(`${id}@${ver}`);
            continue;
          }
          next.push({ id: parent.id, ver: parent.ver, own: false });
          if (!this.rows.has(parent.id)) unread.push(parent.id);
        }
      }
      if (unread.length > 0) {
        const loaded = await this.load(orgId, [...new Set(unread)]);
        for (const [id, row] of loaded) this.rows.set(id, row);
      }
      if (runs.size > 0) {
        next.push(...(await this.loadWriters(orgId, [...runs], rows)));
      }
      frontier = next;
    }
    return labels;
  }

  /**
   * The row version each run wrote, read by run from the chats being read (this chat, its room, the rows' own chats).
   * A run found in none of them, or a read that fails, leaves its file parent unresolved: it hides.
   */
  private async loadWriters(
    orgId: string,
    runIds: readonly string[],
    given: readonly ReaderRow[],
  ): Promise<Array<{ id: string; ver: string; own: boolean }>> {
    for (const runId of runIds) this.writers.set(runId, []);
    const sessions = new Set(this.sessionIds);
    for (const row of given) {
      const sessionId = idOf(row.sessionId);
      if (sessionId !== undefined) sessions.add(sessionId);
    }
    if (sessions.size === 0) return [];
    let found: readonly ReaderRow[];
    try {
      found = await this.deps.runs(orgId, [...sessions], runIds);
    } catch {
      return [];
    }
    const asked = new Set(runIds);
    const out: Array<{ id: string; ver: string; own: boolean }> = [];
    for (const row of found) {
      const id = idOf(row._id);
      if (
        id === undefined ||
        !isLabelledType(row) ||
        !sessions.has(idOf(row.sessionId) ?? '')
      )
        continue;
      // Each version of the row a run asked for wrote: the current one, or one archived on a regenerate or an edit.
      const wrote: Array<{ runId: string; ver: string }> = [];
      if (typeof row.runId === 'string' && asked.has(row.runId)) {
        wrote.push({ runId: row.runId, ver: versionOf(row) });
      }
      for (const entry of listOf<unknown>(row.labelHistory)) {
        if (
          isPlain(entry) &&
          typeof entry.runId === 'string' &&
          asked.has(entry.runId) &&
          typeof entry.ver === 'string' &&
          entry.ver !== ''
        ) {
          wrote.push({ runId: entry.runId, ver: entry.ver });
        }
      }
      if (wrote.length === 0) continue;
      if (!this.rows.has(id)) this.rows.set(id, row);
      for (const { runId, ver } of wrote) {
        this.writers.get(runId)?.push({ id, ver });
        out.push({ id, ver, own: false });
      }
    }
    return out;
  }

  /**
   * Whether `ver` of `row` restricts nobody: a complete label naming no record, container, person,
   * agent, upload or project, with no mark this build can vouch for, built only on versions that restrict nobody
   * either. A row shared on consent never does, nor one whose versions were not all read.
   */
  pubAt(
    row: ReaderRow,
    ver: string,
    own: boolean,
    memo = new Map<string, boolean>(),
  ): boolean {
    const key = `${idOf(row._id) ?? ''}@${ver}`;
    const known = memo.get(key);
    if (known !== undefined) return known;
    // Until it is decided, a version met again on its own walk is a loop: not public.
    memo.set(key, false);
    const pub = this.pubOnce(row, ver, own, key, memo);
    memo.set(key, pub);
    return pub;
  }

  private pubOnce(
    row: ReaderRow,
    ver: string,
    own: boolean,
    key: string,
    memo: Map<string, boolean>,
  ): boolean {
    if (this.cut.has(key) || row.consent != null) return false;
    const { state, label } = versionAt(row, ver, own);
    if (state !== 'ok' || !label || !label.complete) return false;
    if (listOf(label.incomplete).length > 0) return false;
    const direct: Plain = isPlain(label.direct) ? label.direct : {};
    // Marks too: no store says which attestations still hold, so none is taken as valid.
    if (Object.values(direct).some((ids) => listOf(ids).length > 0)) {
      return false;
    }
    const parents = listOf<unknown>(label.parents);
    // Built on no other row, the label's own word counts too: its writer saw the run, this check sees the label.
    if (parents.length === 0 && !label.pub) return false;
    return parents.every((parent) => {
      if (!isPlain(parent) || typeof parent.id !== 'string') return false;
      // What reached the run through someone else's share, or an artifact, was never the sender's to send.
      if (parent.mode === 'grant' || parent.mode === 'preswitch') return false;
      if (typeof parent.artifactId === 'string' && parent.artifactId !== '')
        return false;
      const stored = this.rows.get(parent.id);
      return (
        stored !== undefined &&
        typeof parent.ver === 'string' &&
        this.pubAt(stored, parent.ver, false, memo)
      );
    });
  }

  private async load(
    orgId: string,
    ids: readonly string[],
  ): Promise<ReadonlyMap<string, ReaderRow>> {
    try {
      return await this.deps.labels(orgId, ids);
    } catch {
      // Unread labels are missing labels: every row read this way is its asker's alone.
      return new Map();
    }
  }

  /** One access request for every id the labels name, and the agent, chat and project checks they need. */
  /** The chats the copies read were made in; undefined when they could not be read. */
  private madeIn: ReadonlyMap<string, ReaderSession> | undefined = new Map();

  private async settle(labels: readonly AnswerProvenance[]): Promise<void> {
    const ids = new Set<string>(this.opts.extraIds ?? []);
    const agents = new Set<string>(this.opts.sharing ?? []);
    const chats = new Set<string>();
    const projects = new Set<string>();
    const madeIn = new Set<string>();
    for (const row of this.rows.values()) {
      const from = row.messageType === SHARED_ANSWER ? copiedFrom(row) : undefined;
      const chat = from && idOf(this.rows.get(from.row)?.sessionId);
      if (chat !== undefined) madeIn.add(chat);
    }
    if (madeIn.size > 0)
      this.madeIn = await this.deps.chats(this.reader.orgId, [...madeIn]).catch(() => undefined);
    for (const thread of this.madeIn?.values() ?? []) {
      const agent = idOf(thread.agentKey);
      const room = idOf(thread.agentRoomId);
      if (room === undefined) continue;
      if (agent !== undefined) agents.add(agent);
      if (!this.uploadChats.has(room)) chats.add(room);
    }
    for (const row of this.rows.values()) {
      const vers = [versionOf(row), ...listOf<unknown>(row.labelHistory).flatMap((e) => (isPlain(e) && typeof e.ver === 'string' ? [e.ver] : []))];
      for (const ver of vers) for (const key of asksOf(consentAt(row, ver))?.agents ?? []) agents.add(key);
      const responder = idOf(row.respondingAgentKey);
      if (this.opts.sharing && responder !== undefined) agents.add(responder);
    }
    for (const label of labels) {
      const direct: Plain = isPlain(label.direct) ? label.direct : {};
      for (const id of listOf<string>(direct.records))
        if (typeof id === 'string') ids.add(id);
      for (const id of listOf<string>(direct.nodes))
        if (typeof id === 'string') ids.add(id);
      for (const a of listOf<unknown>(direct.agents))
        if (isPlain(a) && typeof a.key === 'string') agents.add(a.key);
      for (const c of listOf<unknown>(direct.chat)) {
        const cid =
          isPlain(c) && typeof c.conversationId === 'string'
            ? c.conversationId
            : '';
        if (cid !== '' && !this.uploadChats.has(cid)) chats.add(cid);
      }
      for (const p of listOf<string>(direct.projects))
        if (typeof p === 'string') projects.add(p);
    }
    const timeoutMs = this.opts.timeoutMs ?? DEFAULT_CHECK_TIMEOUT_MS;
    const quietly = <T>(work: Promise<T>): Promise<T | undefined> =>
      work.catch(() => undefined);
    const [readable] = await Promise.all([
      this.readAll([...ids], timeoutMs),
      this.settleLegacyText(labels, timeoutMs),
      this.settleLegacyFiles(labels),
      ...[...agents].map(async (key) =>
        this.opts.usableAgents?.includes(key) === true
          ? this.agentUse.set(key, true)
          : this.agentUse.set(
              key,
              await quietly(this.deps.canUseAgent(this.reader, key)),
            ),
      ),
      ...[...chats].map(async (cid) =>
        this.chats.set(
          cid,
          await quietly(this.deps.canReadConversation(this.reader, cid)),
        ),
      ),
      ...[...projects].map(async (pid) =>
        this.projects.set(
          pid,
          await quietly(this.deps.canReadProject(this.reader, pid)),
        ),
      ),
    ]);
    this.readable = readable;
  }

  /**
   * The answers from before labels that the labels read here name as `text` parents of someone else's asking. One
   * passes for a reader who could read its chat before the rules started, when the per-record view of its turn still
   * shows it to them, exactly as their own read of it would. Their chats and turns are read
   * once per chat, and their records asked within the read's own deadline. What cannot be read leaves the parent
   * unchecked; a row written under the rules, a reader added since, or a turn the view withholds hides it.
   */
  private async settleLegacyText(
    labels: readonly AnswerProvenance[],
    timeoutMs: number,
  ): Promise<void> {
    const wanted = new Map<string, ReaderRow>();
    for (const label of labels) {
      for (const parent of listOf<LabelParent>(label.parents)) {
        if (!isPlain(parent) || parent.mode !== 'text') continue;
        if (parent.ver !== LEGACY_LABEL_VER || typeof parent.id !== 'string')
          continue;
        const row = this.rows.get(parent.id);
        // Its asker reads it as ever. A row labelled since holds another version's text and sources, so the
        // version given cannot be checked: it is judged as before, for its asker alone.
        if (
          !row ||
          !isLabelledType(row) ||
          versionOf(row) !== LEGACY_LABEL_VER ||
          this.askerOf(row) === this.reader.userId
        )
          continue;
        wanted.set(parent.id, row);
      }
    }
    if (wanted.size === 0) return;
    const orgId = this.reader.orgId;
    const readId = idOf(this.opts.session?._id);
    const bySession = new Map<
      string,
      Array<{ id: string; row: ReaderRow; seq: number }>
    >();
    for (const [id, row] of wanted) {
      const sessionId = idOf(row.sessionId) ?? readId;
      if (sessionId === undefined || typeof row.seq !== 'number') {
        this.legacyText.set(id, 'parent');
        continue;
      }
      const list = bySession.get(sessionId) ?? [];
      list.push({ id, row, seq: row.seq });
      bySession.set(sessionId, list);
    }
    const others = [...bySession.keys()].filter((id) => id !== readId);
    const [chats, switchAt] = await Promise.all([
      others.length > 0
        ? this.deps.chats(orgId, others).catch(() => undefined)
        : Promise.resolve(new Map<string, ReaderSession>()),
      // A failed read of when the rules started leaves only the owner and undated share rows as earlier readers.
      this.deps.switchAt(orgId).catch(() => 0),
    ]);
    await Promise.all(
      [...bySession].map(async ([sessionId, parents]) => {
        const same = sessionId === readId;
        const chat = same ? this.opts.session : chats?.get(sessionId);
        if (!chat) {
          for (const p of parents)
            this.legacyText.set(p.id, chats ? 'parent' : 'unchecked');
          return;
        }
        const seen = parents.filter((p) => {
          const before =
            !unlabelledUnderRules(p.row, chat) &&
            this.couldReadBefore(chat, same, switchAt);
          if (!before) this.legacyText.set(p.id, 'parent');
          return before;
        });
        if (seen.length === 0) return;
        const top = Math.max(...seen.map((p) => p.seq));
        const bottom = Math.min(...seen.map((p) => p.seq));
        const limit = Math.min(
          top - bottom + 1 + LEGACY_TURN_LEAD,
          LEGACY_ROWS_MAX,
        );
        try {
          const rows = [
            ...(await this.deps.rowsUpTo(orgId, sessionId, top, limit)),
          ].reverse();
          const shown = await this.deps.perRecordView(
            this.reader,
            rows,
            idOf(chat.userId),
            {
              known: this.opts.known,
              timeoutMs,
              // The read may begin inside a turn whose question it left out: that turn cannot be judged.
              leadUnknown: rows.length >= limit,
              appConfig: this.opts.appConfig,
            },
          );
          const byId = new Map(shown.map((m) => [idOf(m._id), m]));
          for (const p of seen) {
            const view = byId.get(p.id) as Plain | undefined;
            const hidden = view?.answerHidden;
            this.legacyText.set(
              p.id,
              view === undefined
                ? 'unchecked'
                : !isPlain(hidden)
                  ? 'pass'
                  : hidden.reason === ANSWER_UNCONFIRMED.reason
                    ? 'unchecked'
                    : 'parent',
            );
          }
        } catch {
          for (const p of seen) this.legacyText.set(p.id, 'unchecked');
        }
      }),
    );
  }

  /**
   * The files the labels read here name with no run, whose record says which chat they belong to and when they were
   * last written: one written before the rules started for its chat is judged as an
   * answer from then, and passes for a reader who could read that chat before them. A file named with no run and
   * without both is its asker's alone.
   */
  private async settleLegacyFiles(
    labels: readonly AnswerProvenance[],
  ): Promise<void> {
    const wanted = new Map<string, LegacyFile>();
    for (const label of labels) {
      for (const parent of listOf<LabelParent>(label.parents)) {
        const file = legacyFileOf(parent);
        if (file) wanted.set(legacyFileKey(file), file);
      }
    }
    if (wanted.size === 0) return;
    const orgId = this.reader.orgId;
    const readId = idOf(this.opts.session?._id);
    const others = [
      ...new Set([...wanted.values()].map((w) => w.chatId)),
    ].filter((id) => id !== readId);
    const [chats, switchAt] = await Promise.all([
      others.length > 0
        ? this.deps.chats(orgId, others).catch(() => undefined)
        : Promise.resolve(new Map<string, ReaderSession>()),
      // A failed read of when the rules started leaves only the owner and undated share rows as earlier readers.
      this.deps.switchAt(orgId).catch(() => 0),
    ]);
    for (const [key, { chatId, writtenAt }] of wanted) {
      const same = chatId === readId;
      const chat = same ? this.opts.session : chats?.get(chatId);
      if (!chat) {
        this.legacyFiles.set(key, chats ? 'parent' : 'unchecked');
        continue;
      }
      const at = switchOf(chat, switchAt);
      const before = at === undefined || writtenAt < at;
      this.legacyFiles.set(
        key,
        before && this.couldReadBefore(chat, same, switchAt)
          ? 'pass'
          : 'parent',
      );
    }
  }

  /**
   * Whether the reader could read `chat` before the rules started for it. The chat being read answers by its members.
   * Another chat (a private thread's room) answers only by what its own rows say: its owner, or a share row of the
   * reader's own.
   */
  private couldReadBefore(
    chat: ReaderSession,
    same: boolean,
    switchAt: number | undefined,
  ): boolean {
    if (same) return readBeforeSwitch(this.reader, chat, switchAt);
    const userId = this.reader.userId;
    if (idOf(chat.userId) === userId) return true;
    const own = shareRowsOf(chat).find((r) => idOf(r.userId) === userId);
    const at = switchOf(chat, switchAt);
    return own !== undefined && (at === undefined || sharedBefore(own, at));
  }

  private async readAll(
    ids: string[],
    timeoutMs: number,
  ): Promise<ReadableAnswer> {
    const now = this.deps.now();
    if (ids.length === 0) {
      return {
        readable: new Set(),
        asked: new Set(),
        deleted: new Set(),
        failed: false,
        validUntil: now + VERDICT_TTL_MS,
      };
    }
    const readable = new Set<string>();
    const asked = new Set<string>();
    const deleted = new Set<string>();
    let failed = false;
    let validUntil = now + VERDICT_TTL_MS;
    for (let i = 0; i < ids.length; i += READ_IDS_MAX) {
      const chunk = ids.slice(i, i + READ_IDS_MAX);
      let answer: ReadableAnswer;
      try {
        answer = await this.deps.readable(
          this.reader,
          chunk,
          timeoutMs,
          this.opts.known,
          this.opts.appConfig,
        );
      } catch {
        answer = failedAnswer(now);
      }
      for (const id of answer.readable) readable.add(id);
      for (const id of answer.asked) asked.add(id);
      for (const id of answer.deleted) deleted.add(id);
      failed = failed || answer.failed;
      validUntil = Math.min(validUntil, answer.validUntil);
    }
    return { readable, asked, deleted, failed, validUntil };
  }

  /** How `ver` of `row` fares for the reader; `own` judges its current text, else it is a version a later row was given. */
  judge(row: ReaderRow, ver: string, own: boolean): Outcome {
    const id = idOf(row._id) ?? '';
    const key = `${id}@${ver}:${own ? 'own' : 'parent'}`;
    const memo = this.memo.get(key);
    if (memo !== undefined) return memo;
    if (this.inProgress.has(key)) return 'parent';
    this.inProgress.add(key);
    const outcome = this.judgeVersion(row, ver, own);
    this.inProgress.delete(key);
    this.memo.set(key, outcome);
    return outcome;
  }

  private readonly deciding = new Set<string>();

  /** Run once at a time for a share: met again on its own way, it does not hold. */
  private once(key: string, decide: () => boolean): boolean {
    if (this.deciding.has(key)) return false;
    this.deciding.add(key);
    const held = decide();
    this.deciding.delete(key);
    return held;
  }

  /** Whether a share covers the reader: its consent, every agent it stays with, every share it counts on. */
  covered(row: ReaderRow, ver: string, thread?: string): boolean {
    const there = thread !== undefined && idOf(row.sessionId) === thread;
    if (!consentCovers(row, ver, this.reader, there ? undefined : this.chatsRead)) return false;
    const asks = asksOf(consentAt(row, ver));
    if (!asks || asks.agents.some((key) => this.agentUse.get(key) !== true)) return false;
    return this.once(`share ${String(row._id)}@${ver} ${thread ?? ''}`, () => asks.on.every((ref) => this.holds(ref, thread)));
  }

  mayUse(key: string): boolean {
    return this.agentUse.get(key) === true;
  }

  /** The versions whose runs wrote the file a parent names. */
  wrote(parent: Plain): Counted[] {
    const runId = writingRunOf(parent);
    return (runId === undefined ? [] : this.writers.get(runId) ?? []).map(({ id, ver }) => ({ row: id, ver }));
  }

  private holds(ref: Counted, thread?: string): boolean {
    const row = this.rows.get(ref.row);
    if (!row) return false;
    if (row.messageType === SHARED_ANSWER) return this.copyOutcome(row) === 'pass';
    return this.covered(row, ref.ver, thread) || this.judge(row, ref.ver, false) === 'pass';
  }

  /** A live copy shows its text to a member while each share it counts on holds for them. */
  copyShows(copy: ReaderRow): boolean {
    const on = copyCountsOn(copy);
    if (copy.removedAt != null || this.reader.member !== true || !on) return false;
    return this.once(`copy ${String(copy._id)}`, () => on.every((ref) => this.holds(ref, madeInThread(copy))));
  }

  /**
   * The live copy that stands for `copy`: itself, or the end of the copies of the same answer that took its place. Where a
   * version was shared again, only for a copy of that version (`first`).
   */
  standing(copy: ReaderRow, met: readonly string[] = [], first = copiedFrom(copy)?.ver): ReaderRow | undefined {
    if (copy.removedAt == null) return copy;
    const again = idOf(copy.again);
    const next = again === undefined || met.includes(again) ? undefined : this.rows.get(again);
    if (again === undefined || next?.messageType !== SHARED_ANSWER) return undefined;
    const same = copiedFrom(copy)?.row;
    if (same === undefined || copiedFrom(next)?.row !== same || idOf(next.sessionId) !== idOf(copy.sessionId)) return undefined;
    const ver = copiedFrom(next)?.ver;
    if (ver === copiedFrom(copy)?.ver && ver !== first) return undefined;
    return this.standing(next, [...met, again], first);
  }

  /** A copy as something a later answer was given. */
  private copyOutcome(copy: ReaderRow): Outcome {
    const live = this.standing(copy);
    const where = idOf(live?.sessionId);
    const here = this.chatsRead === undefined || where === undefined || this.chatsRead.has(where);
    if (live && here && this.copyShows(live)) return 'pass';
    const from = copiedFrom(copy);
    const original = from && this.rows.get(from.row);
    if (!from || from.ver === '' || !original || original.messageType === SHARED_ANSWER) return 'parent';
    // Its answer is opened by who reads the thread it is in and all that it used.
    const both = [this.readsThread(original), this.judge(original, from.ver, false)];
    if (both.some((o) => o !== 'pass' && o !== 'unchecked')) return 'parent';
    return both.includes('unchecked') ? 'unchecked' : 'pass';
  }

  /** Whose thread an answer that was copied is in: a private one is its owner's, an agent's its room's people's who use the agent. */
  readsThread(original: ReaderRow): Outcome {
    const chat = idOf(original.sessionId);
    if (chat === undefined) return 'parent';
    if (this.uploadChats.has(chat)) return this.reader.member === true ? 'pass' : 'parent';
    if (!this.madeIn) return 'unchecked';
    const thread = this.madeIn.get(chat);
    if (!thread) return 'parent';
    const room = idOf(thread.agentRoomId);
    if (room === undefined) return idOf(thread.userId) === this.reader.userId ? 'pass' : 'parent';
    const agent = idOf(thread.agentKey);
    const said = [
      agent === undefined ? false : this.agentUse.get(agent),
      this.uploadChats.has(room) ? this.reader.member === true : this.chats.get(room),
    ];
    if (said.includes(false)) return 'parent';
    return said.includes(undefined) ? 'unchecked' : 'pass';
  }

  /** What is read beside a version's label: the shares its share counts on; for a copy, everything it may be judged by. */
  private besides(row: ReaderRow, ver: string): Counted[] {
    if (row.messageType !== SHARED_ANSWER) return asksOf(consentAt(row, ver))?.on ?? [];
    const again = idOf(row.again);
    const from = copiedFrom(row);
    return [
      ...(copyCountsOn(row) ?? []),
      ...(again !== undefined ? [{ row: again, ver: LEGACY_LABEL_VER }] : []),
      ...(from ? [{ row: from.row, ver: from.ver === '' ? LEGACY_LABEL_VER : from.ver }] : []),
    ];
  }

  private judgeVersion(row: ReaderRow, ver: string, own: boolean): Outcome {
    const reader = this.reader;
    const asker = this.askerOf(row);
    const isAsker = asker !== undefined && asker === reader.userId;
    if (row.messageType === SHARED_ANSWER) return this.copyOutcome(row);
    if (row.messageType === 'user_query' || row.messageType === 'note') {
      // A person's own words, quoted: every member reads them. What they quote of an answer is never sent with them.
      return reader.member === true && row.removedAt == null
        ? 'pass'
        : 'parent';
    }
    const { label, state } = versionAt(row, ver, own);
    if (state === 'legacy') {
      if (isAsker) return 'pass';
      // Before labels: the per-record view for those who could already read the chat, and only for its own text.
      return own &&
        !unlabelledUnderRules(row, this.opts.session) &&
        readBeforeSwitch(reader, this.opts.session, this.opts.switchAt)
        ? 'pass'
        : 'label_missing';
    }
    if (state !== 'ok' || !label)
      return isAsker
        ? 'pass'
        : HIDDEN_BY_STATE[state === 'ok' ? 'missing' : state];
    if (this.cut.has(`${idOf(row._id) ?? ''}@${ver}`)) return 'parent';
    return combine([
      this.direct(label, isAsker),
      ...this.parents(label, isAsker, own),
    ]);
  }

  /** `ver` of `row` by its own label alone, for a share's check: no deleted source is excused, and nothing it built on counts. */
  directOf(row: ReaderRow, ver: string, own: boolean): Outcome {
    const { label, state } = versionAt(row, ver, own);
    if (state !== 'ok' || !label)
      return HIDDEN_BY_STATE[state === 'ok' ? 'missing' : state];
    return this.direct(label, false);
  }

  private direct(label: AnswerProvenance, isAsker: boolean): Outcome {
    const reader = this.reader;
    const direct: Plain = isPlain(label.direct) ? label.direct : {};
    const outcomes: Outcome[] = [];
    if (listOf<string>(direct.personal).some((p) => p !== reader.userId)) {
      outcomes.push('personal');
    }
    // An administrator's statement about a source is not checked here yet: only the asker reads past it.
    if (listOf(direct.marks).length > 0 && !isAsker) outcomes.push('sources');
    for (const agent of listOf<unknown>(direct.agents)) {
      const key =
        isPlain(agent) && typeof agent.key === 'string' ? agent.key : '';
      const use = this.agentUse.get(key);
      outcomes.push(
        use === true ? 'pass' : use === false ? 'agent' : 'unchecked',
      );
    }
    for (const upload of listOf<unknown>(direct.chat)) {
      const cid =
        isPlain(upload) && typeof upload.conversationId === 'string'
          ? upload.conversationId
          : '';
      if (cid !== '' && this.uploadChats.has(cid) && reader.member === true)
        continue;
      const read = this.chats.get(cid);
      outcomes.push(
        read === true ? 'pass' : read === false ? 'sources' : 'unchecked',
      );
    }
    for (const project of listOf<string>(direct.projects)) {
      const read = this.projects.get(project);
      // The asker keeps an answer whose project was deleted, as one whose sources were; nobody else does.
      if (read === 'deleted' && isAsker) continue;
      outcomes.push(
        read === true
          ? 'pass'
          : read === 'deleted'
            ? 'deleted'
            : read === false
              ? 'sources'
              : 'unchecked',
      );
    }
    const ids = [
      ...listOf<string>(direct.records),
      ...listOf<string>(direct.nodes),
    ];
    const denied: string[] = [];
    let unsettled = false;
    for (const id of ids) {
      if (this.readable.readable.has(id)) continue;
      if (this.readable.asked.has(id)) denied.push(id);
      else unsettled = true;
    }
    if (denied.length > 0) {
      const allDeleted = denied.every((id) => this.readable.deleted.has(id));
      // The asker keeps an answer whose only missing sources were deleted; nobody else does.
      if (!(allDeleted && isAsker))
        outcomes.push(allDeleted ? 'deleted' : 'sources');
    }
    if (unsettled) outcomes.push('unchecked');
    return combine(outcomes);
  }

  private parents(
    label: AnswerProvenance,
    isAsker: boolean,
    own: boolean,
  ): Outcome[] {
    const outcomes: Outcome[] = [];
    for (const parent of listOf<LabelParent>(label.parents)) {
      if (!isPlain(parent)) {
        outcomes.push('parent');
        continue;
      }
      if (typeof parent.id !== 'string' || parent.id === '') {
        const runId = writingRunOf(parent);
        const writers = runId === undefined ? [] : this.writers.get(runId) ?? [];
        if (writers.length === 0) {
          // A file whose writing run is not named or not found here: its asker's own run read it. One named with no
          // run that was written before the rules started is judged as an answer from then (`settleLegacyFiles`).
          const file = runId === undefined ? legacyFileOf(parent) : undefined;
          const before = file
            ? this.legacyFiles.get(legacyFileKey(file))
            : undefined;
          outcomes.push(
            own && isAsker
              ? 'pass'
              : before === 'pass' || before === 'unchecked'
                ? before
                : 'parent',
          );
          continue;
        }
        // A file holds what the run that wrote it read: the version that run wrote is judged as any parent is.
        for (const writer of writers) {
          const row = this.rows.get(writer.id);
          const outcome = row ? this.judge(row, writer.ver, false) : 'parent';
          outcomes.push(
            outcome === 'pass' || outcome === 'unchecked' ? outcome : 'parent',
          );
        }
        continue;
      }
      const row = this.rows.get(parent.id);
      if (!row) {
        outcomes.push('parent');
        continue;
      }
      const ver = parent.ver;
      if (parent.mode === 'text' && this.covered(row, ver)) continue;
      // An answer from before labels, read through the reader's own view of it (`settleLegacyText`).
      const before =
        parent.mode === 'text' && ver === LEGACY_LABEL_VER
          ? this.legacyText.get(parent.id)
          : undefined;
      if (before !== undefined) {
        outcomes.push(
          before === 'pass' || before === 'unchecked' ? before : 'parent',
        );
        continue;
      }
      const outcome = this.judge(row, ver, false);
      outcomes.push(
        outcome === 'pass' || outcome === 'unchecked' ? outcome : 'parent',
      );
    }
    return outcomes;
  }
}

const verdictOf = (
  outcome: Outcome,
  validUntil: number,
  now: number,
): RowVerdict => {
  if (outcome === 'pass') return { verdict: 'visible', validUntil };
  if (outcome === 'unchecked' || outcome === 'check_failed') {
    return {
      verdict: 'unchecked',
      reason: 'check_failed',
      validUntil: now + UNCHECKED_TTL_MS,
    };
  }
  return { verdict: 'hidden', reason: outcome, validUntil };
};

// ---- the reader's verdicts --------------------------------------------------------------------------------

export interface VisibleRowsOptions {
  /** The reader passed the chat's read guard (a member now), though no share row of their own says so. */
  member?: boolean;
  /** Milliseconds the access check may take for this read. */
  timeoutMs?: number;
  /** Rows already carry their stored label fields. */
  labelsLoaded?: boolean;
  /** Whose rows with no author or asker stored are; the chat's owner unless said. */
  ownerId?: string;
  /** Where the access request goes, when no wiring at start said. */
  appConfig?: AppConfig;
  /** What this request already settled for the reader; the check adds to it, so the per-record view asks nothing twice. */
  known?: Readable;
  /** More ids to settle in the same request (the records the rows name). */
  extraIds?: readonly string[];
  /** Judge every row now, past any stored verdict. */
  fresh?: boolean;
  /** Answers the run just produced: retrieval checked their access, so a check that fails now never hides them from their asker. */
  freshIds?: readonly unknown[];
  /** Agents the caller already knows the reader may use (the agent a sender is running this turn). */
  usableAgents?: readonly string[];
  /**
   * The chats this read happens in, when they are not the session's own: a share given in any other chat covers no
   * one here (`consentCovers`). A fork deciding what to copy reads the room's rows as the fork will read them.
   */
  chatsRead?: readonly string[];
  deps?: Partial<VisibilityDeps>;
}

const epochDigestFor = async (
  deps: VisibilityDeps,
  reader: Reader,
  session: ReaderSession | undefined,
): Promise<string | undefined> => {
  if (!deps.epochs) return undefined;
  let keys: string[];
  try {
    keys = [
      principalEpochKey(reader.orgId, reader.userId),
      kbEpochKey(reader.orgId),
    ];
  } catch {
    return undefined;
  }
  const epochs = await readEpochs(deps.epochs, keys);
  // An epoch that cannot be read means anything may have changed: nothing is stored or served.
  if (epochs === null) return undefined;
  return epochDigestOf([
    keys.map((k) => epochs.get(k) ?? 0),
    Number(session?.aclVersion ?? 0),
    Number(session?.audienceEpoch ?? 0),
    reader.member === true,
    reader.memberSince ?? '',
  ]);
};

/**
 * The parts of one stored row a stored verdict depends on beside the check's digest: its text, its share, whom a
 * share with people names, and who asked for it.
 */
const rowDigest = (row: ReaderRow, asker: string | undefined): string =>
  epochDigestOf([
    labelContentHash(
      typeof row.content === 'string' ? row.content : undefined,
      row.parts,
    ),
    row.consent ?? null,
    row.consentTo ?? null,
    asker ?? null,
  ]);

/** A row with the label fields the store holds for it, as `Judge.loadTree` takes them; with none stored, no label and no share. */
const withStoredLabel = (
  given: ReaderRow,
  stored: ReaderRow | undefined,
): ReaderRow =>
  stored
    ? {
        ...given,
        content: stored.content,
        parts: stored.parts,
        labelVer: stored.labelVer,
        provenance: stored.provenance,
        labelHistory: stored.labelHistory,
        consent: stored.consent,
        consentTo: stored.consentTo,
        ...(stored.requestedBy !== undefined && {
          requestedBy: stored.requestedBy,
        }),
      }
    : {
        ...given,
        provenance: undefined,
        labelHistory: undefined,
        consent: undefined,
        consentTo: undefined,
      };

/**
 * The labelled rows with their stored label fields, in one read: a read may have reshaped the rows it passes, and its
 * views leave a share out. A read that fails leaves every row with no label, its asker's alone.
 */
const storedLabelsOf = async (
  deps: VisibilityDeps,
  orgId: string,
  rows: readonly ReaderRow[],
): Promise<ReaderRow[]> => {
  if (rows.length === 0) return [];
  let stored: ReadonlyMap<string, ReaderRow>;
  try {
    stored = await deps.labels(orgId, [
      ...new Set(rows.map((r) => idOf(r._id) as string)),
    ]);
  } catch {
    stored = new Map();
  }
  return rows.map((row) =>
    withStoredLabel(row, stored.get(idOf(row._id) as string)),
  );
};

interface CardContext {
  /** The cards among the rows, and those a question among them answers, read from the store when not among them. */
  cards: ReadonlyMap<string, ReaderRow>;
  /** The answers and errors of those cards' runs that the rows do not hold, with their stored label fields. */
  answers: ReaderRow[];
}

/**
 * What the rows' cards are judged by: a card follows the answers of its own run, and a question that
 * answers a card follows that card. A feed page holds only what changed, in change order, so either may be missing
 * from it; they are read here, to be judged with the rows in the same access request. A read that fails leaves a card
 * with no answer: its asker's alone.
 */
async function cardContextOf(
  deps: VisibilityDeps,
  orgId: string,
  session: ReaderSession | undefined,
  rows: readonly ReaderRow[],
  labelled: readonly ReaderRow[],
): Promise<CardContext> {
  const cards = new Map<string, ReaderRow>();
  for (const row of rows) {
    const id = idOf(row._id);
    if (id !== undefined && row.messageType === 'tool_call') cards.set(id, row);
  }
  const named = [
    ...new Set(
      rows
        .map(answeredCardOf)
        .filter((id): id is string => id !== undefined && !cards.has(id)),
    ),
  ];
  if (named.length > 0) {
    const loaded = await deps
      .labels(orgId, named)
      .catch(() => new Map<string, ReaderRow>());
    for (const [id, row] of loaded) {
      if (row.messageType === 'tool_call') cards.set(id, row);
    }
  }
  const held = new Set(labelled.map(runOf));
  const runs = new Set<string>();
  const sessions = new Set<string>();
  for (const card of cards.values()) {
    const runId = runOf(card);
    const sessionId = idOf(card.sessionId) ?? idOf(session?._id);
    if (runId === undefined || held.has(runId) || sessionId === undefined)
      continue;
    runs.add(runId);
    sessions.add(sessionId);
  }
  if (runs.size === 0) return { cards, answers: [] };
  const have = new Set(labelled.map((r) => idOf(r._id)));
  const found = await deps
    .runs(orgId, [...sessions], [...runs])
    .catch((): readonly ReaderRow[] => []);
  return {
    cards,
    answers: found.filter((row) => {
      const id = idOf(row._id);
      return (
        id !== undefined &&
        !have.has(id) &&
        isLabelledType(row) &&
        runs.has(runOf(row) ?? '')
      );
    }),
  };
}

/** The chats a check reads rows of: the one read, the room of a thread or of an agent thread, and the rows' own. */
const chatsOf = (
  session: ReaderSession | undefined,
  rows: readonly ReaderRow[],
): string[] =>
  [
    ...new Set(
      [
        idOf(session?._id),
        idOf(session?.parentId),
        idOf(session?.agentRoomId),
        ...rows.map((r) => idOf(r.sessionId)),
      ].filter((id): id is string => id !== undefined),
    ),
  ].sort();

/**
 * What every verdict of a check depends on beside its rows: the reader's epochs, each chat's audience epoch as stored
 * now (a share's change on any answer moves it, so an answer built on that one is judged again too), which chat is read,
 * whose its rows with no asker are, what the caller vouched for, and whether an agent rule can be settled for the reader
 * at all (the agent check calls out as the request's identity: a read with no request behind it leaves the rule
 * unchecked, which a read with one must not be served). Read before the rows, so a change that lands while they are read
 * is never stored under a key from after it. Undefined when any of it cannot be read.
 */
const checkDigestFor = async (
  deps: VisibilityDeps,
  reader: Reader,
  session: ReaderSession | undefined,
  rows: readonly ReaderRow[],
  opts: VisibleRowsOptions,
): Promise<string | undefined> => {
  if (!deps.epochs) return undefined;
  const chats = chatsOf(session, rows);
  const [epochs, audiences] = await Promise.all([
    epochDigestFor(deps, reader, session),
    chats.length === 0
      ? Promise.resolve(new Map<string, number>())
      : deps.audiences(reader.orgId, chats).catch(() => undefined),
  ]);
  if (epochs === undefined || audiences === undefined) return undefined;
  return epochDigestOf([
    epochs,
    chats.map((id) => [id, audiences.get(id) ?? null]),
    [idOf(session?._id), idOf(session?.parentId), idOf(session?.agentRoomId)],
    // A read named as another chat's judges shares as that chat would; its verdicts are never a read of this one's.
    [...(opts.chatsRead ?? [])].sort(),
    opts.ownerId ?? idOf(session?.userId) ?? null,
    timeOf(session?.privacyStampedAt) ?? null,
    timeOf(session?.projectVisibleAt) ?? null,
    [...(opts.usableAgents ?? [])].sort(),
    reader.identity !== undefined,
  ]);
};

/** The turn each row belongs to, by the question that opens it, as the per-record view groups them. */
const turnsOf = (rows: readonly ReaderRow[]): number[] => {
  let turn = 0;
  return rows.map((row) => {
    if (startsTurn(row as ViewerMessage)) turn += 1;
    return turn;
  });
};

interface Judged {
  verdicts: Map<string, RowVerdict>;
  /** The labelled rows as judged, with their stored label fields. */
  rows: ReadonlyMap<string, ReaderRow>;
  /** When the privacy rules started, when a row from before labels was judged. */
  switchAt?: number;
  /** No label was read: the per-record view decides every row, as it did before labels. */
  perRecord?: true;
  /** What judged the rows, with every version it read behind them. */
  judge?: Judge;
}

/** Every row shown, to be shaped by the per-record view; nothing asked, nothing stored. */
const perRecordJudged = (
  reader: Reader,
  rows: readonly ReaderRow[],
  ownerId: string | undefined,
  now: number,
): Judged => {
  const verdicts = new Map<string, RowVerdict>();
  for (const row of rows) {
    const id = idOf(row._id);
    if (id === undefined) continue;
    verdicts.set(id, {
      verdict: 'visible',
      basis:
        rowAuthorOf(row, ownerId) === reader.userId ? 'asker' : 'before_labels',
      validUntil: now + VERDICT_TTL_MS,
    });
  }
  return { verdicts, rows: new Map(), perRecord: true };
};

async function judgeRows(
  viewer: Reader,
  session: ReaderSession | undefined,
  rows: readonly ReaderRow[],
  opts: VisibleRowsOptions,
): Promise<Judged> {
  const deps: VisibilityDeps = { ...configured, ...opts.deps };
  const reader = asMember(viewer, session, opts.member);
  const now = deps.now();
  // A failed read of the rule leaves the stricter one.
  if (!(await deps.labelRule(reader.orgId).catch(() => true))) {
    return perRecordJudged(
      reader,
      rows,
      opts.ownerId ?? idOf(session?.userId),
      now,
    );
  }
  const out = new Map<string, RowVerdict>();
  const forever = now + VERDICT_TTL_MS;
  const fresh = new Set(
    (opts.freshIds ?? [])
      .map((id) => idOf(id))
      .filter((id): id is string => id !== undefined),
  );
  const passed = rows.filter(
    (r) => idOf(r._id) !== undefined && isLabelledType(r),
  );
  // Stored verdicts first, under what the check depends on beside its rows, read before anything else. Rows with no
  // answer among them need neither.
  const digest =
    opts.fresh === true || passed.length === 0
      ? undefined
      : await checkDigestFor(deps, reader, session, passed, opts);
  // Every input of a verdict, and of the key it is stored under, is the stored one.
  const given =
    opts.labelsLoaded === true || passed.length === 0
      ? passed
      : await storedLabelsOf(deps, reader.orgId, passed);
  // The answers the rows' cards follow, judged with the rows when the rows do not hold them.
  const cards = await cardContextOf(deps, reader.orgId, session, rows, given);
  const labelled =
    cards.answers.length === 0 ? given : [...given, ...cards.answers];
  // Who reads answers from before labels as they always did depends on when the rules started; a failed read of it
  // leaves only the owner and undated share rows as such readers.
  const switchAt =
    labelled.some((r) => versionOf(r) === LEGACY_LABEL_VER) ||
    rows.some(
      (r) =>
        idOf(r._id) === undefined &&
        isLabelledType(r) &&
        versionOf(r) === LEGACY_LABEL_VER,
    )
      ? await deps.switchAt(reader.orgId).catch(() => 0)
      : undefined;
  const judge = new Judge(reader, deps, {
    switchAt,
    appConfig: opts.appConfig,
    usableAgents: opts.usableAgents,
    session,
    ownerId: opts.ownerId,
    timeoutMs: opts.timeoutMs,
    known: opts.known,
    extraIds: opts.extraIds,
    labelsLoaded: true,
    chatsRead: opts.chatsRead,
  });
  const keyOf = (row: ReaderRow): string | undefined =>
    digest === undefined || fresh.has(idOf(row._id) as string)
      ? undefined
      : verdictKey({
          orgId: reader.orgId,
          viewerId: reader.userId,
          rowId: idOf(row._id) as string,
          labelVer: versionOf(row),
          epochDigest: epochDigestOf([
            digest,
            switchAt ?? null,
            rowDigest(row, judge.askerOf(row)),
          ]),
        });
  const stored =
    digest === undefined
      ? new Map<string, StoredVerdict>()
      : await deps.store
          .getMany(
            labelled.map(keyOf).filter((k): k is string => k !== undefined),
          )
          .catch(() => new Map<string, StoredVerdict>());
  const toJudge = labelled.filter((row) => {
    const key = keyOf(row);
    const hit = key === undefined ? undefined : stored.get(key);
    if (!hit) return true;
    out.set(idOf(row._id) as string, {
      verdict: hit.v,
      ...(hit.reason !== undefined && { reason: hit.reason as HiddenReason }),
      ...(isBasis(hit.basis) && { basis: hit.basis }),
      validUntil: hit.validUntil,
    });
    return false;
  });
  const copyRows = rows.filter((r) => r.messageType === SHARED_ANSWER && idOf(r._id) !== undefined);
  const storedCopies = copyRows.length === 0 || opts.labelsLoaded === true ? new Map<string, ReaderRow>() : await deps.labels(reader.orgId, copyRows.map((r) => idOf(r._id) as string));
  const copyOf = (row: ReaderRow): ReaderRow => {
    const held = storedCopies.get(idOf(row._id) as string);
    return held ? { ...row, sharedFrom: held.sharedFrom } : row;
  };
  const counting = copyRows.map(copyOf).filter((copy) => copyCountsOn(copy)?.length !== 0);
  if (toJudge.length > 0 || counting.length > 0 || (opts.extraIds?.length ?? 0) > 0) {
    await judge.prepare([...toJudge, ...counting]);
    const toStore: Array<[string, StoredVerdict]> = [];
    for (const given of toJudge) {
      const id = idOf(given._id) as string;
      const row = judge.rows.get(id) ?? given;
      let verdict = verdictOf(
        judge.judge(row, versionOf(row), true),
        judge.validUntil,
        now,
      );
      // A check that could not settle does not hide an answer from its own asker, as nothing did before labels: its
      // label names what her own run read. The run's new answer stays as the label decided it (retrieval checked its
      // access); any other goes on through the per-record view (`basis: 'asker'`). A check that settled a denial still
      // hides it.
      if (
        verdict.verdict === 'unchecked' &&
        judge.askerOf(row) === reader.userId
      ) {
        verdict = fresh.has(id)
          ? { verdict: 'visible', validUntil: now + UNCHECKED_TTL_MS }
          : {
              verdict: 'visible',
              basis: 'asker',
              validUntil: now + UNCHECKED_TTL_MS,
            };
      }
      // An answer shared with the reader at the version it shows: its text, when they cannot see it whole.
      if (verdict.verdict === 'hidden' && judge.covered(row, versionOf(row))) {
        verdict = { verdict: 'consented', validUntil: verdict.validUntil };
      }
      if (verdict.verdict === 'visible' && verdict.basis === undefined) {
        verdict = {
          ...verdict,
          basis: basisOf(row, judge.askerOf(row), reader),
        };
      }
      out.set(id, verdict);
      const key = keyOf(given);
      if (key !== undefined) {
        toStore.push([
          key,
          {
            v: verdict.verdict,
            ...(verdict.reason !== undefined && { reason: verdict.reason }),
            ...(verdict.basis !== undefined && { basis: verdict.basis }),
            validUntil: verdict.validUntil,
          },
        ]);
      }
    }
    if (toStore.length > 0)
      await deps.store.putMany(toStore).catch(() => undefined);
  }
  // Questions, notes and copies; cards by their run's answer, and a card's answer by its card.
  const turns = turnsOf(rows);
  const answerOfTurn = new Map<number, RowVerdict>();
  rows.forEach((row, i) => {
    const id = idOf(row._id);
    if (id === undefined || !isLabelledType(row)) return;
    const verdict = out.get(id);
    const turn = turns[i] ?? 0;
    const seen = answerOfTurn.get(turn);
    if (verdict && (!seen || seen.verdict === 'visible'))
      answerOfTurn.set(turn, verdict);
  });
  const answerOfRun = new Map<string, RowVerdict>();
  for (const row of labelled) {
    const runId = runOf(row);
    const verdict = out.get(idOf(row._id) ?? '');
    if (runId === undefined || !verdict) continue;
    const seen = answerOfRun.get(runId);
    if (!seen || seen.verdict === 'visible') answerOfRun.set(runId, verdict);
  }
  const rowsOwnerId = opts.ownerId ?? idOf(session?.userId);
  /**
   * A card goes with its run's answer: shown with it, hidden with it, and its asker's alone while no
   * answer of its run is read. A card stamped with no run goes with its turn's answer among the rows. A share shows an
   * answer's text alone, never its cards.
   */
  const cardVerdict = (
    card: ReaderRow,
    turnAnswer: RowVerdict | undefined,
  ): RowVerdict => {
    const runId = runOf(card);
    const answer = runId === undefined ? turnAnswer : answerOfRun.get(runId);
    if (answer) {
      if (answer.verdict === 'visible')
        return { verdict: 'visible', validUntil: answer.validUntil };
      return answer.verdict === 'consented'
        ? { verdict: 'hidden', reason: 'parent', validUntil: answer.validUntil }
        : answer;
    }
    return rowAuthorOf(card, rowsOwnerId) === reader.userId
      ? { verdict: 'visible', validUntil: forever }
      : { verdict: 'hidden', reason: 'sources', validUntil: forever };
  };
  rows.forEach((row, i) => {
    const id = idOf(row._id);
    if (id === undefined || isLabelledType(row)) return;
    const type = row.messageType;
    if (type === SHARED_ANSWER) {
      out.set(
        id,
        judge.copyShows(copyOf(row))
          ? { verdict: 'consented', validUntil: forever }
          : { verdict: 'hidden', reason: 'parent', validUntil: forever },
      );
      return;
    }
    if (type === 'tool_call') {
      out.set(id, cardVerdict(row, answerOfTurn.get(turns[i] ?? 0)));
      return;
    }
    if (typeof type === 'string' && HUMAN.has(type)) {
      out.set(id, { verdict: 'visible', validUntil: forever });
      return;
    }
    // A kind of row this build does not know shows nothing.
    out.set(id, {
      verdict: 'hidden',
      reason: 'label_unknown_version',
      validUntil: forever,
    });
  });
  // A question that answers a card holds the card's words and what its author picked: anyone else reads only that the
  // card was answered. The card is the one it was bound to; for one stored before that was recorded, the card before it
  // in its turn among the rows, else none.
  const cardBefore = (i: number): ReaderRow | undefined => {
    for (let j = i - 1; j >= 0; j -= 1) {
      if ((turns[j] ?? 0) !== (turns[i] ?? 0)) return undefined;
      if (rows[j]?.messageType === 'tool_call') return rows[j];
    }
    return undefined;
  };
  rows.forEach((row, i) => {
    const id = idOf(row._id);
    const named = answeredCardOf(row);
    const approval = isApprovalAnswer(row);
    if (id === undefined || (named === undefined && !approval && !isUnboundCardAnswer(row)))
      return;
    if (rowAuthorOf(row, rowsOwnerId) === reader.userId) return;
    // An approval's answer holds no word of its card, only the decision, which nobody else is given.
    if (approval) {
      out.set(id, { verdict: 'visible', cardWithheld: true, validUntil: forever });
      return;
    }
    const card = named !== undefined ? cards.cards.get(named) : cardBefore(i);
    const cardId = card === undefined ? undefined : idOf(card._id);
    const shown =
      card === undefined
        ? undefined
        : ((cardId !== undefined ? out.get(cardId) : undefined) ??
          cardVerdict(card, undefined));
    out.set(id, {
      verdict: 'visible',
      cardWithheld: true,
      validUntil: shown?.validUntil ?? forever,
    });
  });
  return {
    verdicts: out,
    rows: judge.rows,
    judge,
    ...(switchAt !== undefined && { switchAt }),
  };
}

const isBasis = (value: unknown): value is RowVerdict['basis'] =>
  value === 'label' || value === 'asker' || value === 'before_labels';

/** What a shown answer was shown by: its label when it could be relied on, else being the asker's or older. */
const basisOf = (
  row: ReaderRow,
  asker: string | undefined,
  reader: Reader,
): NonNullable<RowVerdict['basis']> => {
  if (labelStateOf(row) === 'ok') return 'label';
  return asker !== undefined && asker === reader.userId
    ? 'asker'
    : 'before_labels';
};

/**
 * Each row's verdict for `viewer`, by row id. A question, note or system line is visible; an answer or error is
 * judged by its label; a card follows its turn's answer and is its asker's own; a shared copy is shown to members
 * as text. Every failed check hides.
 */
export async function visibleRows(
  viewer: Reader,
  session: ReaderSession | undefined,
  rows: readonly ReaderRow[],
  opts: VisibleRowsOptions = {},
): Promise<Map<string, RowVerdict>> {
  return (await judgeRows(viewer, session, rows, opts)).verdicts;
}

/** Whether `viewer` may read `ver` of `row` whole, now (no stored verdict is used). */
export async function fullOK(
  viewer: Reader,
  row: ReaderRow,
  ver: string,
  opts: {
    session?: ReaderSession;
    deps?: Partial<VisibilityDeps>;
    timeoutMs?: number;
  } = {},
): Promise<boolean> {
  const deps: VisibilityDeps = { ...configured, ...opts.deps };
  const reader = asMember(viewer, opts.session, undefined);
  const judge = new Judge(reader, deps, {
    session: opts.session,
    timeoutMs: opts.timeoutMs,
  });
  const own = ver === versionOf(row);
  await judge.prepare([row]);
  const loaded = judge.rows.get(idOf(row._id) ?? '') ?? row;
  return judge.judge(loaded, ver, own) === 'pass';
}

// ---- sharing ----------------------------------------------------------------------------------------------

/** Whether the share of `ver` of `row` covers `viewer` now, read in `session`. */
export async function shareCovers(
  viewer: Reader,
  row: ReaderRow,
  ver: string,
  opts: { session: ReaderSession; deps?: Partial<VisibilityDeps> },
): Promise<boolean> {
  const reader = asMember({ ...viewer, identity: viewer.identity ?? ON_BEHALF_OF_READER }, opts.session, true);
  const judge = new Judge(reader, { ...configured, ...opts.deps }, { session: opts.session, labelsLoaded: true });
  await judge.prepare([row]);
  return judge.covered(judge.rows.get(idOf(row._id) ?? '') ?? row, ver);
}

export type WidenAudience = 'everyone' | { userIds: string[] } | 'outside' | { outside: readonly string[] };

export interface WidenResult {
  ok: boolean;
  code?: 'SHARE_NOT_WIDENABLE' | 'SHARE_NOT_ALLOWED_AGENT' | 'SHARE_NOT_ALLOWED_SIGN_IN';
  limitedTo?: string[];
  on?: Array<{ row: string; ver: string }>;
  /** Of the people a share names, those who cannot open what the answer used. */
  cannotOpen?: string[];
}

const NOT_WIDENABLE: WidenResult = { ok: false, code: 'SHARE_NOT_WIDENABLE' };
const AGENT_CONTENT: WidenResult = { ok: false, code: 'SHARE_NOT_ALLOWED_AGENT' };
const SIGN_IN_CONTENT: WidenResult = { ok: false, code: 'SHARE_NOT_ALLOWED_SIGN_IN' };

const listed = (label: AnswerProvenance | undefined, field: string): unknown[] => {
  const direct: Plain = isPlain(label?.direct) ? label.direct : {};
  return listOf<unknown>(direct[field]);
};

/**
 * Whether the asker of `row` may widen `ver` of it to `audience`, sharing it in `session`. Outside the chat (a file
 * grant): every version behind it opened by the asker's own access, a file by the answer that wrote it, no share of any
 * kind, nothing of a service agent or a shared sign-in, and an agent's work only to people who can use it, or from its
 * owner when no skill they can't open was given. Inside it: an earlier answer shared with everyone there counts, an
 * agent stops nothing while it is no service agent and the asker can use it, and a question or a note of the room the
 * share reaches that every reader there reads stops nothing either.
 */
export async function widenOK(
  row: ReaderRow,
  ver: string,
  audience: WidenAudience,
  opts: { session?: ReaderSession; deps?: Partial<VisibilityDeps> } = {},
): Promise<WidenResult> {
  const deps: VisibilityDeps = { ...configured, ...opts.deps };
  const sharer = idOf(row.requestedBy);
  const orgId = idOf(row.orgId) ?? idOf(opts.session?.orgId);
  if (sharer === undefined || orgId === undefined) return NOT_WIDENABLE;
  const to = typeof audience === 'object' && 'outside' in audience ? audience.outside : undefined;
  const inChat = audience !== 'outside' && to === undefined;
  const reader: Reader = inChat
    ? { ...asMember({ userId: sharer, orgId }, opts.session, true), member: true, identity: ON_BEHALF_OF_READER }
    : { userId: sharer, orgId, identity: ON_BEHALF_OF_READER };
  const own = idOf(row.sessionId);
  const readId = idOf(opts.session?._id);
  if (readId !== undefined && own !== undefined && own !== readId) return NOT_WIDENABLE;
  const here = new Set(
    readId === undefined ? [own] : [readId, idOf(opts.session?.parentId), idOf(opts.session?.agentRoomId)],
  );
  const keys = [idOf(row.respondingAgentKey), idOf(opts.session?.agentKey)].filter((k): k is string => k !== undefined);
  // Where the share puts the answer before everyone who reads: the chat, or a thread's room.
  const room =
    opts.session === undefined
      ? undefined
      : (idOf(opts.session.agentRoomId) ?? idOf(opts.session.parentId) ?? idOf(opts.session._id));
  const judge = new Judge(reader, deps, {
    session: opts.session,
    timeoutMs: DEFAULT_CHECK_TIMEOUT_MS,
    sharing: keys,
  });
  await judge.prepare([row]);
  const start = judge.rows.get(idOf(row._id) ?? '') ?? row;
  const result = inChat
    ? await inChatOK(judge, deps, start, ver, here, room, keys, opts.session)
    : await outsideOK(judge, deps, start, ver, keys, to);
  if (!result.ok || typeof audience !== 'object' || !('userIds' in audience)) return result;
  const cannot: string[] = [];
  for (const userId of audience.userIds) {
    const ok = await fullOK(
      {
        userId,
        orgId,
        member: true,
        memberSince: opts.session ? memberSinceOf(opts.session, userId) : undefined,
      },
      start,
      ver,
      { session: opts.session, deps: opts.deps },
    );
    if (!ok) cannot.push(userId);
  }
  return cannot.length > 0 ? { ...result, cannotOpen: cannot } : result;
}

async function outsideOK(
  judge: Judge,
  deps: VisibilityDeps,
  start: ReaderRow,
  ver: string,
  keys: readonly string[],
  to: readonly string[] | undefined,
): Promise<WidenResult> {
  // Every agent met, those whose label says skill text the asker can't open was given, and those of labels too old to say.
  const met = new Set<string>(keys);
  const skill = new Set<string>();
  const older = new Set<string>();
  const path = new Set<string>();
  const done = new Map<string, WidenResult | undefined>();
  const walk = (current: ReaderRow, at: string, own: boolean): WidenResult | undefined => {
    const key = `${idOf(current._id) ?? ''}@${at}`;
    if (done.has(key)) return done.get(key);
    if (path.has(key)) return NOT_WIDENABLE;
    path.add(key);
    const result = step(current, at, own);
    path.delete(key);
    done.set(key, result);
    return result;
  };
  const step = (current: ReaderRow, at: string, own: boolean): WidenResult | undefined => {
    const { label, state } = versionAt(current, at, own);
    if (state !== 'ok' || !label) return NOT_WIDENABLE;
    if (listed(label, 'saVrids').length > 0) return AGENT_CONTENT;
    const saysNothing = label.tagset < 2;
    const responder = idOf(current.respondingAgentKey);
    const named = listed(label, 'agents');
    if (named.some((a) => !isPlain(a) || idOf(a.key) === undefined || a.kind !== 'config')) return AGENT_CONTENT;
    for (const a of named as Plain[]) {
      met.add(String(a.key));
      if (a.skill === true) skill.add(String(a.key));
    }
    if (responder !== undefined) met.add(responder);
    if (saysNothing) {
      for (const k of [...keys, ...(named as Plain[]).map((a) => String(a.key)), ...(responder !== undefined ? [responder] : [])])
        older.add(k);
    }
    if (responder !== undefined && !judge.mayUse(responder)) return NOT_WIDENABLE;
    if (judge.directOf(current, at, own) !== 'pass') return NOT_WIDENABLE;
    for (const parent of listOf<LabelParent>(label.parents)) {
      if (!isPlain(parent) || typeof parent.id !== 'string') return NOT_WIDENABLE;
      // A file is the version of the answer whose run wrote it; one no answer is found to have written refuses.
      const behind =
        parent.id === ''
          ? judge.wrote(parent)
          : [{ row: parent.id, ver: parent.ver }];
      if (behind.length === 0) return NOT_WIDENABLE;
      for (const ref of behind) {
        const p = judge.rows.get(ref.row);
        if (!p || p.messageType === SHARED_ANSWER) return NOT_WIDENABLE;
        const deeper = walk(p, ref.ver, false);
        if (deeper) return deeper;
      }
    }
    return undefined;
  };
  const refused = walk(start, ver, ver === versionOf(start));
  if (refused) return refused;
  if (met.size === 0) return { ok: true };
  const all = [...met].sort();
  const records = await Promise.all(all.map((key) => deps.isServiceAgent(judge.reader, key).catch(() => undefined)));
  if (records.some((r) => r === true)) return AGENT_CONTENT;
  if (records.some((r) => r !== false)) return NOT_WIDENABLE;
  if (keys.some((key) => !judge.mayUse(key))) return NOT_WIDENABLE;
  if (to !== undefined && to.length === 0) return { ok: true };
  // Who must be able to use each agent: everyone it goes to, unless the asker owns the agent and no skill was given.
  const stays: string[] = [];
  for (const key of all) {
    const gave =
      skill.has(key) ||
      (older.has(key) && (await deps.hasClosedSkill(judge.reader, key).catch(() => undefined)) !== false);
    const owner = !gave && (await deps.ownsAgent(judge.reader, key).catch(() => undefined)) === true;
    if (!owner) stays.push(key);
  }
  if (stays.length === 0) return { ok: true };
  if (to === undefined) return AGENT_CONTENT;
  for (const userId of to) {
    for (const key of stays) {
      const may = await deps
        .canUseAgent({ userId, orgId: judge.reader.orgId, identity: ON_BEHALF_OF_READER }, key)
        .catch(() => undefined);
      if (may !== true) return AGENT_CONTENT;
    }
  }
  return { ok: true };
}

async function inChatOK(
  judge: Judge,
  deps: VisibilityDeps,
  start: ReaderRow,
  startVer: string,
  here: ReadonlySet<string | undefined>,
  room: string | undefined,
  keys: readonly string[],
  session: ReaderSession | undefined,
): Promise<WidenResult> {
  // Every version behind it, below shares too, and through a copy the answer it was made from.
  const behind = new Map<string, { row: ReaderRow; ver: string; own: boolean; label?: AnswerProvenance }>();
  let sound = true;
  const given = (id: string, ver: string): Counted | undefined => {
    const row = judge.rows.get(id);
    if (row?.messageType !== SHARED_ANSWER) return row && { row: id, ver };
    const from = copiedFrom(row);
    return from && { row: from.row, ver: from.ver === '' ? LEGACY_LABEL_VER : from.ver };
  };
  const visit = (row: ReaderRow, ver: string, own: boolean): void => {
    const key = `${idOf(row._id) ?? ''}@${ver}`;
    if (behind.has(key)) return;
    const { label, state } = versionAt(row, ver, own);
    behind.set(key, { row, ver, own, label });
    if (state !== 'ok' && state !== 'legacy') sound = false;
    for (const parent of listOf<LabelParent>(label?.parents)) {
      if (!isPlain(parent) || typeof parent.id !== 'string') {
        sound = false;
        continue;
      }
      // A file is what the answer whose run wrote it is: each of them, and one no answer wrote can't be told.
      const refs = parent.id === '' ? judge.wrote(parent) : [given(parent.id, parent.ver)];
      if (refs.length === 0) sound = false;
      for (const ref of refs) {
        const target = ref && judge.rows.get(ref.row);
        if (!target || target.messageType === SHARED_ANSWER || typeof ref.ver !== 'string') sound = false;
        else visit(target, ref.ver, false);
      }
    }
  };
  visit(start, startVer, startVer === versionOf(start));

  const all = [...behind.values()];
  const agentsOf = (label: AnswerProvenance | undefined): Plain[] => listed(label, 'agents').filter(isPlain);
  const signIns = (label: AnswerProvenance | undefined): boolean =>
    listed(label, 'saVrids').every((v) => typeof v === 'string' && v.startsWith('shared-sign-in:'));
  if (all.some((v) => agentsOf(v.label).some((a) => a.kind !== 'config') || !signIns(v.label))) return AGENT_CONTENT;
  const asked = new Set(keys);
  const staysWith = new Set<string>();
  for (const { row, label } of all) {
    for (const agent of agentsOf(label)) {
      asked.add(String(agent.key));
      if (agent.skill === true) staysWith.add(String(agent.key));
    }
    const responder = idOf(row.respondingAgentKey);
    if (responder !== undefined) asked.add(responder);
  }
  const records = await Promise.all([...asked].map(async (key) => deps.isServiceAgent(judge.reader, key).catch(() => undefined)));
  if (records.some((r) => r === true)) return AGENT_CONTENT;
  if (all.some((v) => listed(v.label, 'saVrids').length > 0)) return SIGN_IN_CONTENT;
  if (!sound || records.some((r) => r === undefined)) return NOT_WIDENABLE;
  if (keys.some((key) => !judge.mayUse(key))) return NOT_WIDENABLE;

  // Shared with everyone here, the shares it counts on with it; a copy by being in the chat, or the one that stands for it.
  const decided = new Map<string, boolean>();
  const sharedHere = (id: string, ver: string, thread?: string): boolean => {
    const key = `${id}@${ver} ${thread ?? ''}`;
    if (decided.has(key)) return decided.get(key) === true;
    decided.set(key, false);
    const row = judge.rows.get(id);
    const copy = row?.messageType === SHARED_ANSWER ? judge.standing(row) : undefined;
    const at = copy ?? (row?.messageType === SHARED_ANSWER ? undefined : row);
    const inThread = thread !== undefined && idOf(at?.sessionId) === thread;
    if (!at || !(inThread || here.has(idOf(at.sessionId)))) return false;
    const on = copy ? copyCountsOn(copy) : asksOf(consentAt(at, ver))?.on;
    const open = copy ? judge.copyShows(copy) : (consentAt(at, ver)?.scope ?? 'everyone') === 'everyone' && judge.covered(at, ver, thread);
    const next = copy ? madeInThread(copy) : thread;
    const shared = open && on !== undefined && on.every((ref) => sharedHere(ref.row, ref.ver, next));
    decided.set(key, shared);
    return shared;
  };

  // What the sharer passes a version by: `undefined` when they don't, else the shares it counts on.
  const path = new Set<string>();
  const done = new Map<string, Map<string, Counted> | undefined>();
  const opens = (key: string): Map<string, Counted> | undefined => {
    if (done.has(key)) return done.get(key);
    if (path.has(key)) return undefined;
    path.add(key);
    const needs = opening(key);
    path.delete(key);
    done.set(key, needs);
    return needs;
  };
  const opening = (key: string): Map<string, Counted> | undefined => {
    const v = behind.get(key);
    const responder = idOf(v?.row.respondingAgentKey);
    if (!v?.label || (responder !== undefined && !judge.mayUse(responder))) return undefined;
    if (judge.directOf(v.row, v.ver, v.own) !== 'pass') return undefined;
    const needs = new Map<string, Counted>();
    for (const parent of listOf<LabelParent>(v.label.parents)) {
      if (!isPlain(parent) || typeof parent.id !== 'string' || parent.id === '') return undefined;
      // A person's words as a reply gives them, which everyone the share reaches reads: nothing more to pass or count on.
      const message = judge.rows.get(parent.id);
      if (parent.mode === 'text' && parent.ver === LEGACY_LABEL_VER && message && readByTheRoom(message, room)) continue;
      const ref = given(parent.id, parent.ver);
      const copied = judge.rows.get(parent.id)?.messageType === SHARED_ANSWER;
      const made = ref && copied ? judge.rows.get(ref.row) : undefined;
      const reads = !copied || (made !== undefined && judge.readsThread(made) === 'pass');
      const through = ref && reads ? opens(`${ref.row}@${ref.ver}`) : undefined;
      if (through) {
        for (const [k, n] of through) needs.set(k, n);
      } else if (sharedHere(parent.id, parent.ver)) {
        const isCopy = judge.rows.get(parent.id)?.messageType === SHARED_ANSWER;
        const ver = isCopy ? '' : parent.ver;
        needs.set(`${parent.id}@${ver}`, { row: parent.id, ver });
      } else return undefined;
    }
    return needs;
  };
  const needs = opens(`${idOf(start._id) ?? ''}@${startVer}`);
  if (!needs) return NOT_WIDENABLE;
  // A version whose label is from before labels said whether such text was used, or that has none: whose answer it was,
  // by who it names and by whose chat it is in (one with a label is looked up in the chat read alone). One nobody can
  // name refuses; of the rest only a record that says there is no such skill clears the agent.
  const quiet = new Set<string>();
  let refusal: WidenResult | undefined;
  const saysNothing = (v: { row: ReaderRow; label?: AnswerProvenance }): boolean =>
    (v.row.messageType === 'bot_response' || v.row.messageType === 'error') && (v.label === undefined || v.label.tagset < 2);
  for (const v of all.filter(saysNothing)) {
    const at = idOf(v.row.sessionId);
    let chat = at !== undefined && at === idOf(session?._id) ? session : undefined;
    if (!chat && !v.label && at !== undefined) chat = (await deps.chats(judge.reader.orgId, [at]).catch(() => undefined))?.get(at);
    const chatAgent = chat?.agentKey;
    // Only a thread of a room's is an agent's without a key to say which.
    const unnamed = chat?.agentRoomId !== undefined && idOf(chatAgent) === undefined;
    if ((!chat && !v.label) || unnamed) return NOT_WIDENABLE;
    for (const key of [...agentsOf(v.label).map((a) => String(a.key)), idOf(v.row.respondingAgentKey), idOf(chatAgent)])
      if (key !== undefined) quiet.add(key);
  }
  for (const key of quiet) {
    const service = asked.has(key) ? false : await deps.isServiceAgent(judge.reader, key).catch(() => undefined);
    if (service === true) refusal = AGENT_CONTENT;
    else if (service !== false) refusal = refusal ?? NOT_WIDENABLE;
    else if ((await deps.hasClosedSkill(judge.reader, key).catch(() => undefined)) !== false) staysWith.add(key);
  }
  if (refusal) return refusal;
  const followed = new Set<string>();
  const stay = (ref: Counted): void => {
    const key = `${ref.row}@${ref.ver}`;
    const row = judge.rows.get(ref.row);
    const at = row?.messageType === SHARED_ANSWER ? judge.standing(row) : row;
    if (!at || followed.has(key)) return;
    followed.add(key);
    const asks = row?.messageType === SHARED_ANSWER ? undefined : asksOf(consentAt(at, ref.ver));
    for (const agent of asks?.agents ?? []) staysWith.add(agent);
    for (const earlier of row?.messageType === SHARED_ANSWER ? copyCountsOn(at) ?? [] : asks?.on ?? []) stay(earlier);
  };
  for (const ref of needs.values()) stay(ref);
  return {
    ok: true,
    ...(staysWith.size > 0 && { limitedTo: [...staysWith].sort() }),
    ...(needs.size > 0 && { on: [...needs.keys()].sort().map((key) => needs.get(key) as Counted) }),
  };
}

// ---- what a reader is given ------------------------------------------------------------------------------

/** What a hidden row keeps: which row it is, where and when, never anything its text or length could tell. */
export function hiddenView(row: ReaderRow & Plain): Record<string, unknown> {
  const out: Record<string, unknown> = {
    _id: row._id,
    // A failed run looks like any other hidden answer; a card stays a card.
    messageType: row.messageType === 'tool_call' ? 'tool_call' : 'bot_response',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    attachments: [],
    referenceData: [],
    followUpQuestions: [],
    reasoning: [],
    tools: [],
    parts: [],
  };
  for (const field of [
    'sessionId',
    'seq',
    'changeRev',
    'createdAt',
    'updatedAt',
    'inReplyTo',
    'requestedBy',
  ] as const) {
    if (row[field] !== undefined) out[field] = row[field];
  }
  return out;
}

const HIDDEN_LOST = 'This answer used content you can no longer access.';
const HIDDEN_UNCHECKED = "We couldn't confirm your access. Try again.";

/**
 * A hidden row as a reader gets it: its fixed fields (`hiddenView`), the fixed text and the marker the app shows a
 * hidden answer by. One reason for everyone (the reader is never told which source, tool or earlier answer it was),
 * except that a check that could not run says so, as it is worth trying again. A card hides the same way.
 */
export function hiddenRowFor(
  row: ReaderRow & Plain,
  verdict: Pick<RowVerdict, 'verdict' | 'reason'>,
): Record<string, unknown> {
  const unchecked =
    verdict.verdict === 'unchecked' || verdict.reason === 'check_failed';
  return {
    ...hiddenView(row),
    content: unchecked ? HIDDEN_UNCHECKED : HIDDEN_LOST,
    answerHidden: { ...(unchecked ? ANSWER_UNCONFIRMED : ANSWER_LOST) },
  };
}

/** An answer shared with the reader: its text alone, never its sources, citations, steps or files. */
export function consentedView(
  row: ReaderRow & Plain,
  deps: Pick<VisibilityDeps, 'sharedText'> = configured,
): Record<string, unknown> {
  if (row.messageType === SHARED_ANSWER) {
    const copy: Record<string, unknown> = { ...row };
    for (const field of ['citations', 'referenceData', 'parts', 'tools', 'reasoning']) delete copy[field];
    return copy;
  }
  let text: string;
  try {
    text = deps.sharedText(typeof row.content === 'string' ? row.content : '');
  } catch {
    text = '';
  }
  return {
    ...hiddenView(row),
    content: text,
    sharedWithYou: true,
  };
}

/** Another person's answer as a reader who may see it gets it: no steps, reasoning or tool output. */
export function withoutSteps<T extends object>(row: T): T {
  const r = row as Plain;
  // A question or note is its author's own words, and a card is shaped for each reader where it is drawn: neither
  // carries the steps of a run.
  if (r.messageType !== 'bot_response' && r.messageType !== 'error') return row;
  const view: Plain = { ...r };
  if ('reasoning' in view) view.reasoning = [];
  if ('tools' in view) view.tools = [];
  if (Array.isArray(r.parts)) {
    view.parts = (r.parts as unknown[]).filter(
      (p) => isPlain(p) && p.type === 'text',
    );
  }
  return view as T;
}

export interface ReaderViewOptions extends VisibleRowsOptions {
  /** The chat's owner: rows from before authorship was stored are theirs. */
  ownerId?: string;
  /**
   * The per-record view of these rows (`redactMessagesFor`, or a page's own form of it): run with the same `known`,
   * so it asks nothing the reader check already asked.
   */
  perRecordView?: <M extends object>(
    rows: M[],
    known: Readable,
  ) => Promise<M[]>;
  /**
   * Which rows pass the per-record view; every row unless said. A channel with its own check of a question's
   * sources sends only the answers the label did not decide alone (`basis` other than `label`).
   */
  perRecordFor?: (row: ReaderRow, verdict: RowVerdict | undefined) => boolean;
  /** For `perRecordView` when none is given. */
  appConfig?: AppConfig;
}

/** The answers a label did not decide alone: an asker's own, and those written before labels. */
export const unlabelledAnswers = (
  row: ReaderRow,
  verdict: RowVerdict | undefined,
): boolean =>
  isLabelledType(row) && (verdict === undefined || verdict.basis !== 'label');

/**
 * Rows as `viewer` may be given them: hidden rows as `hiddenRowFor`, answers shared with them as their text, every
 * other row through the per-record view, with another person's steps left out. One access request covers the
 * reader check and the per-record view.
 */
export async function readerView<M extends object>(
  viewer: Reader,
  session: ReaderSession | undefined,
  rows: M[],
  opts: ReaderViewOptions = {},
): Promise<{ rows: M[]; verdicts: Map<string, RowVerdict> }> {
  const known = opts.known ?? newReadable();
  // One deadline for the whole read: the label check and the per-record view ask within it, never twice.
  known.deadline ??= Date.now() + (opts.timeoutMs ?? DEFAULT_CHECK_TIMEOUT_MS);
  const ownerId = opts.ownerId ?? idOf(session?.userId);
  const asRows = rows as unknown as Array<ReaderRow & Plain>;
  const judged = await judgeRows(viewer, session, asRows, { ...opts, known });
  const verdicts = judged.verdicts;
  const appConfig = opts.appConfig ?? appConfigOf?.();
  const perRecord =
    opts.perRecordView ??
    (async <R extends object>(list: R[], settled: Readable): Promise<R[]> =>
      // With no endpoint to ask, a row that names a record fails its check and hides; one that names none is shown.
      (await redactMessagesFor(
        appConfig as AppConfig,
        viewer,
        list as unknown as ViewerMessage[],
        (m) => rowAuthorOf(m as ReaderRow, ownerId),
        { known: settled },
      )) as unknown as R[]);
  if (judged.perRecord) {
    const shown = await perRecord(rows, known);
    shown.forEach((m, i) => {
      const id = idOf((rows[i] as unknown as ReaderRow)._id);
      const hidden = (m as unknown as Plain).answerHidden;
      if (id === undefined || !isPlain(hidden)) return;
      verdicts.set(id, {
        verdict:
          hidden.reason === ANSWER_UNCONFIRMED.reason ? 'unchecked' : 'hidden',
        reason: 'sources',
        validUntil: Date.now() + UNCHECKED_TTL_MS,
      });
    });
    return { rows: shown, verdicts };
  }
  const through = rows
    .map((m, i) => ({ m, i }))
    .filter(({ m }) => {
      const row = m as unknown as ReaderRow;
      const id = idOf(row._id);
      return (
        opts.perRecordFor?.(
          row,
          id === undefined ? undefined : verdicts.get(id),
        ) ?? true
      );
    });
  const viewed: Array<M | undefined> = new Array<M | undefined>(rows.length);
  if (through.length > 0) {
    const out = await perRecord(
      through.map(({ m }) => m),
      known,
    );
    through.forEach(({ i }, n) => {
      viewed[i] = out[n];
    });
  }
  const replies = await replyVerdicts(viewer, session, asRows, verdicts, {
    ...opts,
    known,
  });
  const reader = asMember(viewer, session, opts.member);
  /** A row the per-record view let through (or did not need to see), as the reader is given it. */
  const shownView = (shown: M, row: ReaderRow & Plain): M => {
    const view = shown as unknown as Plain;
    if (view.answerHidden !== undefined) {
      const reason = (view.answerHidden as Plain).reason;
      return hiddenRowFor(row, {
        verdict: reason === ANSWER_UNCONFIRMED.reason ? 'unchecked' : 'hidden',
        reason: 'sources',
      }) as M;
    }
    const checked = withCheckedReply(shown, replies);
    if (rowAuthorOf(row, ownerId) === viewer.userId) return checked;
    return withoutSteps(withoutQuote(checked));
  };
  const out = rows.map((m, i) => {
    const row = m as unknown as ReaderRow & Plain;
    const id = idOf(row._id);
    const verdict = id === undefined ? undefined : verdicts.get(id);
    if (!verdict) {
      if (!isLabelledType(row) && row.messageType !== 'tool_call') {
        return shownView(viewed[i] ?? m, row);
      }
      // A row read with no id has no stored label: what was shown before labels, to those it was shown to then.
      if (
        id === undefined &&
        unlabelledShownTo(reader, session, row, ownerId, judged.switchAt)
      ) {
        return shownView(viewed[i] ?? m, row);
      }
      return hiddenRowFor(row, { verdict: 'unchecked' }) as M;
    }
    if (verdict.verdict === 'hidden' || verdict.verdict === 'unchecked') {
      return hiddenRowFor(row, verdict) as M;
    }
    if (verdict.verdict === 'consented') return consentedView(row) as M;
    const shown = shownView(viewed[i] ?? m, row);
    return verdict.cardWithheld === true ? withheldCardAnswer(shown) : shown;
  });
  return { rows: out, verdicts };
}

/**
 * Whether a row with no stored label was shown to the reader before labels: to its asker, and to someone who could
 * already read the chat when it was written.
 */
const unlabelledShownTo = (
  reader: Reader,
  session: ReaderSession | undefined,
  row: ReaderRow,
  ownerId: string | undefined,
  switchAt: number | undefined,
): boolean => {
  if (versionOf(row) !== LEGACY_LABEL_VER) return false;
  return (
    rowAuthorOf(row, ownerId) === reader.userId ||
    (!unlabelledUnderRules(row, session) &&
      readBeforeSwitch(reader, session, switchAt))
  );
};

/** The reply kinds that quote an answer: an assistant's answer, or a shared copy of one. */
const QUOTES_AN_ANSWER = new Set(['answer', 'shared_answer']);

const replyTargetOf = (row: Plain): string | undefined => {
  const reply = row.replyTo;
  if (
    !isPlain(reply) ||
    typeof reply.kind !== 'string' ||
    !QUOTES_AN_ANSWER.has(reply.kind)
  )
    return undefined;
  if (typeof reply.excerpt !== 'string' || reply.excerpt === '')
    return undefined;
  return idOf(reply.messageId);
};

/** An answer a reply quotes, as the reader is judged against it: their verdict now, and the version it is for. */
interface QuotedAnswer {
  verdict: RowVerdict | undefined;
  /** The answer's version now. */
  ver: string | undefined;
  /** How many versions it has had: 1 when it was never written again. */
  versions: number;
}

/**
 * Every answer the rows quote, for the reader: its verdict (those on the page, then the rest read and judged), with the
 * version that verdict is for. Read once for the page; a read that fails leaves every excerpt withheld.
 */
async function replyVerdicts(
  viewer: Reader,
  session: ReaderSession | undefined,
  rows: ReadonlyArray<ReaderRow & Plain>,
  page: ReadonlyMap<string, RowVerdict>,
  opts: VisibleRowsOptions,
): Promise<ReadonlyMap<string, QuotedAnswer>> {
  const out = new Map<string, QuotedAnswer>();
  const ids = [
    ...new Set(
      rows.flatMap((row) => {
        const target = replyTargetOf(row);
        return target === undefined ? [] : [target];
      }),
    ),
  ];
  if (ids.length === 0) return out;
  const deps: VisibilityDeps = { ...configured, ...opts.deps };
  let targets: ReadonlyMap<string, ReaderRow>;
  try {
    targets = await deps.labels(viewer.orgId, ids);
  } catch {
    return out;
  }
  const missing = ids.flatMap((id) => {
    const target = targets.get(id);
    return page.has(id) || target === undefined ? [] : [target];
  });
  const judged =
    missing.length === 0
      ? new Map<string, RowVerdict>()
      : await visibleRows(viewer, session, missing, {
          ...opts,
          labelsLoaded: true,
        });
  for (const id of ids) {
    const target = targets.get(id);
    out.set(id, {
      verdict: page.get(id) ?? judged.get(id),
      ver: target === undefined ? undefined : versionOf(target),
      versions:
        target === undefined
          ? 0
          : 1 + listOf<unknown>(target.labelHistory).length,
    });
  }
  return out;
}

/**
 * A reply keeps the words it quotes only for a reader who may see the answer they come from (S-1.8), and only as text
 * for one it was shared with, as the share shows the answer. An excerpt of a question that opens with a quote holds
 * an answer's words, not the question's, so it is withheld from everyone.
 */
const withCheckedReply = <M>(
  row: M,
  replies: ReadonlyMap<string, QuotedAnswer>,
): M => {
  const r = row as unknown as Plain;
  const reply = r.replyTo;
  // The version the excerpt was copied from is for this check alone.
  const { ver: copiedFrom, ...given } = isPlain(reply) ? reply : {};
  const withheld = (): M =>
    ({
      ...r,
      replyTo: { ...given, excerpt: '', withheld: true },
    }) as unknown as M;
  if (
    isPlain(reply) &&
    !QUOTES_AN_ANSWER.has(String(reply.kind)) &&
    typeof reply.excerpt === 'string' &&
    reply.excerpt
      .replace(/\s+/gu, ' ')
      .trimStart()
      .startsWith(QUOTE_HEADER.replace(/\s+/gu, ' ').trim())
  )
    return withheld();
  const target = replyTargetOf(r);
  if (target === undefined)
    return copiedFrom === undefined
      ? row
      : ({ ...r, replyTo: given } as unknown as M);
  const quoted = replies.get(target);
  // The excerpt is a copy of one version: a verdict on any other says nothing of it, and a share of a later version
  // never brings it back. A copy stored with no version is taken as of the only version there has been.
  const sameVersion =
    typeof copiedFrom === 'string' && copiedFrom !== ''
      ? copiedFrom === quoted?.ver
      : quoted?.versions === 1;
  if (!sameVersion) return withheld();
  if (quoted?.verdict?.verdict === 'visible')
    return { ...r, replyTo: given } as unknown as M;
  if (quoted?.verdict?.verdict !== 'consented') return withheld();
  let text: string;
  try {
    text = configured.sharedText(String(given.excerpt));
  } catch {
    text = '';
  }
  return text === ''
    ? withheld()
    : ({
        ...r,
        replyTo: { ...given, excerpt: text },
      } as unknown as M);
};

// ---- quotes ----------------------------------------------------------------------------------------------

const QUOTE_HEADER = 'Quoted excerpt:\n"""\n';

/**
 * A question that opens with a quoted excerpt of an answer names no answer, so no reader check can say who may
 * read the excerpt: only its author keeps it. Everyone else reads the question alone.
 */
export function withoutQuote<T extends object>(row: T): T {
  const r = row as Plain;
  if (
    (r.messageType !== 'user_query' && r.messageType !== 'note') ||
    typeof r.content !== 'string'
  )
    return row;
  if (!r.content.startsWith(QUOTE_HEADER)) return row;
  const view: Plain = { ...r, content: withoutQuotedExcerpt(r.content) };
  return view as T;
}

/**
 * A question's text without the excerpt of an answer it opens with: only what follows the last closing mark, as an
 * excerpt can hold the mark itself; a quote that never closes leaves nothing.
 */
export function withoutQuotedExcerpt(text: string): string {
  const { quoted, question } = splitQuotedSelection(text);
  return quoted ? question : text;
}

// ---- history to the model --------------------------------------------------------------------------------

export interface IncludableOptions extends VisibleRowsOptions {
  /** The row this turn's question replies to: another person's answer the sender brought in. */
  replyTargetId?: string;
  /**
   * Whether anyone but the sender reads the answer this history feeds: by default, whether anyone but its owner reads
   * `session`. A private thread's answer, built on its room's rows, is read by its asker alone.
   */
  othersRead?: boolean;
}

/**
 * The mark on a history row the model is given nothing of. Such a row goes to the AI backend with no `_ref`, `_ver`
 * or `_pub`, so the new answer's label never names it as a parent: a later step that writes words of its own in its
 * place must not make it read as given.
 */
export const WITHHELD = '_withheld';

/**
 * Whether a check withheld `row` from the sender's model: the reader check (`blank`), or the history filter, which
 * empties an answer that used a record the sender can no longer open and leaves its `answerHidden` marker on it.
 */
export const isWithheld = (row: object): boolean => {
  const r = row as Plain;
  return (
    r[WITHHELD] === true ||
    (r.answerHidden !== undefined && r.answerHidden !== null)
  );
};

/** What the model is given in place of a row it may not have: nothing, so no text, length or source of it reaches it. */
const blank = <R extends object>(row: R): R => {
  const r = row as Plain;
  const out: Plain = { ...r, content: '' };
  for (const field of [
    'parts',
    'tools',
    'referenceData',
    'attachments',
    'citations',
    'reasoning',
  ]) {
    if (field in out) out[field] = [];
  }
  delete out._ref;
  delete out._ver;
  delete out._pub;
  out[WITHHELD] = true;
  return out as R;
};

/**
 * The row and version a history row is, for the AI backend to name it as a parent of the new answer. A withheld row
 * stays unsealed: the model is given nothing of it.
 */
export const sealedRef = <R extends object>(row: R, pub = false): R => {
  const r = row as ReaderRow & Plain;
  const id = idOf(r._id);
  if (id === undefined || isWithheld(r)) return row;
  const sealed: Plain = { ...r, _ref: id, _ver: versionOf(r), _pub: pub };
  return sealed as R;
};

/**
 * The rows the sender's model may be given, each marked with its row and version
 * (`_ref`, `_ver`) and whether that version restricts nobody (`_pub`, which an outbound send's check reads).
 * Questions and notes go as they are, another person's without a quoted excerpt. The sender's
 * own answers go while they can still see them, whether or not they reply to them. Another person's answer goes
 * as text only when the sender can see it and either replied to it, or it was shared with them, or it restricts
 * nobody. Every other answer, card or error goes empty, so the model never reads it as an unanswered question.
 */
export async function includableRows<R extends object>(
  sender: Reader,
  session: ReaderSession | undefined,
  rows: R[],
  opts: IncludableOptions = {},
): Promise<R[]> {
  const reader = asMember(sender, session, opts.member ?? true);
  const ownerId = idOf(session?.userId);
  const asRows = rows as unknown as Array<ReaderRow & Plain>;
  // Judged now, past any stored verdict: the sender's own model reads what they may see at the start of the turn.
  const judged = await judgeRows(reader, session, asRows, {
    ...opts,
    member: true,
    fresh: true,
  });
  const { verdicts } = judged;
  // Whether a version restricts nobody is read under either rule: the outbound check reads it either way.
  const tree = judged.judge ?? (await labelTree(reader, session, asRows, opts));
  const memo = new Map<string, boolean>();
  const pubOf = (row: ReaderRow): boolean => {
    const id = idOf(row._id);
    const stored = id === undefined ? undefined : tree.rows.get(id);
    if (!stored || !isLabelledType(row)) return false;
    const ver = versionOf(row);
    return tree.pubAt(stored, ver, ver === versionOf(stored), memo);
  };
  const sealed = <T extends object>(m: T): T =>
    sealedRef(m, pubOf(m as unknown as ReaderRow));
  /** Whether the stored row (never the one passed) is shared with the sender, in the chat this turn is in. */
  const sharedWithSender = (row: ReaderRow): boolean => {
    const id = idOf(row._id);
    const stored = id === undefined ? undefined : tree.rows.get(id);
    return stored !== undefined && tree.covered(stored, versionOf(stored));
  };
  const deps: VisibilityDeps = { ...configured, ...opts.deps };
  // No label rule: the history the sender's own filter built goes as it is, marked.
  if (judged.perRecord) {
    return rows.map((m) => {
      const type = (m as Plain).messageType;
      return type === 'system' || type === 'feedback' ? m : sealed(m);
    });
  }
  /** A row read with no id has no stored label: shown before labels to its asker and to readers from then. */
  const idlessVerdict = (row: ReaderRow): RowVerdict | undefined => {
    if (!isLabelledType(row)) return undefined;
    if (!unlabelledShownTo(reader, session, row, ownerId, judged.switchAt)) {
      return undefined;
    }
    const asker = rowAuthorOf(row, ownerId);
    return {
      verdict: 'visible',
      basis: asker === reader.userId ? 'asker' : 'before_labels',
      validUntil: Date.now() + UNCHECKED_TTL_MS,
    };
  };
  const oldCards = cardsFromBeforeLabels(asRows)
  const everyMemberSaw = oldAnswersEveryMemberSaw(
    reader,
    session,
    asRows,
    (row) => {
      const rowId = idOf(row._id);
      return rowId === undefined ? idlessVerdict(row) : verdicts.get(rowId);
    },
    ownerId,
    judged.switchAt,
  );
  return rows.map((m) => {
    const row = m as unknown as ReaderRow & Plain;
    const id = idOf(row._id);
    const verdict = id === undefined ? idlessVerdict(row) : verdicts.get(id);
    const author = rowAuthorOf(row, ownerId);
    const own = author !== undefined && author === reader.userId;
    const type = row.messageType;
    if (type === 'user_query' || type === 'note') {
      // Someone else's answer to a card reaches no model but its author's, a row read with no id included.
      if (verdict?.cardWithheld === true || (!own && answersACard(row))) return sealed(withheldCardAnswer(m));
      return sealed(own ? m : withoutQuote(m));
    }
    if (type === 'system' || type === 'feedback') return m;
    if (type === SHARED_ANSWER) {
      return verdict?.verdict === 'consented' || verdict?.verdict === 'visible'
        ? sealed(m)
        : blank(m);
    }
    if (type === 'tool_call') {
      if (!own || verdict?.verdict !== 'visible') return blank(m);
      // A card from before labels, where others read the chat, goes with its turn's answer.
      return (opts.othersRead ?? othersRead(session)) && oldCards.has(row) && !everyMemberSaw.has(row)
        ? blank(m)
        : m;
    }
    if (
      !verdict ||
      verdict.verdict === 'hidden' ||
      verdict.verdict === 'unchecked'
    )
      return blank(m);
    if (own) {
      if (verdict.verdict !== 'visible') return blank(m);
      if (!(opts.othersRead ?? othersRead(session)) || versionOf(row) !== LEGACY_LABEL_VER) return sealed(m);
      // One's own answer from before labels goes as text where others read the chat, and only while every member
      // could read it then (each way into the chat older than the switch, and a turn that names no source), or when
      // the question replies to it.
      return everyMemberSaw.has(row) || (id !== undefined && id === opts.replyTargetId)
        ? sealed(textOnly(m))
        : blank(m);
    }
    // Shared with them, whether or not they could also read it whole: the text the share shows, and no more. An
    // answer built on it names it as a text parent, which every reader the share covers may see.
    if (verdict.verdict === 'consented' || sharedWithSender(row)) {
      const stored = id === undefined ? undefined : tree.rows.get(id);
      return stored ? sealed(sharedTextOf({ ...m, labelVer: stored.labelVer }, deps, stored)) : blank(m);
    }
    const replied = id !== undefined && id === opts.replyTargetId;
    // One they could read before the rules started: its text; where others read the chat, only while every member
    // could read it then, or when the question replies to it.
    if (verdict.basis === 'before_labels') {
      return (opts.othersRead ?? othersRead(session)) && versionOf(row) === LEGACY_LABEL_VER && !everyMemberSaw.has(row) && !replied
        ? blank(m)
        : sealed(textOnly(m));
    }
    // Another person's answer that restricts nobody, or the one the sender replied to.
    return replied || pubOf(row) ? sealed(textOnly(m)) : blank(m);
  });
}


/**
 * The cards among `rows` from before labels: a card carries no label of its own, so it is from before them when the
 * answer of its run is, or, for a card stamped with no run, the answer after it in its turn; with no such answer, a card
 * with no run is.
 */
function cardsFromBeforeLabels(rows: ReadonlyArray<ReaderRow & Plain>): Set<object> {
  const versionOfRun = new Map<string, string>()
  for (const row of rows) {
    const runId = runOf(row)
    if (runId !== undefined && isLabelledType(row) && !versionOfRun.has(runId)) versionOfRun.set(runId, versionOf(row))
  }
  const old = new Set<object>()
  rows.forEach((row, i) => {
    if (row.messageType !== 'tool_call') return
    const runId = runOf(row)
    if (runId !== undefined) {
      if (versionOfRun.get(runId) === LEGACY_LABEL_VER) old.add(row)
      return
    }
    for (const next of rows.slice(i + 1)) {
      if (next.messageType === 'user_query') break
      if (isLabelledType(next)) {
        if (versionOf(next) === LEGACY_LABEL_VER) old.add(row)
        return
      }
    }
    old.add(row)
  })
  return old
}

/** Whether a row names a record, a file or an address a per-record view would check. */
const namesASource = (row: Plain): boolean =>
  ['citations', 'attachments', 'referenceData'].some(
    (field) => Array.isArray(row[field]) && (row[field] as unknown[]).length > 0,
  ) ||
  [row.appliedFilters, row.filters].some(
    (filters) => isPlain(filters) && Object.values(filters).some((list) => Array.isArray(list) && list.length > 0),
  );

/**
 * The rows from before labels, visible to the sender, that every member could read before the switch: with each way
 * into the chat older than it, each answer, whoever asked it, not written under the rules whose turn (the question
 * before it, and it) names no source, and the sender's own cards of a turn whose answer is one.
 */
const oldAnswersEveryMemberSaw = (
  sender: Reader,
  session: ReaderSession | undefined,
  rows: ReadonlyArray<ReaderRow & Plain>,
  verdictOf: (row: ReaderRow) => RowVerdict | undefined,
  ownerId: string | undefined,
  switchAt: number | undefined,
): Set<object> => {
  const seen = new Set<object>();
  if (!session) return seen;
  const at = switchOf(session, switchAt);
  const everyWayInBefore =
    at === undefined ||
    (shareRowsOf(session).every((r) => sharedBefore(r, at)) &&
      (session.projectVisibility !== 'project' || projectVisibleBefore(session)));
  if (!everyWayInBefore) return seen;
  let question: Plain | undefined;
  let cards: Plain[] = [];
  let answered = false;
  const close = (): void => {
    if (answered) for (const card of cards) if (!namesASource(card)) seen.add(card);
    cards = [];
    answered = false;
  };
  for (const row of rows) {
    if (row.messageType === 'user_query') {
      close();
      question = row;
      continue;
    }
    if (versionOf(row) !== LEGACY_LABEL_VER || verdictOf(row)?.verdict !== 'visible') continue;
    if (row.messageType === 'tool_call') {
      if (rowAuthorOf(row, ownerId) === sender.userId) cards.push(row);
    } else if (
      isLabelledType(row) &&
      question !== undefined &&
      !unlabelledUnderRules(row, session) &&
      !namesASource(question) &&
      !namesASource(row)
    ) {
      seen.add(row);
      answered = true;
    }
  }
  close();
  return seen;
};

/** Whether anyone but its owner reads the chat: a share row, its project, or the readers of an agent room. */
const othersRead = (session: ReaderSession | undefined): boolean =>
  session !== undefined &&
  (shareRowsOf(session).length > 0 ||
    session.projectVisibility === 'project' ||
    idOf(session.agentRoomId) !== undefined);

/** The rows with their label fields and every version behind them, read without asking anyone's access. */
async function labelTree(
  reader: Reader,
  session: ReaderSession | undefined,
  rows: readonly ReaderRow[],
  opts: VisibleRowsOptions,
): Promise<Judge> {
  const judge = new Judge(
    reader,
    { ...configured, ...opts.deps },
    {
      session,
      ownerId: opts.ownerId,
      labelsLoaded: opts.labelsLoaded,
      chatsRead: opts.chatsRead,
    },
  );
  await judge.loadTree(rows);
  return judge;
}

/**
 * Whether `ver` of `row` restricts nobody, its label fields and every version behind it read from
 * the store: what a quote of the row may be marked with. A failed read is no.
 */
export async function pubVersion(
  orgId: string,
  row: ReaderRow,
  ver: string,
  opts: { deps?: Partial<VisibilityDeps>; labelsLoaded?: boolean } = {},
): Promise<boolean> {
  try {
    const tree = await labelTree({ userId: '', orgId }, undefined, [row], opts);
    const stored = tree.rows.get(idOf(row._id) ?? '');
    return (
      stored !== undefined && tree.pubAt(stored, ver, ver === versionOf(stored))
    );
  } catch {
    return false;
  }
}

/** An answer shared with the sender, as the share shows it to them: its shared text alone. */
const sharedTextOf = <R extends object>(
  row: R,
  deps: Pick<VisibilityDeps, 'sharedText'>,
  source: ReaderRow = row as ReaderRow,
): R => {
  let text: string;
  try {
    text = deps.sharedText(typeof source.content === 'string' ? source.content : '');
  } catch {
    // A share's text that cannot be made gives the model none of the answer, as `consentedView` shows a reader none.
    text = '';
  }
  const out: Plain = { ...(textOnly(row) as Plain), content: text };
  delete out.answerHidden;
  delete out[WITHHELD];
  return out as R;
};

/** Another person's answer as text: their tool output, references and files came with their access. */
const textOnly = <R extends object>(row: R): R => {
  const r = row as Plain;
  const out: Plain = { ...r };
  for (const field of [
    'parts',
    'tools',
    'referenceData',
    'attachments',
    'citations',
    'reasoning',
  ]) {
    if (field in out) out[field] = [];
  }
  return out as R;
};
