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
 * The check runs once per reader per read: one access request for every id the page's labels and the closures of
 * their versions name, at most `READ_IDS_MAX` per request. A version's closure (`closure.ts`) holds what every
 * version behind it was given; it is kept beside the rows, so a read fetches it instead of walking those versions.
 */
import {
  LABEL_UNKNOWN_VERSION,
  labelContentHash,
  labelProblems,
  labelStateOf,
  type LabelState,
} from './label';
import {
  LEGACY_LABEL_VER,
  type AnswerProvenance,
  type LabelParent,
} from './label.types';
import {
  READER_REV,
  VerdictStore,
  epochDigestOf,
  verdictKey,
  type StoredVerdict,
} from './verdict-store';
import {
  kbEpochKey,
  principalEpochKey,
  readEpochs,
  type EpochSource,
} from './epochs';
import {
  CHAIN_MAX,
  PUB_ROWS_MAX,
  CLOSURE_IDS_MAX,
  SHARES_SEEN_MAX,
  Fold,
  askerKey,
  closureFromStored,
  closureKey,
  isStoredClosure,
  mayBeBelow,
  shareId,
  storedClosure,
  type Closure,
  type ClosureStore,
  type ShareMarkRow,
  type StoredClosure,
  type VersionRef,
} from './closure';
import {
  ANSWER_LOST,
  ANSWER_UNCONFIRMED,
  beforeDeadline,
  newReadable,
  readableRecordIds,
  redactMessagesFor,
  startsTurn,
  type Readable,
  type ViewerMessage,
} from '../../../utils/viewer-redaction';
import type { AppConfig } from '../../../../tokens_manager/config/config';
import { splitQuotedSelection } from '../../../utils/quoted-selection';
import { getConversationRepository } from '../../../../../libs/repositories/conversation';
import { Logger } from '../../../../../libs/services/logger.service';

const logger = Logger.getInstance({ service: 'Reader Check' });

export { READER_REV };

const messageOf = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

/**
 * Warns once per outage that the closure store did not answer. Every read still judges right without it, making the
 * closures it needs from the labels, only slower; so one line says the store is down, not one per read. Any call the
 * store answers ends the outage.
 */
class ClosureStoreOutage {
  private open = false;

  failed(call: 'get' | 'put' | 'shares', orgId: string, error: unknown): void {
    if (this.open) return;
    this.open = true;
    logger.warn(
      'The answer closure store did not answer; reads make the closures they need from the labels until it does',
      { call, orgId, error: messageOf(error) },
    );
  }

  answered(): void {
    if (!this.open) return;
    this.open = false;
    logger.info('The answer closure store answers again');
  }

  reset(): void {
    this.open = false;
  }
}

const closureStoreOutage = new ClosureStoreOutage();

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
  /**
   * Stands for every member of the chat read at once (`everyMemberOf`), as its weakest one: in it only by its newest
   * way in, the asker of nothing, and able to open, use or read nothing of their own (no record, agent, other chat or
   * project). Each of these only ever adds to what a reader is shown, so what this reader is shown, every member is.
   * It asks no service anything, and no verdict of it is stored.
   */
  everyMember?: true;
}

/** The reader that stands for every member of a chat (`Reader.everyMember`); its id is no one's. */
export const everyMemberOf = (orgId: string): Reader => ({
  userId: '',
  orgId,
  member: true,
  everyMember: true,
});

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
  /** Whether the reader may read a project; undefined when it cannot be said now. */
  canReadProject(
    reader: Reader,
    projectId: string,
  ): Promise<boolean | undefined>;
  /** Whether an agent runs on a service account, bound or not; undefined when it cannot be said now. */
  isServiceAgent(orgId: string, agentKey: string): Promise<boolean | undefined>;
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
  /** Where closures are kept; none means every read makes the closures it needs from the labels. */
  closures?: ClosureStore;
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
/**
 * Rows a read may read to make the closures it needs and that were never stored, at most; past it the read falls back
 * to the walk, which hides what it cannot reach.
 */
const CLOSURE_ROWS_MAX = 5_000;
/** Rounds of shared versions and file writers followed from a page, at most; past it the read falls back to the walk. */
const CLOSURE_ROUNDS_MAX = 16;
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
          out.set(id, chat);
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
    await redactMessagesFor(
      (opts.appConfig ?? appConfigOf?.()) as AppConfig,
      reader,
      rows as unknown as ViewerMessage[],
      (m) => rowAuthorOf(m as ReaderRow, ownerId),
      {
        known: opts.known,
        timeoutMs: opts.timeoutMs,
        leadUnknown: opts.leadUnknown,
      },
    ),
});

/** Ids the closures this process keeps in memory may hold together; past it the least recently used go. */
const CLOSURE_CACHE_IDS = 200_000;

/**
 * Closures this process read from the store or stored, by org and key: the reads of one chat share them. Never
 * changed once kept; each read still checks a kept closure against the shares of its chats.
 */
class ClosureCache {
  private readonly entries = new Map<
    string,
    { closure: Closure; stored: StoredClosure; size: number }
  >();
  private total = 0;

  constructor(private readonly max: number) {}

  get(
    orgId: string,
    key: string,
  ): { closure: Closure; stored: StoredClosure } | undefined {
    const k = `${orgId}:${key}`;
    const entry = this.entries.get(k);
    if (!entry) return undefined;
    this.entries.delete(k);
    this.entries.set(k, entry);
    return { closure: entry.closure, stored: entry.stored };
  }

  set(
    orgId: string,
    key: string,
    closure: Closure,
    stored: StoredClosure,
  ): void {
    const k = `${orgId}:${key}`;
    this.drop(orgId, key);
    const size = 1 + closure.weight;
    if (size > this.max) return;
    this.entries.set(k, { closure, stored, size });
    this.total += size;
    while (this.total > this.max) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.total -= this.entries.get(oldest)?.size ?? 0;
      this.entries.delete(oldest);
    }
  }

  drop(orgId: string, key: string): void {
    const k = `${orgId}:${key}`;
    const entry = this.entries.get(k);
    if (!entry) return;
    this.total -= entry.size;
    this.entries.delete(k);
  }

  clear(): void {
    this.entries.clear();
    this.total = 0;
  }
}

const closureCache = new ClosureCache(CLOSURE_CACHE_IDS);
/** Closures a read of this process is making now, by org and key: another read that needs one waits for it. */
const closuresBeingMade = new Map<string, Promise<void>>();

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

/** Closures kept by the conversation repository: its own collection, beside the rows, never returned to a client. */
export const repositoryClosureStore = (): ClosureStore => ({
  get: async (orgId, keys) => {
    const docs = await getConversationRepository().listAnswerClosures(orgId, [
      ...keys,
    ]);
    // A document that is not a closure as this build stores them (another build's, or damaged) is left out: the read
    // makes that closure again, and its write replaces the document.
    const out = new Map<string, StoredClosure>();
    for (const [key, doc] of docs) if (isStoredClosure(doc)) out.set(key, doc);
    return out;
  },
  put: (orgId, closures) =>
    getConversationRepository().saveAnswerClosures(orgId, [...closures]),
  shares: (orgId, sessionIds) =>
    getConversationRepository().listShareMarks(orgId, [...sessionIds]),
});

/** Back to the unwired defaults (tests). */
export function resetVisibility(): void {
  appConfigOf = undefined;
  closureCache.clear();
  closuresBeingMade.clear();
  closureStoreOutage.reset();
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

/** What a reader who may not see a card gets of a question that answers it: that it was answered, never with what. */
export const CARD_ANSWER_WITHHELD = 'Answered a question from the assistant.';

const withheldCardAnswer = <T extends object>(row: T): T => {
  const view: Plain = { ...(row as Plain), content: CARD_ANSWER_WITHHELD };
  return view as T;
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

const legacyFileOfKey = (key: string): LegacyFile | undefined => {
  try {
    const value: unknown = JSON.parse(key);
    if (!Array.isArray(value) || value.length !== 3) return undefined;
    const [chatId, artifactId, writtenAt] = value as unknown[];
    return legacyFileOf({
      id: '',
      artifactId,
      conversationId: chatId,
      writtenAt,
    });
  } catch {
    return undefined;
  }
};

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
}

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

/** Each version of a row that carries a share, with it, as `consentAt` reads them: its current one, and archived ones. */
const sharesOfRow = (
  row: ShareMarkRow,
): Array<{ ver: string; consent: ConsentRecord }> => {
  const out: Array<{ ver: string; consent: ConsentRecord }> = [];
  const current =
    typeof row.labelVer === 'string' && row.labelVer !== ''
      ? row.labelVer
      : LEGACY_LABEL_VER;
  if (isPlain(row.consent) && row.consent.ver === current)
    out.push({ ver: current, consent: row.consent });
  for (const entry of listOf<unknown>(row.labelHistory)) {
    if (
      isPlain(entry) &&
      typeof entry.ver === 'string' &&
      isPlain(entry.consent) &&
      entry.consent.ver === entry.ver
    )
      out.push({ ver: entry.ver, consent: entry.consent });
  }
  return out;
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
  // Every member could read it then only when every way into it is older: each share row, and its project.
  if (reader.everyMember === true) {
    return (
      rows.every(before) &&
      (session.projectVisibility !== 'project' || projectVisibleBefore(session))
    );
  }
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
  /** Rows this judge may read to make closures; `CLOSURE_ROWS_MAX` unless said. */
  closureRowsMax?: number;
  /** Rows the walk of every version behind the rows (`prepareTree`) may read, at any depth; else 32 levels or 2,000 rows. */
  treeRowsMax?: number;
  /** The chats the read happens in, when not the session's (`VisibleRowsOptions.chatsRead`). */
  chatsRead?: readonly string[];
}

/**
 * One reader's judgement of a set of rows: the rows and the closures of the versions to judge are read first (a
 * closure holds everything the earlier versions behind a version name), then every id is asked in one request, then
 * each row is judged from what was read. The share check instead walks every earlier version (`prepareTree`).
 */
class Judge {
  readonly rows = new Map<string, ReaderRow>();
  private readonly memo = new Map<string, Outcome>();
  private readonly inProgress = new Set<string>();
  private readable: ReadableAnswer;
  private readonly agentUse = new Map<string, boolean | undefined>();
  private readonly chats = new Map<string, boolean | undefined>();
  private readonly projects = new Map<string, boolean | undefined>();
  /** Rows whose earlier versions were not all read (too deep or too many): they hide. */
  private readonly cut = new Set<string>();
  /** For each run a file parent names, the row versions it wrote in the chats read; empty when none was found. */
  private readonly writers = new Map<
    string,
    Array<{ id: string; ver: string }>
  >();
  private readonly sessionIds: Set<string>;
  private readonly sharesRead: ReadonlySet<string> | undefined;
  /**
   * The chats whose uploads a member reads without a check of that chat: those this read happens in. A fork deciding
   * what to copy names the fork (`chatsRead`), so an upload of the room is checked there as the fork's own reads check it.
   */
  private readonly uploadChats: ReadonlySet<string>;
  /** Answers from before labels named as `text` parents of someone else's asking, by id: how each fares for the reader. */
  private readonly legacyText = new Map<string, Outcome>();
  /** Files written before the rules, named with no run, by `legacyFileKey`: how each fares for the reader. */
  private readonly legacyFiles = new Map<string, Outcome>();
  /** Judged by closures (`prepare`), or by the walk of every earlier version (`prepareTree`, or a closure missing). */
  private byClosure = false;
  /** Each version's closure this read uses or made, by `id@ver`. */
  private readonly closures = new Map<string, Closure>();
  /** The rows given to judge: the chats the writers of their files are looked for in. */
  private given: readonly ReaderRow[] = [];

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

  /**
   * Reads `rows` (and their label fields when they do not carry them), the closure of each version to judge (their
   * own, and any of `versions`), and every id. A closure that cannot be had leaves the whole read to the walk.
   */
  async prepare(
    rows: readonly ReaderRow[],
    versions: readonly VersionRef[] = [],
  ): Promise<void> {
    await this.loadRows(rows);
    const folds = await this.loadClosures(rows, versions);
    if (folds) {
      this.byClosure = true;
      await this.settle([], folds);
      return;
    }
    await this.settle(await this.walkFrom(rows));
  }

  /** Reads `rows`, every version behind them a level at a time, and every id: what the share check walks. */
  async prepareTree(rows: readonly ReaderRow[]): Promise<void> {
    await this.settle(await this.loadTree(rows));
  }

  /** Reads `rows` with their label fields and every version behind them; returns the labels read. */
  async loadTree(rows: readonly ReaderRow[]): Promise<AnswerProvenance[]> {
    await this.loadRows(rows);
    return this.walkFrom(rows);
  }

  /** The rows to judge, with their stored label fields. */
  private async loadRows(rows: readonly ReaderRow[]): Promise<void> {
    this.given = rows;
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
              }
            : { ...given, provenance: undefined, labelHistory: undefined },
        );
      }
    }
  }

  /** Every earlier version `rows` (read with `loadRows`) were given, a level at a time; returns the labels read. */
  private async walkFrom(
    rows: readonly ReaderRow[],
  ): Promise<AnswerProvenance[]> {
    const orgId = this.reader.orgId;
    const rowsMax = this.opts.treeRowsMax ?? MAX_JUDGED_ROWS;
    const depthMax = this.opts.treeRowsMax ?? MAX_PARENT_DEPTH;
    let frontier: Array<{ id: string; ver: string; own: boolean }> = [];
    for (const row of rows) {
      const id = idOf(row._id);
      if (id !== undefined && isLabelledType(row)) {
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
        const { label } = versionAt(row, ver, own);
        if (!label) continue;
        labels.push(label);
        for (const parent of listOf<LabelParent>(label.parents)) {
          if (!isPlain(parent) || typeof parent.id !== 'string') continue;
          if (parent.id === '') {
            // A file the run read: read below by the run that wrote it, at the version that run wrote.
            const runId = writingRunOf(parent);
            if (runId === undefined || this.writers.has(runId)) continue;
            if (depth + 1 >= depthMax || this.rows.size >= rowsMax) {
              this.cut.add(`${id}@${ver}`);
              continue;
            }
            runs.add(runId);
            continue;
          }
          if (depth + 1 >= depthMax || this.rows.size >= rowsMax) {
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
    if (this.byClosure) return this.pubByClosure(row, ver, own);
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

  /**
   * One access request for every id the labels and closures name, and the agent, chat and project checks they need.
   */
  private async settle(
    labels: readonly AnswerProvenance[],
    folds: readonly Fold[] = [],
  ): Promise<void> {
    const ids = new Set<string>(this.opts.extraIds ?? []);
    const agents = new Set<string>();
    const chats = new Set<string>();
    const projects = new Set<string>();
    const legacy = new Set<string>();
    for (const fold of folds) {
      for (const part of fold.askers.values())
        for (const id of part.ids) ids.add(id);
      for (const key of fold.agents) agents.add(key);
      for (const cid of fold.uploads)
        if (cid !== '' && !this.uploadChats.has(cid)) chats.add(cid);
      for (const pid of fold.projects) projects.add(pid);
      for (const id of fold.legacy) legacy.add(id);
    }
    for (const label of labels) {
      for (const parent of listOf<LabelParent>(label.parents)) {
        if (!isPlain(parent) || parent.mode !== 'text') continue;
        if (parent.ver === LEGACY_LABEL_VER && typeof parent.id === 'string')
          legacy.add(parent.id);
      }
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
      this.settleLegacyText(legacy, timeoutMs),
      this.settleLegacyFiles(labels, folds),
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
   * The answers from before labels that the labels and closures read here name as `text` parents (`parentIds`) of
   * someone else's asking. One passes for a reader who could read its chat before the rules started,
   * when the per-record view of its turn still shows it to them, exactly as their own read of it would. Their chats
   * and turns are read once per chat, and their records asked within the read's own deadline. What cannot be read
   * leaves the parent unchecked; a row written under the rules, a reader added since, or a turn the view withholds
   * hides it.
   */
  private async settleLegacyText(
    parentIds: Iterable<string>,
    timeoutMs: number,
  ): Promise<void> {
    const wanted = new Map<string, ReaderRow>();
    for (const id of parentIds) {
      const row = this.rows.get(id);
      // Its asker reads it as ever. A row labelled since holds another version's text and sources, so the
      // version given cannot be checked: it is judged as before, for its asker alone.
      if (
        !row ||
        !isLabelledType(row) ||
        versionOf(row) !== LEGACY_LABEL_VER ||
        this.askerOf(row) === this.reader.userId
      )
        continue;
      wanted.set(id, row);
    }
    if (wanted.size === 0) return;
    const orgId = this.reader.orgId;
    const readId = idOf(this.opts.session?._id);
    // The turns are read within the read's one deadline: a turn the store has not given by then is not read.
    const deadline = this.opts.known?.deadline ?? Date.now() + timeoutMs;
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
            ...(await beforeDeadline(
              deadline,
              this.deps.rowsUpTo(orgId, sessionId, top, limit),
            )),
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
   * without both is its asker's alone. The rows judged count too: judged by closures, a read walks no label, and a
   * row's own files are judged at every read (`fileParent`).
   */
  private async settleLegacyFiles(
    labels: readonly AnswerProvenance[],
    folds: readonly Fold[] = [],
  ): Promise<void> {
    const wanted = new Map<string, LegacyFile>();
    const judged = this.given.flatMap((given) => {
      const id = idOf(given._id);
      const row = id === undefined ? undefined : this.rows.get(id);
      const label = row ? this.parentLabelOf(row, versionOf(row)) : undefined;
      return label ? [label] : [];
    });
    for (const label of [...labels, ...judged]) {
      for (const parent of listOf<LabelParent>(label.parents)) {
        const file = legacyFileOf(parent);
        if (file) wanted.set(legacyFileKey(file), file);
      }
    }
    for (const fold of folds) {
      for (const key of fold.legacyFiles) {
        const file = legacyFileOfKey(key);
        if (file) wanted.set(key, file);
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

  private judgeVersion(row: ReaderRow, ver: string, own: boolean): Outcome {
    if (this.byClosure && !own) {
      // As an earlier answer, a version is what its closure holds: also once its label has left the row's archive. A
      // version with none (from before labels, given as text) is judged as it is.
      const closure = this.closures.get(closureKey(idOf(row._id) ?? '', ver));
      if (closure)
        return combine([
          this.fromFold(closure.self),
          this.fromFold(closure.rest),
        ]);
    }
    const reader = this.reader;
    const asker = this.askerOf(row);
    const isAsker = asker !== undefined && asker === reader.userId;
    if (row.messageType === SHARED_ANSWER) {
      // A copy is shared with the chat: its members read its text. A removed copy no longer is.
      return reader.member === true && row.removedAt == null
        ? 'pass'
        : 'parent';
    }
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
    if (this.byClosure) {
      // Every earlier version it was given is in its closure; the files its run read are judged as the walk does.
      const closure = this.closures.get(closureKey(idOf(row._id) ?? '', ver));
      if (!closure) return 'parent';
      const files: Outcome[] = [];
      for (const parent of listOf<LabelParent>(label.parents)) {
        if (
          isPlain(parent) &&
          typeof parent.id === 'string' &&
          parent.id !== ''
        )
          continue;
        files.push(this.fileParent(parent, isAsker, own));
      }
      return combine([
        this.direct(label, isAsker),
        this.fromFold(closure.rest),
        ...files,
      ]);
    }
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
      outcomes.push(
        read === true ? 'pass' : read === false ? 'sources' : 'unchecked',
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
      if (
        !isPlain(parent) ||
        typeof parent.id !== 'string' ||
        parent.id === ''
      ) {
        outcomes.push(this.fileParent(parent, isAsker, own));
        continue;
      }
      const row = this.rows.get(parent.id);
      if (!row) {
        outcomes.push('parent');
        continue;
      }
      const ver = parent.ver;
      if (
        parent.mode === 'text' &&
        consentCovers(row, ver, this.reader, this.chatsRead)
      )
        continue;
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

  /**
   * A file the run read, as a parent of `label`: judged by the version of the answer whose run wrote it. A parent that
   * is no file, or a file whose run is not named or not found in the chats read, passes only for the asker's own run.
   */
  private fileParent(parent: unknown, isAsker: boolean, own: boolean): Outcome {
    if (!isPlain(parent)) return 'parent';
    const runId = writingRunOf(parent);
    const writers = runId === undefined ? [] : (this.writers.get(runId) ?? []);
    if (writers.length === 0) {
      // A file whose writing run is not named or not found here: its asker's own run read it. One named with no run
      // that was written before the rules started is judged as an answer from then (`settleLegacyFiles`).
      const file = runId === undefined ? legacyFileOf(parent) : undefined;
      const before = file
        ? this.legacyFiles.get(legacyFileKey(file))
        : undefined;
      return own && isAsker
        ? 'pass'
        : before === 'pass' || before === 'unchecked'
          ? before
          : 'parent';
    }
    // A file holds what the run that wrote it read: the version that run wrote is judged as any parent is.
    const outcomes: Outcome[] = [];
    for (const writer of writers) {
      const row = this.rows.get(writer.id);
      const outcome = row ? this.judge(row, writer.ver, false) : 'parent';
      outcomes.push(
        outcome === 'pass' || outcome === 'unchecked' ? outcome : 'parent',
      );
    }
    return combine(outcomes);
  }

  // ---- closures ----

  /** Whose a part of a closure is, as the chat read says (a row that names no asker is its chat owner's). */
  private askerOfKey(key: string): string | undefined {
    if (key.startsWith('u:')) return key.slice(2);
    const sessionId = key.slice(2);
    return this.ownerOfSession({
      sessionId: sessionId === '' ? undefined : sessionId,
    });
  }

  /**
   * How a fold fares for the reader as earlier answers of a row: it fails when anything in it does, and is unchecked
   * when something in it cannot be settled now. Every part is judged as the walk judges the version it came from.
   */
  private fromFold(fold: Fold): Outcome {
    const reader = this.reader;
    if (fold.broken) return 'parent';
    for (const p of fold.personal) if (p !== reader.userId) return 'parent';
    let unchecked = false;
    const add = (outcome: Outcome): boolean => {
      if (outcome === 'pass') return true;
      if (outcome === 'unchecked' || outcome === 'check_failed') {
        unchecked = true;
        return true;
      }
      return false;
    };
    for (const [key, part] of fold.askers) {
      const asker = this.askerOfKey(key);
      const isAsker = asker !== undefined && asker === reader.userId;
      if ((part.unsure || part.marks) && !isAsker) return 'parent';
      const denied: string[] = [];
      for (const id of part.ids) {
        if (this.readable.readable.has(id)) continue;
        if (this.readable.asked.has(id)) denied.push(id);
        else unchecked = true;
      }
      // The asker keeps an answer whose only missing sources were deleted; nobody else does.
      if (
        denied.length > 0 &&
        !(isAsker && denied.every((id) => this.readable.deleted.has(id)))
      )
        return 'parent';
    }
    for (const key of fold.agents) {
      const use = this.agentUse.get(key);
      if (!add(use === true ? 'pass' : use === false ? 'parent' : 'unchecked'))
        return 'parent';
    }
    for (const cid of fold.uploads) {
      if (cid !== '' && this.uploadChats.has(cid) && reader.member === true)
        continue;
      const read = this.chats.get(cid);
      if (
        !add(read === true ? 'pass' : read === false ? 'parent' : 'unchecked')
      )
        return 'parent';
    }
    for (const pid of fold.projects) {
      const read = this.projects.get(pid);
      if (
        !add(read === true ? 'pass' : read === false ? 'parent' : 'unchecked')
      )
        return 'parent';
    }
    for (const ref of fold.rows.values()) {
      const row = this.rows.get(ref.id);
      if (!row) return 'parent';
      const type = row.messageType;
      if (type === SHARED_ANSWER || type === 'user_query' || type === 'note') {
        // A copy, a question or a note: its members read it while it is there.
        if (reader.member !== true || row.removedAt != null) return 'parent';
        continue;
      }
      if (!add(this.judge(row, ref.ver, false))) return 'parent';
    }
    for (const runId of fold.files) {
      const writers = this.writers.get(runId) ?? [];
      if (writers.length === 0) return 'parent';
      for (const writer of writers) {
        const row = this.rows.get(writer.id);
        if (!row || !add(this.judge(row, writer.ver, false))) return 'parent';
      }
    }
    // A file saved before the rules, as an earlier answer's: who could read its chat then (`settleLegacyFiles`).
    for (const key of fold.legacyFiles)
      if (!add(this.legacyFiles.get(key) ?? 'parent')) return 'parent';
    // A share covers the reader only in the chats this read happens in (`chatsRead`), as the walk asks it: a fork
    // judging the room's rows is covered by no share given in the room.
    for (const ref of fold.alts.values()) {
      const row = this.rows.get(ref.id);
      if (!row) return 'parent';
      // Shared with the reader at the version given: its text is theirs, and nothing it was built on counts.
      if (consentCovers(row, ref.ver, reader, this.chatsRead)) continue;
      if (!add(this.judge(row, ref.ver, false))) return 'parent';
    }
    for (const id of fold.legacy) {
      const row = this.rows.get(id);
      if (!row) return 'parent';
      if (consentCovers(row, LEGACY_LABEL_VER, reader, this.chatsRead))
        continue;
      const before = this.legacyText.get(id);
      if (!add(before ?? this.judge(row, LEGACY_LABEL_VER, false)))
        return 'parent';
    }
    return unchecked ? 'unchecked' : 'pass';
  }

  /** Rows read to make closures in this read. */
  private closureRows = 0;

  /**
   * The closures this read judges by: of each version to judge, and of every version the judging reaches through a
   * share it may check or a file it follows, with the rows it reads as they are. Returns every fold the judging asks
   * about; undefined when a closure cannot be had (the walk then decides the read).
   */
  private async loadClosures(
    rows: readonly ReaderRow[],
    versions: readonly VersionRef[],
  ): Promise<Fold[] | undefined> {
    const orgId = this.reader.orgId;
    let wanted: VersionRef[] = [];
    for (const row of rows) {
      const id = idOf(row._id);
      if (id === undefined || !isLabelledType(row)) continue;
      wanted.push({ id, ver: versionOf(this.rows.get(id) as ReaderRow) });
    }
    wanted.push(...versions);
    const reached = new Set(wanted.map((r) => closureKey(r.id, r.ver)));
    const folds: Fold[] = [];
    for (let round = 0; wanted.length > 0; round += 1) {
      if (round >= CLOSURE_ROUNDS_MAX) return undefined;
      if (!(await this.makeClosures(wanted))) return undefined;
      const next: VersionRef[] = [];
      const unread = new Set<string>();
      const runIds = new Set<string>();
      const reach = (ref: VersionRef): void => {
        const key = closureKey(ref.id, ref.ver);
        if (reached.has(key)) return;
        reached.add(key);
        next.push(ref);
      };
      for (const ref of wanted) {
        const closure = this.closures.get(closureKey(ref.id, ref.ver));
        // A version of a row that does not exist: judged where it is named.
        if (!closure) continue;
        for (const fold of [closure.self, closure.rest]) {
          folds.push(fold);
          for (const alt of fold.alts.values()) {
            // Read as it is now: whether its share covers the reader.
            unread.add(alt.id);
            reach(alt);
          }
          // Read as it is now: whether a share check stops at it.
          for (const held of fold.held.values()) unread.add(held.id);
          for (const r of fold.rows.values()) unread.add(r.id);
          for (const id of fold.legacy) unread.add(id);
          for (const runId of fold.files) {
            const known = this.writers.get(runId);
            // Writers this judge found before (the share check's walk finds them too) still need their closures.
            if (known === undefined) runIds.add(runId);
            else for (const writer of known) reach(writer);
          }
        }
      }
      const toRead = [...unread].filter((id) => !this.rows.has(id));
      if (toRead.length > 0) {
        for (const [id, row] of await this.load(orgId, toRead))
          if (!this.rows.has(id)) this.rows.set(id, row);
      }
      if (runIds.size > 0) {
        for (const writer of await this.loadWriters(
          orgId,
          [...runIds],
          this.given,
        ))
          reach(writer);
      }
      wanted = next;
    }
    const store = this.deps.closures;
    if (store) {
      // Whether a version restricts nobody is read with the shares of the rows of its tree now.
      const chats = new Set<string>();
      for (const closure of this.closures.values())
        if (closure.pub.below)
          for (const chat of closure.pub.chats) chats.add(chat);
      if (!(await this.loadShares(store, [...chats]))) return undefined;
    }
    return folds;
  }

  /**
   * Makes the closure of each of `refs` this read does not have yet, from its row and the closures of the versions it
   * was given, reading rows a level at a time only where a closure is not known. False when one cannot be made: too
   * many rows to read, or versions that name each other.
   */
  private async makeClosures(refs: readonly VersionRef[]): Promise<boolean> {
    const store = this.deps.closures;
    const rowsMax = this.opts.closureRowsMax ?? CLOSURE_ROWS_MAX;
    const needs = new Map<string, { ref: VersionRef; deps: string[] }>();
    let frontier = refs.filter(
      (r) => !this.closures.has(closureKey(r.id, r.ver)),
    );
    // Another read of this process making these closures now: theirs are taken when it is done.
    const waits = store ? this.beingMade(frontier) : [];
    if (store && waits.length > 0) {
      await Promise.all(waits);
      await this.fromStore(store, frontier);
      frontier = frontier.filter(
        (r) => !this.closures.has(closureKey(r.id, r.ver)),
      );
    }
    const finish = store ? this.making(frontier) : undefined;
    try {
      return await this.makeNew(frontier, needs, rowsMax, store);
    } finally {
      finish?.();
    }
  }

  /** `makeClosures` for versions no read of this process is making. */
  private async makeNew(
    start: VersionRef[],
    needs: Map<string, { ref: VersionRef; deps: string[] }>,
    rowsMax: number,
    store: ClosureStore | undefined,
  ): Promise<boolean> {
    const orgId = this.reader.orgId;
    let frontier = start;
    while (frontier.length > 0) {
      if (store) await this.fromStore(store, frontier);
      const open = frontier.filter(
        (r) => !this.closures.has(closureKey(r.id, r.ver)),
      );
      const unread = [
        ...new Set(open.map((r) => r.id).filter((id) => !this.rows.has(id))),
      ];
      if (unread.length > 0) {
        this.closureRows += unread.length;
        if (this.closureRows > rowsMax) return false;
        for (const [id, row] of await this.load(orgId, unread))
          this.rows.set(id, row);
      }
      const next: VersionRef[] = [];
      const legacy = new Set<string>();
      for (const ref of open) {
        const key = closureKey(ref.id, ref.ver);
        if (needs.has(key) || this.closures.has(key)) continue;
        const row = this.rows.get(ref.id);
        if (!row) continue;
        const deps: string[] = [];
        const label = this.parentLabelOf(row, ref.ver);
        for (const parent of listOf<LabelParent>(label?.parents)) {
          if (!isPlain(parent) || typeof parent.id !== 'string') continue;
          if (parent.id === '' || typeof parent.ver !== 'string') continue;
          if (parent.mode === 'text' && parent.ver === LEGACY_LABEL_VER) {
            if (!this.rows.has(parent.id)) legacy.add(parent.id);
            continue;
          }
          deps.push(closureKey(parent.id, parent.ver));
          next.push({ id: parent.id, ver: parent.ver });
        }
        needs.set(key, { ref, deps });
      }
      if (legacy.size > 0) {
        const toRead = [...legacy].filter((id) => !this.rows.has(id));
        this.closureRows += toRead.length;
        if (this.closureRows > rowsMax) return false;
        for (const [id, row] of await this.load(orgId, toRead))
          if (!this.rows.has(id)) this.rows.set(id, row);
      }
      frontier = next.filter(
        (r) => !this.closures.has(closureKey(r.id, r.ver)),
      );
    }
    // Each closure once those it builds on are made: parents first. What is never ready names itself through others.
    const waiting = new Map<string, number>();
    const dependents = new Map<string, string[]>();
    const ready: string[] = [];
    for (const [key, { deps }] of needs) {
      const open = [...new Set(deps)].filter((d) => needs.has(d));
      waiting.set(key, open.length);
      for (const d of open) {
        const list = dependents.get(d) ?? [];
        list.push(key);
        dependents.set(d, list);
      }
      if (open.length === 0) ready.push(key);
    }
    if (store && needs.size > 0) {
      // A share decides whether a version given as text is folded in: read where those versions sit.
      const chats = new Set<string>();
      for (const { deps } of needs.values()) {
        for (const dep of deps) {
          const id = dep.slice(0, dep.indexOf('@'));
          const chat =
            this.closures.get(dep)?.sessionId ??
            idOf(this.rows.get(id)?.sessionId);
          if (chat !== undefined) chats.add(chat);
        }
      }
      if (!(await this.loadShares(store, [...chats]))) return false;
    }
    const made: Closure[] = [];
    while (ready.length > 0) {
      const key = ready.pop() as string;
      const need = needs.get(key);
      if (!need) continue;
      const closure = this.closureOf(need.ref);
      this.closures.set(key, closure);
      made.push(closure);
      for (const child of dependents.get(key) ?? []) {
        const left = (waiting.get(child) ?? 0) - 1;
        waiting.set(child, left);
        if (left === 0) ready.push(child);
      }
    }
    if (made.length !== needs.size) return false;
    if (store && made.length > 0) await this.keep(store, made);
    return true;
  }

  /** The label of `ver` of a row as an earlier answer; none for a copy, question or note, or one not relied on. */
  private parentLabelOf(
    row: ReaderRow,
    ver: string,
  ): AnswerProvenance | undefined {
    const type = row.messageType;
    if (type === SHARED_ANSWER || type === 'user_query' || type === 'note')
      return undefined;
    return versionAt(row, ver, false).label;
  }

  /**
   * Whether a version given as text was shared when its closure is made: a share can then cover a reader. With closures
   * stored, the shares read for its chat decide (`shares`), so what the closure took account of is exactly what it saw.
   */
  private sharedThen(
    id: string,
    ver: string,
    row: ReaderRow | undefined,
    chat: string | undefined,
  ): boolean {
    if (!this.deps.closures)
      return row !== undefined && consentAt(row, ver) !== undefined;
    return (this.shares.get(chat ?? '') ?? []).some(
      (share) => share.rowId === id && share.ver === ver,
    );
  }

  /** By chat, the shares read for this read's stored closures: each shared version and how it was shared. */
  private readonly shares = new Map<
    string,
    Array<{ rowId: string; ver: string; id: string }>
  >();

  /** The shares of `chats` not read yet in this read; false when they cannot be read. */
  private async loadShares(
    store: ClosureStore,
    chats: readonly string[],
  ): Promise<boolean> {
    const unread = [...new Set(chats)].filter((c) => !this.shares.has(c));
    if (unread.length === 0) return true;
    let rows: readonly ShareMarkRow[];
    try {
      rows = await store.shares(this.reader.orgId, unread);
    } catch (error) {
      closureStoreOutage.failed('shares', this.reader.orgId, error);
      return false;
    }
    closureStoreOutage.answered();
    for (const chat of unread) this.shares.set(chat, []);
    for (const row of rows) {
      const list = this.shares.get(row.sessionId);
      if (!list) continue;
      if (isPlain(row.consent)) this.sharedNow.add(row._id);
      for (const { ver, consent } of sharesOfRow(row))
        list.push({ rowId: row._id, ver, id: shareId(row._id, ver, consent) });
    }
    return true;
  }

  /** Rows of the chats whose shares were read that carry a share on their current version field now. */
  private readonly sharedNow = new Set<string>();

  /**
   * Whether a stored closure took account of every share there is now that could change it: one of a version made
   * before its own, in a chat whose rows it folded in. A share made since of such a version may cover a reader the
   * closure asks too much of, so it is made again.
   */
  private freshStored(closure: Closure): boolean {
    for (const chat of closure.spans) {
      const seen = new Set(closure.seen?.[chat] ?? []);
      for (const share of this.shares.get(chat) ?? []) {
        if (
          share.ver === LEGACY_LABEL_VER ||
          !mayBeBelow(share.ver, closure.ver)
        )
          continue;
        if (!seen.has(share.id)) return false;
      }
    }
    return true;
  }

  /**
   * By chat, the shares a closure made now takes account of: those of its chats it could be changed by, which every
   * closure it folded in took account of too.
   */
  private seenBy(
    closure: Closure,
    inputs: readonly Closure[],
  ): Record<string, string[]> | undefined {
    const out: Record<string, string[]> = {};
    for (const chat of closure.spans) {
      const kept: string[] = [];
      for (const share of this.shares.get(chat) ?? []) {
        if (
          share.ver === LEGACY_LABEL_VER ||
          !mayBeBelow(share.ver, closure.ver)
        )
          continue;
        const missed = inputs.some(
          (input) =>
            input.spans.has(chat) &&
            mayBeBelow(share.ver, input.ver) &&
            !(input.seen?.[chat] ?? []).includes(share.id),
        );
        if (!missed) kept.push(share.id);
      }
      if (kept.length > SHARES_SEEN_MAX) return undefined;
      out[chat] = kept;
    }
    return out;
  }

  /** Stored closures of `refs` this read has not tried yet: the fresh ones join this read's closures. */
  private async fromStore(
    store: ClosureStore,
    refs: readonly VersionRef[],
  ): Promise<void> {
    const orgId = this.reader.orgId;
    const keys = [
      ...new Set(
        refs
          .map((r) => closureKey(r.id, r.ver))
          .filter((k) => !this.closures.has(k) && !this.tried.has(k)),
      ),
    ];
    if (keys.length === 0) return;
    for (const key of keys) this.tried.add(key);
    const found = new Map<
      string,
      { closure: Closure; stored: StoredClosure }
    >();
    const missing: string[] = [];
    for (const key of keys) {
      const cached = closureCache.get(orgId, key);
      if (cached) found.set(key, cached);
      else missing.push(key);
    }
    if (missing.length > 0) {
      // A closure the store cannot give is one this read makes from the labels.
      let docs: ReadonlyMap<string, StoredClosure>;
      try {
        docs = await store.get(orgId, missing);
        closureStoreOutage.answered();
      } catch (error) {
        closureStoreOutage.failed('get', orgId, error);
        docs = new Map();
      }
      const chainKeys = new Set<string>();
      for (const doc of docs.values())
        for (const link of Array.isArray(doc.chain) ? doc.chain : [])
          if (!docs.has(link.key)) chainKeys.add(link.key);
      let chain: ReadonlyMap<string, StoredClosure> = new Map();
      if (chainKeys.size > 0) {
        try {
          chain = await store.get(orgId, [...chainKeys]);
          closureStoreOutage.answered();
        } catch (error) {
          closureStoreOutage.failed('get', orgId, error);
          chain = new Map();
        }
      }
      const all = new Map([...docs, ...chain]);
      for (const [key, doc] of docs) {
        const closure = closureFromStored(doc, all);
        if (!closure) continue;
        found.set(key, { closure, stored: doc });
        closureCache.set(orgId, key, closure, doc);
      }
    }
    if (found.size === 0) return;
    const chats = new Set<string>();
    for (const { closure } of found.values())
      for (const chat of closure.spans) chats.add(chat);
    if (!(await this.loadShares(store, [...chats]))) return;
    for (const [key, { closure, stored }] of found) {
      if (!this.freshStored(closure)) {
        closureCache.drop(orgId, key);
        continue;
      }
      this.closures.set(key, closure);
      this.storedDocs.set(key, stored);
    }
  }

  /** Keys this read looked for in the store. */
  private readonly tried = new Set<string>();
  /** The stored form of each closure this read took from the store or stored: what a new one may be built on. */
  private readonly storedDocs = new Map<string, StoredClosure>();

  /** Stores the closures this read made, each built on the largest stored closure it folded in whole. */
  private async keep(
    store: ClosureStore,
    made: readonly Closure[],
  ): Promise<void> {
    const orgId = this.reader.orgId;
    const at = new Date(this.deps.now());
    const docs: StoredClosure[] = [];
    for (const closure of made) {
      const inputs = closure.folded
        .map((key) => this.closures.get(key))
        .filter((c): c is Closure => c !== undefined);
      const seen = this.seenBy(closure, inputs);
      if (!seen) continue;
      closure.seen = seen;
      let base: { stored: StoredClosure; closure: Closure } | undefined;
      let largest = -1;
      for (const input of inputs) {
        const stored = this.storedDocs.get(closureKey(input.id, input.ver));
        if (!stored || stored.chain.length + 1 > CHAIN_MAX) continue;
        if (input.weight <= largest) continue;
        largest = input.weight;
        base = { stored, closure: input };
      }
      const doc = storedClosure(orgId, closure, seen, base, at);
      docs.push(doc);
      this.storedDocs.set(doc._id, doc);
    }
    if (docs.length === 0) return;
    try {
      await store.put(orgId, docs);
      closureStoreOutage.answered();
    } catch (error) {
      // Not kept: the next read that needs them makes them again.
      closureStoreOutage.failed('put', orgId, error);
      return;
    }
    for (const doc of docs) {
      const closure = this.closures.get(doc._id);
      if (closure) closureCache.set(orgId, doc._id, closure, doc);
    }
  }

  /** What the reads of this process already making any of `refs` will be done by. */
  private beingMade(refs: readonly VersionRef[]): Array<Promise<void>> {
    const orgId = this.reader.orgId;
    return [
      ...new Set(
        refs
          .map((r) =>
            closuresBeingMade.get(`${orgId}:${closureKey(r.id, r.ver)}`),
          )
          .filter((w): w is Promise<void> => w !== undefined),
      ),
    ];
  }

  /** Marks `refs` as being made by this read until the returned function is called. */
  private making(refs: readonly VersionRef[]): () => void {
    const orgId = this.reader.orgId;
    let finish: () => void = () => undefined;
    const done = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const keys = refs
      .map((r) => `${orgId}:${closureKey(r.id, r.ver)}`)
      .filter((k) => !closuresBeingMade.has(k));
    for (const key of keys) closuresBeingMade.set(key, done);
    return () => {
      for (const key of keys) {
        if (closuresBeingMade.get(key) === done) closuresBeingMade.delete(key);
      }
      finish();
    };
  }

  /**
   * The closure of `ref`: the version's own label as an earlier answer (`self`), and everything the versions it was
   * given bring (`rest`), made from their closures, which are made first.
   */
  private closureOf(ref: VersionRef): Closure {
    const row = this.rows.get(ref.id) as ReaderRow;
    const sessionId = idOf(row.sessionId);
    const self = new Fold();
    const rest = new Fold();
    const folded: string[] = [];
    const closure: Closure = {
      id: ref.id,
      ver: ref.ver,
      ...(sessionId !== undefined && { sessionId }),
      self,
      rest,
      folded,
      weight: 0,
      spans: rest.spans,
      pub: { self: false, below: false, rows: [], chats: [] },
    };
    const type = row.messageType;
    if (type === SHARED_ANSWER || type === 'user_query' || type === 'note') {
      // Read only while it is there, by members: judged as it is then.
      self.addRow(ref);
      return { ...closure, weight: self.size };
    }
    const { label, state } = versionAt(row, ref.ver, false);
    const asker = askerKey(idOf(row.requestedBy), sessionId);
    if (state !== 'ok' || !label) {
      // Its asker reads past it; nobody else does, and nothing behind it is read.
      self.part(asker).unsure = true;
      return { ...closure, weight: self.size };
    }
    const direct: Plain = isPlain(label.direct) ? label.direct : {};
    const part = self.part(asker);
    for (const id of listOf<unknown>(direct.records))
      if (typeof id === 'string') part.ids.add(id);
    for (const id of listOf<unknown>(direct.nodes))
      if (typeof id === 'string') part.ids.add(id);
    if (listOf(direct.marks).length > 0) part.marks = true;
    for (const p of listOf<unknown>(direct.personal))
      if (typeof p === 'string') self.personal.add(p);
    for (const a of listOf<unknown>(direct.agents))
      if (isPlain(a) && typeof a.key === 'string') self.agents.add(a.key);
    for (const c of listOf<unknown>(direct.chat))
      if (isPlain(c) && typeof c.conversationId === 'string')
        self.uploads.add(c.conversationId);
    for (const p of listOf<unknown>(direct.projects))
      if (typeof p === 'string') self.projects.add(p);
    if (listOf(direct.saVrids).length > 0) self.service = true;
    const parents = listOf<unknown>(label.parents);
    const candidates: Closure[] = [];
    for (const parent of parents) {
      if (!isPlain(parent)) {
        rest.broken = true;
        continue;
      }
      if (typeof parent.id !== 'string' || parent.id === '') {
        // A file: judged by its writer, found in the chats the read covers; one saved before the rules, which no run
        // wrote, by who could read its chat then, at each read.
        const runId = writingRunOf(parent);
        const file = runId === undefined ? legacyFileOf(parent) : undefined;
        if (file) self.legacyFiles.add(legacyFileKey(file));
        else if (runId === undefined) self.broken = true;
        else self.files.add(runId);
        continue;
      }
      const prow = this.rows.get(parent.id);
      const pver = parent.ver;
      const below =
        typeof pver === 'string'
          ? this.closures.get(closureKey(parent.id, pver))
          : undefined;
      // A version whose closure is stored was there when it was made; rows are never deleted from under a later one.
      if ((!prow && !below) || typeof pver !== 'string') {
        rest.broken = true;
        continue;
      }
      const psession = below?.sessionId ?? idOf(prow?.sessionId);
      if (parent.mode === 'text' && pver === LEGACY_LABEL_VER) {
        if (!prow) rest.broken = true;
        else rest.legacy.add(parent.id);
        continue;
      }
      if (
        parent.mode === 'text' &&
        this.sharedThen(parent.id, pver, prow, psession)
      ) {
        rest.addAlt({ id: parent.id, ver: pver });
        if (psession !== undefined) rest.spans.add(psession);
        continue;
      }
      if (!below) {
        rest.broken = true;
        continue;
      }
      if (this.sharedThen(parent.id, pver, prow, psession))
        rest.addHeld({ id: parent.id, ver: pver });
      candidates.push(below);
    }
    // The largest first: a parent folded into one already taken is inside it whole, and is never built.
    candidates.sort((a, b) => b.weight - a.weight);
    const inside = new Set<string>();
    for (const c of candidates) {
      const key = closureKey(c.id, c.ver);
      folded.push(key);
      if (inside.has(key)) continue;
      rest.merge(c.self);
      rest.merge(c.rest);
      if (c.sessionId !== undefined) rest.spans.add(c.sessionId);
      inside.add(key);
      for (const k of c.folded) inside.add(k);
    }
    if (rest.size > CLOSURE_IDS_MAX) {
      // Too much to keep: read as a version whose earlier answers could not all be read.
      const cut = new Fold();
      cut.broken = true;
      closure.rest = cut;
      closure.spans = cut.spans;
    }
    closure.weight = self.size + closure.rest.size;
    closure.pub = this.pubPartOf(label, parents);
    return closure;
  }

  /**
   * Whether `label` restricts nobody by what is stored: its own conditions as an earlier answer, and every
   * version below it; the rows of that tree are read again for a share when the mark is asked (`pubAt`).
   */
  private pubPartOf(
    label: AnswerProvenance,
    parents: readonly unknown[],
  ): Closure['pub'] {
    // `label` passed its check as a parent's (`versionAt`): complete, and its fields of the types they name.
    const direct: Plain = isPlain(label.direct) ? label.direct : {};
    const self =
      label.complete &&
      listOf(label.incomplete).length === 0 &&
      !Object.values(direct).some((ids) => listOf(ids).length > 0) &&
      (parents.length > 0 || label.pub);
    const not: Closure['pub'] = { self, below: false, rows: [], chats: [] };
    const rows = new Set<string>();
    const chats = new Set<string>();
    for (const parent of parents) {
      if (!isPlain(parent) || typeof parent.id !== 'string') return not;
      // What reached the run through someone else's share, or an artifact, was never the sender's to send.
      if (parent.mode === 'grant' || parent.mode === 'preswitch') return not;
      if (typeof parent.artifactId === 'string' && parent.artifactId !== '')
        return not;
      const below =
        typeof parent.ver === 'string'
          ? this.closures.get(closureKey(parent.id, parent.ver))
          : undefined;
      if (!below || !below.pub.self || !below.pub.below) return not;
      rows.add(parent.id);
      if (below.sessionId !== undefined) chats.add(below.sessionId);
      for (const id of below.pub.rows) rows.add(id);
      for (const chat of below.pub.chats) chats.add(chat);
      if (rows.size > PUB_ROWS_MAX) return not;
    }
    return { self, below: true, rows: [...rows], chats: [...chats] };
  }

  /**
   * Whether `ver` of `row` restricts nobody, by its closure: its own label as read now, and every row of the tree
   * below it, none shared now.
   */
  private pubByClosure(row: ReaderRow, ver: string, own: boolean): boolean {
    if (row.consent != null) return false;
    const { state, label } = versionAt(row, ver, own);
    if (state !== 'ok' || !label?.complete) return false;
    if (listOf(label.incomplete).length > 0) return false;
    const direct: Plain = isPlain(label.direct) ? label.direct : {};
    if (Object.values(direct).some((ids) => listOf(ids).length > 0))
      return false;
    if (listOf(label.parents).length === 0 && !label.pub) return false;
    const closure = this.closures.get(closureKey(idOf(row._id) ?? '', ver));
    if (!closure?.pub.below) return false;
    if (closure.pub.rows.length === 0) return true;
    if (this.deps.closures) {
      // The rows below were not read: the shares of their chats were (`loadClosures`).
      if (closure.pub.chats.some((c) => !this.shares.has(c))) return false;
      return closure.pub.rows.every((id) => !this.sharedNow.has(id));
    }
    return closure.pub.rows.every((id) => {
      const below = this.rows.get(id);
      return below !== undefined && below.consent == null;
    });
  }

  /**
   * Whether each of `viewers` may read `ver` of `row` whole, as `fullOK` says, after this judge read the row (the share
   * check's walk): the closures are made once from what was read, and each viewer gets their own access request, a few
   * at a time.
   */
  async openFor(
    viewers: readonly Reader[],
    row: ReaderRow,
    ver: string,
  ): Promise<boolean[]> {
    const id = idOf(row._id);
    const folds =
      id === undefined ? undefined : await this.loadClosures([], [{ id, ver }]);
    const own = ver === versionOf(row);
    return eachAtMost(viewers, PEOPLE_AT_ONCE, async (viewer) => {
      if (!folds)
        return fullOK(viewer, row, ver, {
          session: this.opts.session,
          deps: this.deps,
        });
      const judge = this.judgeFor(
        asMember(viewer, this.opts.session, undefined),
      );
      await judge.settle([], folds);
      return judge.judge(row, ver, own) === 'pass';
    });
  }

  /**
   * The share check (`widenOK`) of `ver` of `row` by its asker, from its closure, as the walk of every version behind it
   * answers it: undefined when it may be shared, the refusal otherwise; 'walk' when only the walk's order says which
   * refusal (an agent's work below the answer, or a version given in full that the share check would stop at, being
   * shared in this chat).
   */
  widenByClosures(
    row: ReaderRow,
    ver: string,
    scope: 'chat' | 'outside',
  ): WidenResult | undefined | 'walk' {
    if (!this.byClosure) return 'walk';
    const { label, state } = versionAt(row, ver, ver === versionOf(row));
    if (state !== 'ok' || !label) return NOT_WIDENABLE;
    const direct: Plain = isPlain(label.direct) ? label.direct : {};
    if (listOf(direct.saVrids).length > 0) return AGENT_CONTENT;
    if (listOf<unknown>(direct.agents).length > 0) return AGENT_CONTENT;
    const closure = this.closures.get(closureKey(idOf(row._id) ?? '', ver));
    if (!closure) return 'walk';
    if (!this.opensWhole(closure.self)) return NOT_WIDENABLE;
    const sessionId = idOf(row.sessionId);
    // Where the walk stops: a version shared with everyone in this chat, for a share inside it.
    const stopsAt = (ref: VersionRef): boolean => {
      if (scope !== 'chat') return false;
      const shared = this.rows.get(ref.id);
      const consent = shared ? consentAt(shared, ref.ver) : undefined;
      return (
        consent !== undefined &&
        (consent.scope ?? 'everyone') === 'everyone' &&
        idOf(shared?.sessionId) === sessionId
      );
    };
    const checked: Fold[] = [closure.self, closure.rest];
    const below: Fold[] = [];
    const seen = new Set<string>([closureKey(closure.id, closure.ver)]);
    const visited: Closure[] = [closure];
    for (let i = 0; i < checked.length + below.length; i += 1) {
      const fold = i < checked.length ? checked[i] : below[i - checked.length];
      if (!fold) continue;
      const stopped = i >= checked.length;
      if (!stopped)
        for (const held of fold.held.values()) if (stopsAt(held)) return 'walk';
      for (const alt of fold.alts.values()) {
        const key = closureKey(alt.id, alt.ver);
        if (seen.has(key)) continue;
        seen.add(key);
        const altClosure = this.closures.get(key);
        if (!altClosure || !this.rows.has(alt.id)) return 'walk';
        visited.push(altClosure);
        // Past a share the walk still looks for an agent's work, and nothing else.
        (stopped || stopsAt(alt) ? below : checked).push(
          altClosure.self,
          altClosure.rest,
        );
      }
    }
    if ([...checked, ...below].some((f) => f.agents.size > 0 || f.service))
      return 'walk';
    // Nothing else refuses a share but as not widenable, in any order: a label not relied on, a file, a copy, a
    // question or note, an answer from before labels, an earlier row that is gone, or a source its asker cannot open.
    for (const fold of checked) {
      if (fold.broken || fold.files.size > 0 || fold.legacyFiles.size > 0)
        return NOT_WIDENABLE;
      // An answer from before labels, a question or a note refuses unless shared here; a copy refuses always.
      for (const id of fold.legacy)
        if (!stopsAt({ id, ver: LEGACY_LABEL_VER })) return NOT_WIDENABLE;
      for (const ref of fold.rows.values()) {
        if (
          this.rows.get(ref.id)?.messageType === SHARED_ANSWER ||
          !stopsAt(ref)
        )
          return NOT_WIDENABLE;
      }
      if ([...fold.askers.values()].some((part) => part.unsure))
        return scope === 'chat' && this.legacyShareIn(visited)
          ? 'walk'
          : NOT_WIDENABLE;
      if (!this.opensWhole(fold)) return NOT_WIDENABLE;
    }
    return undefined;
  }

  /**
   * Whether a share on a version from before labels may stop the share check inside `closures`. A kept closure does
   * not follow such shares (an owner's one-step consent shares every such answer of a chat at once, which would remake
   * every closure kept there), so one folded whole before it was shared reads as not shared. Where a chat they fold
   * rows of holds such a share now, or its shares were not read, the walk decides, reading each share as it is.
   */
  private legacyShareIn(closures: readonly Closure[]): boolean {
    if (!this.deps.closures) return false;
    for (const closure of closures) {
      for (const chat of closure.spans) {
        const shares = this.shares.get(chat);
        if (shares === undefined) return true;
        if (shares.some((share) => share.ver === LEGACY_LABEL_VER)) return true;
      }
    }
    return false;
  }

  /**
   * Whether the reader can open everything a fold names, as a share's check reads a label (`directOf`): nobody's
   * personal results but theirs, no mark, every source, upload and project, nothing excused and nothing unsettled.
   */
  private opensWhole(fold: Fold): boolean {
    const reader = this.reader;
    for (const p of fold.personal) if (p !== reader.userId) return false;
    for (const part of fold.askers.values()) {
      if (part.marks) return false;
      for (const id of part.ids)
        if (!this.readable.readable.has(id)) return false;
    }
    for (const cid of fold.uploads) {
      if (cid !== '' && this.uploadChats.has(cid) && reader.member === true)
        continue;
      if (this.chats.get(cid) !== true) return false;
    }
    for (const pid of fold.projects)
      if (this.projects.get(pid) !== true) return false;
    return true;
  }

  /** A judge of the rows and closures this one read, for another reader: their own checks, nothing read again. */
  private judgeFor(reader: Reader): Judge {
    const judge = new Judge(reader, this.deps, this.opts);
    for (const [id, row] of this.rows) judge.rows.set(id, row);
    for (const [key, closure] of this.closures)
      judge.closures.set(key, closure);
    for (const [runId, writers] of this.writers)
      judge.writers.set(runId, writers);
    judge.given = this.given;
    judge.byClosure = true;
    return judge;
  }

  /** Makes and stores the closures of `rows`' current versions (rows given with their label fields), within its budget. */
  async storeClosuresOf(rows: readonly ReaderRow[]): Promise<void> {
    await this.loadRows(rows);
    const refs: VersionRef[] = [];
    for (const row of rows) {
      const id = idOf(row._id);
      if (id === undefined || !isLabelledType(row)) continue;
      refs.push({ id, ver: versionOf(this.rows.get(id) as ReaderRow) });
    }
    await this.makeClosures(refs);
  }

  /** `rows` and the closures of their versions (and of `versions`), read without asking anyone's access. */
  async prepareLabels(
    rows: readonly ReaderRow[],
    versions: readonly VersionRef[] = [],
  ): Promise<void> {
    await this.loadRows(rows);
    if (await this.loadClosures(rows, versions)) {
      this.byClosure = true;
      return;
    }
    await this.walkFrom(rows);
  }
}

/** Rows a write may read to make the closures of the answers it stores; past it, the first read makes them. */
const WRITE_CLOSURE_ROWS_MAX = 200;

/**
 * Makes and stores the closures of answers just written (rows with their label fields), from the stored closures of
 * the versions they were given. Never fails the write: a closure not made here is made by the first read that needs it.
 */
export async function rememberAnswerClosures(
  orgId: string,
  rows: readonly ReaderRow[],
): Promise<void> {
  const deps = configured;
  if (!deps.closures) return;
  const labelled = rows.filter(
    (r) =>
      isLabelledType(r) && idOf(r._id) !== undefined && r.provenance != null,
  );
  if (labelled.length === 0) return;
  try {
    const judge = new Judge({ userId: '', orgId }, deps, {
      labelsLoaded: true,
      closureRowsMax: WRITE_CLOSURE_ROWS_MAX,
    });
    await judge.storeClosuresOf(labelled);
  } catch (error) {
    // The answer is stored already: failing its write now would lose the reply, and the first read that needs the
    // closure makes it. The store's own failures are reported where they happen; this is anything else.
    logger.warn(
      'Answer closures not made at the write; the first read that needs them makes them',
      { orgId, answers: labelled.length, error: messageOf(error) },
    );
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
   * one here (`consentCovers`), and an upload to any other chat counts only if the reader may read that chat. A fork
   * deciding what to copy reads the room's rows as the fork will read them.
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

/**
 * A read's answers that asks the access service nothing (`Readable.askNoOne`), within `deadline` when given: its
 * reader's store reads keep their bound.
 */
const askingNothing = (deadline?: number): Readable => ({
  ...newReadable(),
  askNoOne: true,
  ...(deadline !== undefined && { deadline }),
});

/**
 * What a check of `reader` depends on. For the reader that stands for every member (`Reader.everyMember`), what they
 * pass of their own is nothing, settled at once (no record, agent, other chat or project), and the per-record view
 * of a turn asks nothing for them: a turn that names a record leaves its answer hidden.
 */
const depsFor = (reader: Reader, deps: VisibilityDeps): VisibilityDeps =>
  reader.everyMember !== true
    ? deps
    : {
        ...deps,
        readable: (_reader, ids) =>
          Promise.resolve({
            readable: new Set<string>(),
            asked: new Set(ids),
            deleted: new Set<string>(),
            failed: false,
            validUntil: deps.now() + VERDICT_TTL_MS,
          }),
        canUseAgent: () => Promise.resolve(false),
        canReadConversation: () => Promise.resolve(false),
        canReadProject: () => Promise.resolve(false),
        perRecordView: (each, rows, ownerId, viewOpts) =>
          deps.perRecordView(each, rows, ownerId, {
            ...viewOpts,
            known: askingNothing(viewOpts.known?.deadline),
          }),
      };

async function judgeRows(
  viewer: Reader,
  session: ReaderSession | undefined,
  rows: readonly ReaderRow[],
  opts: VisibleRowsOptions,
): Promise<Judged> {
  const deps = depsFor(viewer, { ...configured, ...opts.deps });
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
  // answer among them need neither, and the reader that stands for every member is no one's to store a verdict for.
  const digest =
    opts.fresh === true || passed.length === 0 || reader.everyMember === true
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
  if (toJudge.length > 0 || (opts.extraIds?.length ?? 0) > 0) {
    await judge.prepare(toJudge);
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
      if (
        verdict.verdict === 'hidden' &&
        consentCovers(row, versionOf(row), reader, judge.chatsRead)
      ) {
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
        reader.member === true && row.removedAt == null
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
  // A question that answers a card holds the card's words: anyone but its author reads them only while they may read
  // the card. The card is the one it was bound to; for one stored before that was recorded, the card before it in its
  // turn among the rows, else none, which withholds it.
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
    if (id === undefined || (named === undefined && !isUnboundCardAnswer(row)))
      return;
    if (rowAuthorOf(row, rowsOwnerId) === reader.userId) return;
    const card = named !== undefined ? cards.cards.get(named) : cardBefore(i);
    const cardId = card === undefined ? undefined : idOf(card._id);
    const shown =
      card === undefined
        ? undefined
        : ((cardId !== undefined ? out.get(cardId) : undefined) ??
          cardVerdict(card, undefined));
    if (shown?.verdict === 'visible') return;
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

/**
 * Each of `readers`' verdict of `row`, in their order, as `visibleRows` gives it to each of them alone: what a notice
 * of the row asks, to tell only the people it may be shown to. What is the same whoever reads (the row's stored label
 * and closure, the shares of its chats, the chats and runs it names) is read once for all of them (`readOnce`); each
 * reader then gets their own judgement and access request, `PEOPLE_AT_ONCE` at a time, as `Judge.openFor` checks the
 * people a share names. Undefined for a row with no id.
 */
export async function visibleToEach(
  readers: readonly Reader[],
  session: ReaderSession | undefined,
  row: ReaderRow,
  opts: VisibleRowsOptions = {},
): Promise<Array<RowVerdict | undefined>> {
  const id = idOf(row._id);
  if (id === undefined) return readers.map(() => undefined);
  const deps = readOnce({ ...configured, ...opts.deps });
  return eachAtMost(readers, PEOPLE_AT_ONCE, async (reader) =>
    (await judgeRows(reader, session, [row], { ...opts, deps })).verdicts.get(
      id,
    ),
  );
}

/**
 * `deps` for one check of many readers: every read that is the same whoever reads is made once for the same
 * arguments, among them the stored rows and runs, the chats and their audience epochs, the rule, the switch, and the
 * closure store's closures and shares. The access request, the agent, chat and project checks, the per-record view
 * (and the rows it is given), the epochs and the stored verdicts stay each reader's own.
 */
const readOnce = (deps: VisibilityDeps): VisibilityDeps => {
  const store = deps.closures;
  return {
    ...deps,
    labels: onceEach((orgId: string, ids: readonly string[]) =>
      deps.labels(orgId, ids),
    ),
    runs: onceEach(
      (
        orgId: string,
        sessionIds: readonly string[],
        runIds: readonly string[],
      ) => deps.runs(orgId, sessionIds, runIds),
    ),
    chats: onceEach((orgId: string, ids: readonly string[]) =>
      deps.chats(orgId, ids),
    ),
    audiences: onceEach((orgId: string, chatIds: readonly string[]) =>
      deps.audiences(orgId, chatIds),
    ),
    isServiceAgent: onceEach((orgId: string, agentKey: string) =>
      deps.isServiceAgent(orgId, agentKey),
    ),
    labelRule: onceEach((orgId: string) => deps.labelRule(orgId)),
    switchAt: onceEach((orgId: string) => deps.switchAt(orgId)),
    ...(store && {
      closures: {
        get: onceEach((orgId: string, keys: readonly string[]) =>
          store.get(orgId, keys),
        ),
        put: (orgId, closures) => store.put(orgId, closures),
        shares: onceEach((orgId: string, sessionIds: readonly string[]) =>
          store.shares(orgId, sessionIds),
        ),
      },
    }),
  };
};

/** `read` once for each list of arguments: callers with the same ones, at the same time or later, share its answer. */
const onceEach = <A extends unknown[], R>(
  read: (...args: A) => Promise<R>,
): ((...args: A) => Promise<R>) => {
  const made = new Map<string, Promise<R>>();
  return (...args: A): Promise<R> => {
    const key = JSON.stringify(args);
    const known = made.get(key);
    if (known) return known;
    const pending = read(...args);
    made.set(key, pending);
    return pending;
  };
};

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
  const id = idOf(row._id);
  await judge.prepare([row], id === undefined ? [] : [{ id, ver }]);
  const loaded = judge.rows.get(id ?? '') ?? row;
  return judge.judge(loaded, ver, own) === 'pass';
}

// ---- sharing ----------------------------------------------------------------------------------------------

export type WidenAudience = 'everyone' | { userIds: string[] } | 'outside';

export interface WidenResult {
  ok: boolean;
  code?: 'SHARE_NOT_WIDENABLE' | 'SHARE_NOT_ALLOWED_AGENT';
  /** Of the people a share names, those who cannot open what the answer used. */
  cannotOpen?: string[];
}

/**
 * Whether the asker of `row` may widen `ver` of it to `audience`. Every row in what it
 * built on must have a label this build can rely on, and the asker must be able to open everything every one of
 * them used by their own access; an answer an earlier row shared only with them never counts. A share inside the
 * chat may build on an earlier answer shared with everyone in this same chat. Anything an agent's service account
 * or configuration produced is never shared.
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
  const reader: Reader = { userId: sharer, orgId };
  const id = idOf(row._id);
  const judge = new Judge(reader, deps, {
    session: opts.session,
    timeoutMs: DEFAULT_CHECK_TIMEOUT_MS,
  });
  await judge.prepare([row], id === undefined ? [] : [{ id, ver }]);
  const start = judge.rows.get(id ?? '') ?? row;
  const scope = audience === 'outside' ? 'outside' : 'chat';
  // An agent behind the row or its chat, bound to a service account or not, is never shared further.
  const agentKeys = [
    idOf(start.respondingAgentKey),
    idOf(opts.session?.agentKey),
  ].filter((k): k is string => k !== undefined);
  for (const key of agentKeys) {
    const service = await deps
      .isServiceAgent(orgId, key)
      .catch(() => undefined);
    if (service !== false) return AGENT_CONTENT;
  }
  let decided = judge.widenByClosures(start, ver, scope);
  let decider = judge;
  if (decided === 'walk') {
    // Only the walk's order says which refusal comes first: it walks every version behind the answer, up to a budget.
    decider = new Judge(reader, deps, {
      session: opts.session,
      timeoutMs: DEFAULT_CHECK_TIMEOUT_MS,
      treeRowsMax: WIDEN_TREE_ROWS_MAX,
    });
    await decider.prepareTree([row]);
    decided = walkWiden(decider, decider.rows.get(id ?? '') ?? row, ver, scope);
  }
  if (decided) return decided;
  const result: WidenResult = { ok: true };
  if (typeof audience === 'object') {
    const opens = await decider.openFor(
      audience.userIds.map((userId) => ({
        userId,
        orgId,
        member: true,
        memberSince: opts.session
          ? memberSinceOf(opts.session, userId)
          : undefined,
      })),
      start,
      ver,
    );
    const cannot = audience.userIds.filter((_, i) => !opens[i]);
    if (cannot.length > 0) result.cannotOpen = cannot;
  }
  return result;
}

const NOT_WIDENABLE: WidenResult = { ok: false, code: 'SHARE_NOT_WIDENABLE' };
const AGENT_CONTENT: WidenResult = {
  ok: false,
  code: 'SHARE_NOT_ALLOWED_AGENT',
};

/** Rows the share check's walk may read when the closures cannot decide it; past it, the share is refused. */
const WIDEN_TREE_ROWS_MAX = 5_000;

/**
 * The share check's walk of every version behind `start`, read by `judge`'s tree (`prepareTree`): the first refusal
 * met, in the order the labels name the versions, else undefined.
 */
function walkWiden(
  judge: Judge,
  start: ReaderRow,
  ver: string,
  scope: 'chat' | 'outside',
): WidenResult | undefined {
  const sessionId = idOf(start.sessionId);
  // The versions on the path being walked, and what each finished one gave: a version reached again by another path
  // (an answer and a later one both built on it) is judged once; one met again on its own path is a loop.
  const visiting = new Set<string>();
  const finished = new Map<string, WidenResult | undefined>();
  const walk = (
    current: ReaderRow,
    at: string,
    top: boolean,
  ): WidenResult | undefined => {
    const key = `${idOf(current._id) ?? ''}@${at}`;
    if (finished.has(key)) return finished.get(key);
    if (visiting.has(key)) return NOT_WIDENABLE;
    visiting.add(key);
    const result = walkOnce(current, at, top);
    visiting.delete(key);
    finished.set(key, result);
    return result;
  };
  const walkOnce = (
    current: ReaderRow,
    at: string,
    top: boolean,
  ): WidenResult | undefined => {
    const { label, state } = versionAt(
      current,
      at,
      top && at === versionOf(current),
    );
    if (state !== 'ok' || !label) return NOT_WIDENABLE;
    const direct: Plain = isPlain(label.direct) ? label.direct : {};
    if (listOf(direct.saVrids).length > 0) return AGENT_CONTENT;
    if (
      listOf<unknown>(direct.agents).some(
        (a) => isPlain(a) && a.kind === 'service',
      )
    )
      return AGENT_CONTENT;
    // Who may use an agent's configuration is checked per person, which no share to everyone or outside can do.
    if (listOf<Plain>(direct.agents).length > 0) return AGENT_CONTENT;
    if (
      judge.directOf(current, at, top && at === versionOf(current)) !== 'pass'
    )
      return NOT_WIDENABLE;
    for (const parent of listOf<LabelParent>(label.parents)) {
      if (!isPlain(parent) || typeof parent.id !== 'string' || parent.id === '')
        return NOT_WIDENABLE;
      const p = judge.rows.get(parent.id);
      if (!p) return NOT_WIDENABLE;
      const pver = parent.ver;
      if (p.messageType === SHARED_ANSWER) return NOT_WIDENABLE;
      const deeper = walk(p, pver, false);
      if (deeper === undefined) continue;
      if (deeper.code === 'SHARE_NOT_ALLOWED_AGENT') return deeper;
      const consent = consentAt(p, pver);
      const sharedHere =
        scope === 'chat' &&
        consent !== undefined &&
        (consent.scope ?? 'everyone') === 'everyone' &&
        idOf(p.sessionId) === sessionId;
      if (!sharedHere) return deeper;
    }
    return undefined;
  };
  return walk(start, ver, true);
}

/** People a share's check judges at once. */
const PEOPLE_AT_ONCE = 8;

/** `work` for each item, at most `limit` at a time; the results in the items' order. */
async function eachAtMost<T, R>(
  items: readonly T[],
  limit: number,
  work: (item: T) => Promise<R>,
): Promise<R[]> {
  const out = new Array<R>(items.length);
  let next = 0;
  const runner = async (): Promise<void> => {
    while (next < items.length) {
      const i = next;
      next += 1;
      out[i] = await work(items[i] as T);
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, runner),
  );
  return out;
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
    const {
      citations: _c,
      referenceData: _r,
      parts: _p,
      tools: _t,
      reasoning: _s,
      ...copy
    } = row;
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

// A sub-agent's approval card sits in the parts of the sub-agent's own part.
const MAX_CARD_DEPTH = 4;

const isText = (value: unknown): value is string =>
  typeof value === 'string' && value !== '';

/**
 * How a card reads to someone other than its asker: from how the asker answered it (`approvalAnswered`, stored on
 * its row when the answer was taken), or, unanswered, from its expiry as of now, which needs no write.
 */
const cardStatus = (
  answered: unknown,
  expiresAt: number,
  now: number,
): string => {
  if (isPlain(answered) && answered.decision === 'denied') return 'denied';
  if (isPlain(answered) && answered.decision === 'approved') {
    return answered.ran === true ? 'approved' : 'not_run';
  }
  return expiresAt <= now ? 'expired' : 'awaiting_approval';
};

/**
 * What a reader of another person's answer is given of a call that waited on that person's approval card: the tool,
 * its server and when the card expires, which the app's line about the card names, the ids the app keys the card and
 * its call by, and how the card stands (`cardStatus`). Marked `stub`, so the app draws it as that line and never as a
 * step of someone else's run. Never the call's arguments or result, nor what a restricted card says the call would
 * send.
 */
function approvalCardStub(
  part: Plain,
  answered: unknown,
  now: number,
): Plain | undefined {
  const approval = part.approval;
  if (
    part.status !== 'awaiting_approval' ||
    typeof part.approvalAnswer === 'string' ||
    !isPlain(approval)
  ) {
    return undefined;
  }
  const { approvalId, serverName, toolName, toolTitle, expiresAt } = approval;
  if (
    !isText(approvalId) ||
    !isText(serverName) ||
    !isText(toolName) ||
    typeof expiresAt !== 'number'
  ) {
    return undefined;
  }
  return {
    type: 'tool_call',
    ...(isText(part.toolCallId) && { toolCallId: part.toolCallId }),
    stub: true,
    status: cardStatus(answered, expiresAt, now),
    approval: {
      approvalId,
      serverName,
      toolName,
      ...(isText(toolTitle) && { toolTitle }),
      expiresAt,
    },
  };
}

/** `approvalCardStub` of every call in `parts` that waited on its asker's card, a sub-agent's included. */
const approvalCardStubs = (
  parts: unknown,
  answered: unknown,
  now: number,
  depth = 0,
): Plain[] =>
  Array.isArray(parts) && depth < MAX_CARD_DEPTH
    ? (parts as unknown[]).flatMap((part): Plain[] => {
        if (!isPlain(part)) return [];
        const stub =
          part.type === 'tool_call'
            ? approvalCardStub(part, answered, now)
            : undefined;
        return stub
          ? [stub]
          : approvalCardStubs(part.parts, answered, now, depth + 1);
      })
    : [];

/**
 * Another person's answer as a reader who may see it gets it: no steps, reasoning or tool output. A call that waited
 * on its asker's approval card stays as a stub (`approvalCardStub`), read from `stored`, the row as it is stored: the
 * per-record view has already cut another person's parts to their text. An answer that loses any of it here says so
 * (`activityWithheld`), as one the per-record view cut already does.
 */
export function withoutSteps<T extends object>(
  row: T,
  stored: object = row,
  now: number = Date.now(),
): T {
  const r = row as Plain;
  // A question or note is its author's own words, and a card is shaped for each reader where it is drawn: neither
  // carries the steps of a run.
  if (r.messageType !== 'bot_response' && r.messageType !== 'error') return row;
  const view: Plain = { ...r };
  const withheld =
    (Array.isArray(r.reasoning) && r.reasoning.length > 0) ||
    (Array.isArray(r.tools) && r.tools.length > 0) ||
    (Array.isArray(r.parts) &&
      (r.parts as unknown[]).some((p) => isPlain(p) && p.type !== 'text'));
  if (withheld) view.activityWithheld = true;
  if ('reasoning' in view) view.reasoning = [];
  if ('tools' in view) view.tools = [];
  if (Array.isArray(r.parts)) {
    const kept = stored as Plain;
    view.parts = [
      ...approvalCardStubs(kept.parts, kept.approvalAnswered, now),
      ...(r.parts as unknown[]).filter((p) => isPlain(p) && p.type === 'text'),
    ];
  }
  return view as T;
}

export interface ReaderViewOptions extends VisibleRowsOptions {
  /** The chat's owner: rows from before authorship was stored are theirs. */
  ownerId?: string;
  /**
   * The per-record view of these rows (`redactMessagesFor`, or a page's own form of it): run with the same `known`,
   * so it asks nothing the reader check already asked, and given where the reader check sends its own access request.
   */
  perRecordView?: <M extends object>(
    rows: M[],
    known: Readable,
    appConfig: AppConfig | undefined,
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
  // The reader that stands for every member asks the access service nothing, in the label check or the per-record view.
  const known =
    viewer.everyMember === true
      ? askingNothing(opts.known?.deadline)
      : (opts.known ?? newReadable());
  // One clock for the read: how each card stands is read as of now.
  const now = { ...configured, ...opts.deps }.now();
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
    const shown = await perRecord(rows, known, appConfig);
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
      appConfig,
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
    return withoutSteps(withoutQuote(checked), row, now);
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
 * The row `question` replies to, for `replyTargetId`: the answer or shared copy it quotes, unless that was removed. A
 * reply to a person's message names none: their questions and notes go to the model anyway.
 */
export const repliedRowOf = (question: object): string | undefined => {
  const reply = (question as Plain).replyTo;
  if (!isPlain(reply) || reply.removed === true || reply.kind === 'message')
    return undefined;
  return idOf(reply.messageId);
};

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
 * own answers go while they can still see them, whether or not they reply to them. Where others read the chat, an
 * answer from before the rules, whoever asked it, goes as text, and only while every member could read it then or the
 * question replies to it. Another person's answer goes
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
    return (
      stored !== undefined &&
      consentCovers(stored, versionOf(stored), reader, tree.chatsRead)
    );
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
  const othersReadIt = opts.othersRead ?? othersRead(session);
  const everyMemberSaw = othersReadIt
    ? oldRowsEveryMemberSaw(
        reader,
        session,
        asRows,
        (row) => {
          const id = idOf(row._id);
          return id === undefined ? idlessVerdict(row) : verdicts.get(id);
        },
        judged.switchAt,
      )
    : new Set<object>();
  const oldCards = othersReadIt
    ? cardsFromBeforeLabels(asRows)
    : new Set<object>();
  return rows.map((m) => {
    const row = m as unknown as ReaderRow & Plain;
    const id = idOf(row._id);
    const verdict = id === undefined ? idlessVerdict(row) : verdicts.get(id);
    const author = rowAuthorOf(row, ownerId);
    const own = author !== undefined && author === reader.userId;
    const type = row.messageType;
    if (type === 'user_query' || type === 'note') {
      if (verdict?.cardWithheld === true) return sealed(withheldCardAnswer(m));
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
      // A card from before labels, where others read the chat, goes with its turn's answer. Any other card goes: its
      // questions go to the model on its run's answer, whose seal makes that answer a parent of the new one.
      return oldCards.has(row) && !everyMemberSaw.has(row) ? blank(m) : m;
    }
    if (
      !verdict ||
      verdict.verdict === 'hidden' ||
      verdict.verdict === 'unchecked'
    )
      return blank(m);
    if (own) {
      if (verdict.verdict !== 'visible') return blank(m);
      if (!othersReadIt || versionOf(row) !== LEGACY_LABEL_VER)
        return sealed(m);
      // One's own answer from before labels, where others read the chat, goes as text: their check of the new answer
      // passes such a parent only as text, since the view from before labels never showed them its steps. It goes
      // only while every member could read it then, or when the question replies to it; otherwise nothing of it goes,
      // so it is no parent of the new answer.
      return everyMemberSaw.has(row) ||
        (id !== undefined && id === opts.replyTargetId)
        ? sealed(textOnly(m))
        : blank(m);
    }
    // Shared with them, whether or not they could also read it whole: the text the share shows, and no more. An
    // answer built on it names it as a text parent, which every reader the share covers may see. The text is the
    // stored row's, at the version the share covers: a filter before this one may have emptied the row passed (the
    // per-record view empties an answer whose sources the sender cannot open).
    if (verdict.verdict === 'consented' || sharedWithSender(row)) {
      const stored = id === undefined ? undefined : tree.rows.get(id);
      return stored
        ? sealed(
            sharedTextOf({ ...m, labelVer: stored.labelVer }, deps, stored),
          )
        : blank(m);
    }
    const replied = id !== undefined && id === opts.replyTargetId;
    // One they could read before the rules started: its text. Where others read the chat, as one's own: only while
    // every member could read it then, or when the question replies to it.
    if (verdict.basis === 'before_labels') {
      return othersReadIt &&
        versionOf(row) === LEGACY_LABEL_VER &&
        !everyMemberSaw.has(row) &&
        !replied
        ? blank(m)
        : sealed(textOnly(m));
    }
    // Another person's answer that restricts nobody, or the one the sender replied to.
    return replied || pubOf(row) ? sealed(textOnly(m)) : blank(m);
  });
}

/**
 * The cards among `rows` from before labels. A card carries no label of its own, so its version says nothing: it is from
 * before them when the answer of its run is, or, for a card stamped with no run, the answer after it in its turn; with
 * no such answer, a card with no run is, since every row written under the rules names its run.
 */
function cardsFromBeforeLabels(
  rows: ReadonlyArray<ReaderRow & Plain>,
): Set<object> {
  const versionOfRun = new Map<string, string>();
  for (const row of rows) {
    const runId = runOf(row);
    if (runId !== undefined && isLabelledType(row) && !versionOfRun.has(runId))
      versionOfRun.set(runId, versionOf(row));
  }
  const old = new Set<object>();
  rows.forEach((row, i) => {
    if (row.messageType !== 'tool_call') return;
    const runId = runOf(row);
    if (runId !== undefined) {
      if (versionOfRun.get(runId) === LEGACY_LABEL_VER) old.add(row);
      return;
    }
    for (const next of rows.slice(i + 1)) {
      if (next.messageType === 'user_query') break;
      if (isLabelledType(next)) {
        if (versionOf(next) === LEGACY_LABEL_VER) old.add(row);
        return;
      }
    }
    old.add(row);
  });
  return old;
}

/** Whether a row names a record, a file or an address that a per-record view would check: one a reader who can open nothing cannot read. */
const namesASource = (row: Plain): boolean =>
  ['citations', 'attachments', 'referenceData'].some(
    (field) =>
      Array.isArray(row[field]) && (row[field] as unknown[]).length > 0,
  ) ||
  [row.appliedFilters, row.filters].some(
    (filters) =>
      isPlain(filters) &&
      Object.values(filters).some(
        (list) => Array.isArray(list) && list.length > 0,
      ),
  );

/**
 * Of the rows from before the rules that the sender may see (`verdictOf`), those every member of `session` could read
 * then: each answer, whoever asked it, shown before the rules to the reader that stands for every member
 * (`everyMemberOf`: each way into the chat older than the switch, the answer not written under the rules) whose turn
 * (the question before it in `rows`, and it) names no source, as the per-record view from before the rules would show
 * it to one who can open nothing; and the sender's own cards of a turn whose answer is one (nobody else's card reaches
 * their model). It reads no record and asks no one.
 */
function oldRowsEveryMemberSaw(
  sender: Reader,
  session: ReaderSession | undefined,
  rows: ReadonlyArray<ReaderRow & Plain>,
  verdictOf: (row: ReaderRow) => RowVerdict | undefined,
  switchAt: number | undefined,
): Set<object> {
  const ownerId = idOf(session?.userId);
  const everyone = everyMemberOf(sender.orgId);
  const seen = new Set<object>();
  let question: Plain | undefined;
  let cards: Plain[] = [];
  let answered = false;
  const close = (): void => {
    if (answered) {
      for (const card of cards) if (!namesASource(card)) seen.add(card);
    }
    cards = [];
    answered = false;
  };
  for (const row of rows) {
    if (row.messageType === 'user_query') {
      close();
      question = row;
      continue;
    }
    if (
      versionOf(row) !== LEGACY_LABEL_VER ||
      verdictOf(row)?.verdict !== 'visible'
    )
      continue;
    if (row.messageType === 'tool_call') {
      if (rowAuthorOf(row, ownerId) === sender.userId) cards.push(row);
    } else if (
      isLabelledType(row) &&
      question !== undefined &&
      !namesASource(question) &&
      !namesASource(row) &&
      unlabelledShownTo(everyone, session, row, ownerId, switchAt)
    ) {
      seen.add(row);
      answered = true;
    }
  }
  close();
  return seen;
}

/** Whether anyone but its owner reads the chat: a share row, its project, or the readers of an agent room. */
const othersRead = (session: ReaderSession | undefined): boolean =>
  session !== undefined &&
  (shareRowsOf(session).length > 0 ||
    session.projectVisibility === 'project' ||
    idOf(session.agentRoomId) !== undefined);

/** The rows with their label fields and the closures of their versions (and of `versions`), read without asking anyone's access. */
async function labelTree(
  reader: Reader,
  session: ReaderSession | undefined,
  rows: readonly ReaderRow[],
  opts: VisibleRowsOptions,
  versions: readonly VersionRef[] = [],
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
  await judge.prepareLabels(rows, versions);
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
    const id = idOf(row._id);
    const tree = await labelTree(
      { userId: '', orgId },
      undefined,
      [row],
      opts,
      id === undefined ? [] : [{ id, ver }],
    );
    const stored = tree.rows.get(id ?? '');
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
  /** Where the text comes from, when not the row itself. */
  source: ReaderRow = row,
): R => {
  let text: string;
  try {
    text = deps.sharedText(
      typeof source.content === 'string' ? source.content : '',
    );
  } catch {
    // A share's text that cannot be made gives the model none of the answer, as `consentedView` shows a reader none.
    text = '';
  }
  // What a filter before the check hid of the row is not what the share shows: the share's text is the row now.
  const {
    answerHidden: _answerHidden,
    [WITHHELD]: _withheld,
    ...shown
  } = textOnly(row) as Plain;
  const out: Plain = { ...shown, content: text };
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
