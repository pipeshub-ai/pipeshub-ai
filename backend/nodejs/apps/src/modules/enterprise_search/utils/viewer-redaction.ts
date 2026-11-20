/**
 * A chat answer is shown only while its reader can still read every record it
 * used (citations, attachments, reference data, applied filters). If any one is
 * out of reach, the WHOLE answer (text, reasoning, citations, follow-up
 * suggestions, tool steps and results) is replaced by a marker, for the owner
 * and for shared viewers alike: a blended answer cannot be cut sentence by
 * sentence. The user's own question stays. If the access check cannot answer,
 * the answers that name records are hidden with a different reason, so an
 * outage reads as temporary. Answers that name no record are left as stored,
 * except another user's answers while the check cannot answer: those are hidden
 * as unconfirmed too, since free text can quote a record it never cited.
 * Known limit: a record an answer used but did not cite is not detected.
 *
 * A shared conversation or search is also rendered for the person looking at
 * it: every structured reference to a record the viewer cannot read (citation,
 * attachment, reference data, applied filter, search hit) in another user's
 * messages loses its id, name, link and text and shows as a placeholder.
 * A saved search of the viewer's own passes through.
 *
 * Another user's answers and tool rows never show a viewer their reasoning,
 * tool steps or tool results, even when the answer is shown: those can name
 * records the answer never cited, and carry no record ids to check them by.
 * Only the answer text of the activity transcript reaches them, never what the
 * model wrote between its steps. In a shared conversation a message belongs to
 * who sent it (`messageActor`): a collaborator keeps the steps of their own turns.
 */
import { withheldCitation } from '../../agent-harness/projection/citation-access';
import { sharedNotFound } from '../../agent-harness/projection/audiences';
import { LIVE_RECORD_ID_PREFIX } from '../../agent-harness/projection/citation-source';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { Logger } from '../../../libs/services/logger.service';
import { apiStoredRecords } from './internal-record-fields';
import {
  accessCheckBreaker,
  CheckOutcome,
  timeoutOutcome,
} from './access-check-breaker';
import {
  knowledgeLimitOf,
  type KnowledgeLimitClaim,
} from '../../../libs/context/request-context';
import type { AppConfig } from '../../tokens_manager/config/config';
import { getConversationRepository } from '../../../libs/repositories/conversation';
import {
  idOf as idOfValue,
  readerView,
  rowAuthorOf,
  unlabelledAnswers,
  type ReaderRow,
  type ReaderSession,
} from '../services/conversation/privacy/visibility';

const logger = Logger.getInstance({ service: 'Viewer Redaction' });

export const REDACTED = { redacted: true, reason: 'no_access' } as const;
/** Marker on an answer hidden because a record it used is no longer readable. */
export const ANSWER_LOST = {
  redacted: true,
  reason: 'no_longer_accessible',
} as const;
/** Marker on an answer hidden because the access check could not answer. */
export const ANSWER_UNCONFIRMED = {
  redacted: true,
  reason: 'access_check_unavailable',
} as const;
const ANSWER_LOST_TEXT = 'This answer used content you can no longer access.';
const ANSWER_UNCONFIRMED_TEXT = "We couldn't confirm your access. Try again.";
// One request asks for every record id of a read, up to this many: the query service resolves the
// reader and their grants once and splits the ids itself.
export const ACCESS_CHECK_BATCH = 10_000;
// Addresses are looked up one by one, so a request carries fewer of them.
export const ACCESS_CHECK_URL_BATCH = 500;
// Calls one read keeps in flight: its chunks are checked side by side, but no read takes more of
// the query service's single worker than this.
const ACCESS_CHECK_PARALLEL = 4;
// When the service gives no `validUntil`, an answer is relied on this long at most.
const ACCESS_ANSWER_TTL_MS = 30_000;
/** `metadata.origin` of a citation that is a web page found by web search. */
const WEB_SEARCH_ORIGIN = 'WEB_SEARCH';
/** `metadata.origin` of a file of a public repository a run fetched: stored as a web page is. */
const REPOSITORY_ORIGIN = 'REPOSITORY';

/** The read's one deadline passed before every chunk of its check answered. */
class AccessCheckDeadline extends Error {
  constructor() {
    super('the access check ran past its deadline');
    this.name = 'AccessCheckDeadline';
  }
}

const isTimeout = (error: unknown): boolean => {
  const name = (error as { name?: unknown } | null)?.name;
  return (
    error instanceof AccessCheckDeadline ||
    name === 'TimeoutError' ||
    name === 'AbortError'
  );
};

/** `fn` over `items`, at most `limit` at a time, in order; the first failure fails it, and nothing more starts. */
const inParallel = async <T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> => {
  const results = new Array<R>(items.length);
  let next = 0;
  let failed = false;
  const worker = async (): Promise<void> => {
    while (!failed && next < items.length) {
      const index = next++;
      try {
        results[index] = await fn(items[index] as T);
      } catch (error) {
        failed = true;
        throw error;
      }
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, worker),
  );
  return results;
};

/** `work`, or `AccessCheckDeadline` once `deadline` (epoch ms) has passed, whichever comes first. */
const beforeDeadline = async <T>(
  deadline: number,
  work: Promise<T>,
): Promise<T> => {
  let timer: NodeJS.Timeout | undefined;
  const expired = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(
      () => {
        reject(new AccessCheckDeadline());
      },
      Math.max(0, deadline - Date.now()),
    );
  });
  try {
    return await Promise.race([work, expired]);
  } finally {
    clearTimeout(timer);
  }
};

type Viewer = { userId: string; orgId: string };

export const viewerOf = (userId: unknown, orgId: unknown): Viewer => ({
  userId: String(userId),
  orgId: String(orgId),
});

/**
 * What the query service said about records and addresses. Kept across the
 * checks of one request (pass the same value again) so nothing is asked twice.
 */
export interface Readable {
  ids: Set<string>;
  urls: Set<string>;
  /** Addresses no record is stored at (a public web page): not a record to lose. */
  unmatched: Set<string>;
  /** Everything already put to the query service, as `i:<id>` and `u:<address>`. */
  asked: Set<string>;
  /** The check could not answer: what was not asked before is unknown, not a verdict. */
  failed?: boolean;
  /** Each readable id's current name. */
  names: Map<string, string>;
  /** Asked ids whose record is deleted or gone: not readable, and said apart from the forbidden ones. */
  deleted?: Set<string>;
  /** Epoch milliseconds: the soonest any answer settled here may change. */
  validUntil?: number;
  /** Epoch milliseconds: the read's one deadline. Past it nothing more is asked, and what is left stays unknown. */
  deadline?: number;
  /** Put to the query service in this read without an answer: not asked again, and still unknown. */
  unsettled?: Set<string>;
}

export const newReadable = (): Readable => ({
  ids: new Set(),
  urls: new Set(),
  unmatched: new Set(),
  asked: new Set(),
  names: new Map(),
  deleted: new Set(),
});

const isStringList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((v) => typeof v === 'string');

const isNameMap = (value: unknown): value is Record<string, string> =>
  typeof value === 'object' &&
  value !== null &&
  !Array.isArray(value) &&
  Object.values(value).every((v) => typeof v === 'string');

interface AccessAnswer {
  accessible: string[];
  accessibleUrls: string[];
  unmatchedUrls: string[];
  names: Record<string, string>;
  deleted: string[];
  validUntil?: number;
}

/** One chunk of a read's check, given only what is left of the read's deadline. */
const askAccess = async (
  appConfig: AppConfig,
  token: string,
  chunk: {
    recordIds: string[];
    webUrls: string[];
    knowledgeLimit?: KnowledgeLimitClaim;
  },
  deadline: number,
): Promise<AccessAnswer> => {
  const left = deadline - Date.now();
  if (left <= 0) throw new AccessCheckDeadline();
  const response = await new AIServiceCommand<{
    accessible?: string[];
    accessibleUrls?: string[];
    unmatchedUrls?: string[];
    names?: Record<string, string>;
    deleted?: string[];
    validUntil?: number;
  }>({
    uri: `${appConfig.aiBackend}/api/v1/chat/records/access`,
    method: HttpMethod.POST,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: chunk,
    timeoutMs: left,
  }).execute();
  if (response.statusCode !== 200) {
    throw new Error(`record access returned ${String(response.statusCode)}`);
  }
  const accessible: unknown = response.data?.accessible ?? [];
  const accessibleUrls: unknown = response.data?.accessibleUrls ?? [];
  // Node and the query service ship in one image, so this is not version skew in practice; a
  // service that omits `unmatchedUrls` reads as no unmatched address, so a web link counts as a
  // record and the answer is hidden (fails closed, never shown).
  const unmatchedUrls: unknown = response.data?.unmatchedUrls ?? [];
  const names: unknown = response.data?.names ?? {};
  // An older service says nothing about deleted records: none is said apart, so none is excused.
  const deleted: unknown = response.data?.deleted ?? [];
  const validUntil: unknown = response.data?.validUntil;
  // Only lists of strings: a string is read per character, String(['id']) is 'id'.
  if (
    !isStringList(accessible) ||
    !isStringList(accessibleUrls) ||
    !isStringList(unmatchedUrls) ||
    !isNameMap(names) ||
    !isStringList(deleted) ||
    (validUntil !== undefined &&
      (typeof validUntil !== 'number' || !Number.isFinite(validUntil)))
  ) {
    throw new Error('record access returned a malformed body');
  }
  return {
    accessible,
    accessibleUrls,
    unmatchedUrls,
    names,
    deleted,
    ...(validUntil !== undefined && { validUntil }),
  };
};

/**
 * Record ids and record addresses the viewer may read, each readable id with its
 * current name, from the query service's `check_access`: one request for up to
 * `ACCESS_CHECK_BATCH` ids (and `ACCESS_CHECK_URL_BATCH` addresses), more four at a
 * time, asking only what `known` has not already settled (and adding the answer to
 * it), with the ids that are deleted said apart and how long the answers hold.
 * `timeoutMs` is one deadline for the whole read, never one per query. Fails closed:
 * an error, the deadline, or the breaker (`accessCheckBreaker`) leaves the new ones
 * unknown, which nothing treats as readable.
 */
export const readableRecordIds = async (
  appConfig: AppConfig,
  viewer: Viewer,
  recordIds: string[],
  webUrls: string[] = [],
  known: Readable = newReadable(),
  timeoutMs = 15000,
): Promise<Readable> => {
  const unsettled = known.unsettled ?? new Set<string>();
  known.unsettled = unsettled;
  const wanted = [
    ...[...new Set(recordIds.filter(Boolean))].map((i) => `i:${i}`),
    ...[...new Set(webUrls.filter(Boolean))].map((u) => `u:${u}`),
  ];
  const fresh = wanted.filter((k) => !known.asked.has(k) && !unsettled.has(k));
  const ids = fresh.filter((k) => k.startsWith('i:')).map((k) => k.slice(2));
  const urls = fresh.filter((k) => k.startsWith('u:')).map((k) => k.slice(2));
  if (ids.length === 0 && urls.length === 0) {
    return { ...known, failed: wanted.some((k) => unsettled.has(k)) };
  }
  /** What this read leaves unknown: never asked again in it, so it can't wait out a second deadline. */
  const giveUp = (): Readable => {
    for (const k of fresh) unsettled.add(k);
    return { ...known, failed: true };
  };
  const deadline = Math.min(
    Date.now() + timeoutMs,
    known.deadline ?? Number.POSITIVE_INFINITY,
  );
  if (deadline <= Date.now()) return giveUp();
  const ticket = accessCheckBreaker.allow();
  if (ticket === undefined) {
    logger.debug(
      'Viewer access check not asked while it keeps failing; redacting every source',
    );
    return giveUp();
  }
  let outcome: CheckOutcome = 'failed';
  let leftAtSend = 0;
  try {
    const token = new AuthTokenService(
      appConfig.jwtSecret,
      appConfig.scopedJwtSecret,
    ).generateScopedToken(
      {
        userId: viewer.userId,
        orgId: viewer.orgId,
        scopes: [TokenScopes.CONVERSATION_PERMISSIONS],
      },
      '5m',
    );
    // A read made with a token limited to some sources (the viewer's own request) is held to them: the query
    // service counts what lies outside as unreadable, as it does for every other read by that token.
    const knowledgeLimit = knowledgeLimitOf(viewer.userId);
    const chunks: Array<{
      recordIds: string[];
      webUrls: string[];
      knowledgeLimit?: KnowledgeLimitClaim;
    }> = [];
    const count = Math.max(
      Math.ceil(ids.length / ACCESS_CHECK_BATCH),
      Math.ceil(urls.length / ACCESS_CHECK_URL_BATCH),
    );
    for (let n = 0; n < count; n += 1) {
      chunks.push({
        recordIds: ids.slice(
          n * ACCESS_CHECK_BATCH,
          (n + 1) * ACCESS_CHECK_BATCH,
        ),
        webUrls: urls.slice(
          n * ACCESS_CHECK_URL_BATCH,
          (n + 1) * ACCESS_CHECK_URL_BATCH,
        ),
        ...(knowledgeLimit !== undefined && { knowledgeLimit }),
      });
    }
    leftAtSend = deadline - Date.now();
    const answers = await beforeDeadline(
      deadline,
      inParallel(chunks, ACCESS_CHECK_PARALLEL, (chunk) =>
        askAccess(appConfig, token, chunk, deadline),
      ),
    );
    // Settled only when every chunk answered.
    const deletedIds = known.deleted ?? new Set<string>();
    known.deleted = deletedIds;
    let validUntil = known.validUntil ?? Date.now() + ACCESS_ANSWER_TTL_MS;
    for (const {
      accessible,
      accessibleUrls,
      unmatchedUrls,
      names,
      deleted,
      validUntil: until,
    } of answers) {
      for (const id of deleted) deletedIds.add(id);
      if (until !== undefined) validUntil = Math.min(validUntil, until);
      for (const id of accessible) {
        known.ids.add(id);
        const name = Object.prototype.hasOwnProperty.call(names, id)
          ? names[id]
          : undefined;
        if (name !== undefined) known.names.set(id, name);
      }
      for (const u of accessibleUrls) known.urls.add(u);
      for (const u of unmatchedUrls) known.unmatched.add(u);
    }
    for (const id of ids) known.asked.add(`i:${id}`);
    for (const u of urls) known.asked.add(`u:${u}`);
    known.validUntil = validUntil;
    outcome = 'ok';
    return { ...known, failed: false };
  } catch (error: unknown) {
    // A timeout is seen in the turn of the event loop its timer fired in: this is how late it fired.
    outcome = isTimeout(error)
      ? timeoutOutcome(leftAtSend, Date.now() - deadline)
      : 'failed';
    logger.error('Viewer access check failed; redacting every source', {
      error: error instanceof Error ? error.message : String(error),
      outcome,
    });
    return giveUp();
  } finally {
    const change = accessCheckBreaker.record(ticket, outcome);
    if (change === 'opened') {
      logger.warn(
        'Viewer access check keeps failing; reads hide sourced answers without asking for 15 s',
      );
    } else if (change === 'closed') {
      logger.info('Viewer access check answers again');
    }
  }
};

/** What a check reads of a cited document's metadata. */
interface CitedMeta {
  recordId?: string;
  webUrl?: string;
  origin?: string;
  sourceKind?: string;
}
/** A public page (web search result or fetched repository file): its `recordId` is its address. */
const isPage = (meta: CitedMeta | undefined): boolean =>
  meta?.origin === WEB_SEARCH_ORIGIN ||
  meta?.origin === REPOSITORY_ORIGIN ||
  meta?.sourceKind === 'web';
/** A cited document: populated, or just an id. Only its metadata is read. */
interface CitedDoc {
  _id?: { toString: () => string };
  metadata?: CitedMeta;
}
interface CitationEntry {
  citationId?: CitedDoc | string | null;
  citationData?: CitedDoc | string | null;
}
interface AttachmentEntry {
  recordId?: string;
}
/** An item of `referenceData` or `appliedFilters.*`: a named reference to a record or node. */
interface RefEntry {
  id?: string;
  recordId?: string;
  name?: string;
  webUrl?: string;
  metadata?: { recordId?: string } | Map<string, string>;
}
/** An entry of an answer's activity transcript: text, reasoning, a tool call, a delegate's own timeline. */
interface PartEntry {
  type?: string;
  isFinal?: boolean;
  notFound?: unknown;
  runId?: string;
  parentRunId?: string;
  parts?: unknown[];
}
/**
 * A background job's card: a durable child run, named by its parent. A viewer
 * sees its card and status, as the live stream names it, never its steps.
 */
const isJobCard = (part: PartEntry | null): boolean =>
  part?.type === 'sub_agent' && !!part.runId && part.parentRunId !== undefined;
export interface ViewerMessage {
  _id?: unknown;
  messageType?: string;
  content?: string;
  senderId?: { toString(): string } | null;
  runId?: string;
  reasoning?: unknown[];
  tools?: unknown[];
  parts?: Array<PartEntry | null>;
  notFound?: unknown;
  citations?: Array<CitationEntry | null>;
  attachments?: Array<AttachmentEntry | null>;
  referenceData?: Array<RefEntry | null>;
  appliedFilters?: object;
}
/** Whose turn a message is: its sender's, or the conversation's owner's when it names none. */
export const messageActor = (message: unknown, owner: string): string => {
  // An ObjectId as Mongo stores it, or its hex string.
  const sender = (message as ViewerMessage | null)?.senderId;
  return sender === undefined || sender === null ? owner : sender.toString();
};
export interface ViewerSearch {
  userId?: unknown;
  citationIds?: unknown[];
  records?: Map<string, string> | Record<string, string>;
  toObject?: () => ViewerSearch;
}

const RESTRICTED = 'Restricted source';
const restrictedRef = (): Record<string, unknown> => ({
  ...REDACTED,
  name: RESTRICTED,
});
const FILTER_LISTS = ['apps', 'kb', 'recordGroups', 'records', 'recordsExact'];

const refMeta = (e: RefEntry): string | undefined =>
  e.metadata instanceof Map ? e.metadata.get('recordId') : e.metadata?.recordId;
const refId = (e: RefEntry): string | undefined =>
  e.recordId ?? refMeta(e) ?? e.id;
/**
 * Shown only if its record is readable. Applied filters name a node by its own
 * id. Reference data names things by a vendor id, which is never trusted: its
 * PipesHub `recordId` decides if it has one, else the record(s) at its `webUrl`,
 * else it is hidden.
 */
const refVisible = (
  e: RefEntry | null,
  ok: (id?: string) => boolean,
  okUrl?: (url?: string) => boolean,
): boolean => {
  if (!e) return true;
  if (okUrl === undefined) {
    const id = refId(e);
    if (id !== undefined && id !== '') return ok(id);
    return (e.name ?? '') === '' && (e.webUrl ?? '') === '';
  }
  const recordId = e.recordId ?? refMeta(e) ?? '';
  if (recordId !== '') return ok(recordId);
  return (e.webUrl ?? '') !== '' && okUrl(e.webUrl);
};
const refIds = (e: RefEntry | null): string[] => {
  const id = e === null ? undefined : refId(e);
  return id === undefined || id === '' ? [] : [id];
};
const filterLists = (f: object | undefined): Array<[string, RefEntry[]]> =>
  FILTER_LISTS.flatMap((k) =>
    Array.isArray((f as Record<string, unknown> | undefined)?.[k])
      ? [[k, (f as Record<string, RefEntry[]>)[k]] as [string, RefEntry[]]]
      : [],
  );

/**
 * A citation the harness's per-viewer check (P1b C-6) already replaced with its placeholder: it names no document,
 * so there is nothing to look up and nothing left to withhold.
 */
const alreadyWithheld = (c: CitationEntry | null): boolean =>
  typeof c?.citationData === 'object' &&
  c.citationData !== null &&
  (c.citationData as { unavailable?: unknown }).unavailable === true;

/** What a withheld citation keeps: its chunk index, read from the populated document when there is one. */
const citedDocOrData = (c: CitationEntry | null): unknown => {
  const d = c?.citationData ?? c?.citationId;
  return typeof d === 'object' && d !== null ? d : undefined;
};

/** The cited document of a message citation: populated, or a bare id to look up. */
const citedDoc = (c: CitationEntry | null): CitedDoc | string | undefined => {
  if (alreadyWithheld(c)) return undefined;
  const d = c?.citationData ?? c?.citationId;
  if (!d) return undefined;
  if (typeof d === 'string') return d;
  if (d.metadata) return d;
  const id: { toString: () => string } = d._id ?? d;
  return id.toString();
};

export interface RedactOptions<M> {
  /** What an earlier check of this request settled: those records are not asked again. */
  known?: Readable;
  timeoutMs?: number;
  /** A message the run just produced: retrieval checked its access, so a check that cannot answer never hides it. */
  fresh?: (message: M) => boolean;
  /** The rows before the first question are the tail of a turn that began off the page and could not be read: its rows are hidden as unconfirmed. */
  leadUnknown?: boolean;
  /** The last turn goes on past the page and could not be read: its rows are hidden as unconfirmed. */
  trailUnknown?: boolean;
  /**
   * The messages whose citations the harness's per-viewer check (P1b C-6) already decided with the
   * viewer's credential: on one not the viewer's own, a citation it showed is not asked about again,
   * one it withheld leaves the answer unconfirmed, and a web page's citation is judged by its
   * address, as on any other message.
   */
  citationsDecided?: (message: M) => boolean;
  /** The harness's check could not be made: another person's answers are unconfirmed, as when this one's cannot. */
  checkFailed?: boolean;
}

// A classified failure is stored as the turn's answer with type `error`, and still carries what it said.
type HiddenMarker = typeof ANSWER_LOST | typeof ANSWER_UNCONFIRMED;

const isAnswer = (m: ViewerMessage): boolean =>
  m.messageType === 'bot_response' || m.messageType === 'error';
/** A question that opens a turn; `User selections:` and `Tool approval:` are replies to a card of the same turn. */
export const startsTurn = (m: ViewerMessage): boolean => {
  if (m.messageType !== 'user_query') return false;
  const text = (m.content ?? '').trimStart();
  return (
    !text.startsWith('User selections:') && !text.startsWith('Tool approval:')
  );
};

/** What the hidden answer keeps: who, when and which message, never what was said. */
const hiddenAnswer = (
  m: object,
  marker: typeof ANSWER_LOST | typeof ANSWER_UNCONFIRMED,
): Record<string, unknown> => ({
  ...m,
  content: marker === ANSWER_LOST ? ANSWER_LOST_TEXT : ANSWER_UNCONFIRMED_TEXT,
  citations: [],
  attachments: [],
  referenceData: [],
  followUpQuestions: [],
  reasoning: [],
  tools: [],
  parts: [],
  appliedFilters: undefined,
  metadata: undefined,
  confidence: undefined,
  notFound: undefined,
  limits: undefined,
  runOutcome: undefined,
  answerHidden: { ...marker },
});

/**
 * Hides, for `viewer`, every answer that used a record they cannot read, and
 * redacts what they cannot read in the messages of conversations they do not
 * own. `ownerOf` gives a message's conversation owner. One batched access check
 * covers the whole list: ceil(records / 500) calls to the query service.
 */
export const redactMessagesFor = async <M extends ViewerMessage>(
  appConfig: AppConfig,
  viewer: Viewer,
  messages: M[],
  ownerOf: (message: M) => string | undefined,
  opts: RedactOptions<M> = {},
): Promise<M[]> => {
  const { known, timeoutMs, fresh } = opts;
  const isOthers = (m: M): boolean => ownerOf(m) !== viewer.userId;
  // The harness decided this message's citations for this viewer: the ones it showed are readable.
  const decided = (m: M): boolean =>
    isOthers(m) && opts.citationsDecided?.(m) === true;
  // The user's own files and filters (the question's, and a card reply's) are used by the turn's
  // answer too; a stopped answer stores none.
  const checked = messages.filter(
    (m) => isOthers(m) || isAnswer(m) || m.messageType === 'user_query',
  );
  if (checked.length === 0 && !opts.leadUnknown && !opts.trailUnknown)
    return messages;

  // Citations stored as bare ids (archives) are resolved to their record here.
  const bare = new Set<string>();
  for (const m of checked) {
    for (const c of m.citations ?? []) {
      const d = citedDoc(c);
      if (typeof d === 'string') bare.add(d);
    }
  }
  const docOfCitation = new Map<string, CitedMeta>();
  if (bare.size > 0) {
    const docs = await getConversationRepository().getCitations(
      [...bare],
      viewer.orgId,
    );
    for (const d of docs) {
      const meta = d.metadata as CitedMeta | undefined;
      if (d._id) docOfCitation.set(d._id, meta ?? {});
    }
  }
  const metaOf = (c: CitationEntry | null): CitedMeta | undefined => {
    const d = citedDoc(c);
    return typeof d === 'string' ? docOfCitation.get(d) : d?.metadata;
  };
  // A file a harness tool read straight from its connector (P1b C-18) names a temporary `live:` id, never a
  // record key: the query service's record check cannot decide it, only the harness's per-viewer check can.
  const isLive = (c: CitationEntry | null): boolean =>
    metaOf(c)?.recordId?.startsWith(LIVE_RECORD_ID_PREFIX) === true;
  // A web search result stores its page address as `recordId`: that names no record.
  const recordIdOf = (c: CitationEntry | null): string | undefined => {
    const meta = metaOf(c);
    if (isPage(meta)) return undefined;
    const id = meta?.recordId;
    return id === undefined || id === '' || isLive(c) ? undefined : id;
  };
  // A web citation names an address instead of a record. A web search result is checked by its
  // page address (its `webUrl` adds a text fragment no stored record matches); `webUrl` is shown.
  const webUrlOf = (c: CitationEntry | null): string | undefined => {
    const meta = metaOf(c);
    const url =
      isPage(meta) && (meta?.recordId ?? '') !== ''
        ? meta?.recordId
        : meta?.webUrl;
    return recordIdOf(c) === undefined &&
      !isLive(c) &&
      url !== undefined &&
      url !== ''
      ? url
      : undefined;
  };

  // One batched check for every record the viewer is shown a reference to.
  const readable = await readableRecordIds(
    appConfig,
    viewer,
    checked.flatMap((m) => [
      ...(decided(m) ? [] : (m.citations ?? []).map(recordIdOf)),
      ...(m.attachments ?? []).map((a) => a?.recordId),
      ...(m.referenceData ?? []).flatMap((e) => {
        const id = e?.recordId ?? (e ? refMeta(e) : undefined);
        return id === undefined || id === '' ? [] : [id];
      }),
      ...filterLists(m.appliedFilters).flatMap(([, l]) => l.flatMap(refIds)),
    ]) as string[],
    checked.flatMap((m) => [
      ...(m.referenceData ?? []).map((e) => e?.webUrl ?? ''),
      ...(m.citations ?? []).map((c) => webUrlOf(c) ?? ''),
    ]),
    known,
    timeoutMs,
  );
  const ok = (id?: string): boolean => id !== undefined && readable.ids.has(id);
  // An address no record is stored at is a plain web reference, shown as it is.
  const okUrl = (u?: string): boolean =>
    u !== undefined && (readable.urls.has(u) || readable.unmatched.has(u));
  // What a message used: record ids, and addresses where a reference has no record id. An address
  // no record is stored at (a public web page) is no record the viewer could have lost. A stored
  // citation that cannot be read at all (its row is gone) is unknown, never "record-free".
  const usedBy = (
    m: M,
  ): { ids: string[]; urls: string[]; unknown: boolean } => {
    const ids: string[] = [];
    const urls: string[] = [];
    let unknown = false;
    for (const c of m.citations ?? []) {
      if (c === null) continue;
      // The harness's check shows every page; its address is still judged here, as on any other message.
      if (decided(m) && !alreadyWithheld(c) && !isPage(metaOf(c))) continue;
      // The owner's own live reads are theirs, as C-6 leaves them; another reader's are unconfirmed without the harness's check.
      if (isLive(c)) {
        if (isOthers(m)) unknown = true;
        continue;
      }
      const id = recordIdOf(c);
      const url = webUrlOf(c);
      if (id !== undefined) ids.push(id);
      else if (url !== undefined) {
        if (!readable.unmatched.has(url)) urls.push(url);
      } else if (metaOf(c) === undefined) unknown = true;
    }
    for (const a of m.attachments ?? [])
      if ((a?.recordId ?? '') !== '') ids.push(a?.recordId as string);
    for (const e of m.referenceData ?? []) {
      if (!e) continue;
      const id = e.recordId ?? refMeta(e) ?? '';
      if (id !== '') ids.push(id);
      else if (
        (e.webUrl ?? '') !== '' &&
        !readable.unmatched.has(e.webUrl ?? '')
      )
        urls.push(e.webUrl as string);
    }
    for (const [, l] of filterLists(m.appliedFilters))
      ids.push(...l.flatMap(refIds));
    return { ids, urls, unknown };
  };
  // Turns: a question that opens one, up to the next. A turn's rows go with its hidden answer
  // wherever they sit (the live path saves a card before its answer, a regeneration after it).
  const turnOf: number[] = [];
  const turnDeps: Array<{ ids: string[]; urls: string[] }> = [
    { ids: [], urls: [] },
  ];
  let turn = 0;
  for (const m of messages) {
    if (startsTurn(m)) {
      turn += 1;
      turnDeps[turn] = { ids: [], urls: [] };
    }
    if (m.messageType === 'user_query') {
      const { ids, urls } = usedBy(m);
      const deps = turnDeps[turn] ?? { ids: [], urls: [] };
      deps.ids.push(...ids);
      deps.urls.push(...urls);
      turnDeps[turn] = deps;
    }
    turnOf.push(turn);
  }
  const verdictOf = (m: M, t: number): HiddenMarker | null => {
    const own = usedBy(m);
    const ids = [...own.ids, ...(turnDeps[t]?.ids ?? [])];
    const urls = [...own.urls, ...(turnDeps[t]?.urls ?? [])];
    // A settled denial hides the answer whatever else could not be checked.
    if (
      ids.some((id) => readable.asked.has(`i:${id}`) && !ok(id)) ||
      urls.some((u) => readable.asked.has(`u:${u}`) && !okUrl(u))
    )
      return ANSWER_LOST;
    const unverified =
      own.unknown ||
      ids.some((id) => !readable.asked.has(`i:${id}`)) ||
      urls.some((u) => !readable.asked.has(`u:${u}`));
    return unverified && fresh?.(m) !== true ? ANSWER_UNCONFIRMED : null;
  };
  // A turn that runs past what could be read may hold records nobody saw: its answers are
  // unconfirmed too (a settled denial still says lost).
  const unreadTurn = (t: number): boolean =>
    (opts.leadUnknown === true && t === 0) ||
    (opts.trailUnknown === true && t === turn);
  const answerVerdict: Array<HiddenMarker | null> = messages.map((m, i) => {
    if (!isAnswer(m)) return null;
    const t = turnOf[i] ?? 0;
    const v = verdictOf(m, t);
    if (v !== null) return v;
    const unconfirmed =
      unreadTurn(t) ||
      ((readable.failed === true || opts.checkFailed === true) &&
        isOthers(m) &&
        fresh?.(m) !== true);
    return unconfirmed ? ANSWER_UNCONFIRMED : null;
  });
  const turnVerdict = new Map<number, HiddenMarker>();
  answerVerdict.forEach((v, i) => {
    const t = turnOf[i] ?? 0;
    if (v !== null && turnVerdict.get(t) !== ANSWER_LOST) turnVerdict.set(t, v);
  });
  if (opts.leadUnknown === true && !turnVerdict.has(0))
    turnVerdict.set(0, ANSWER_UNCONFIRMED);
  if (opts.trailUnknown === true && !turnVerdict.has(turn))
    turnVerdict.set(turn, ANSWER_UNCONFIRMED);
  return messages.map((m, i) => {
    if (isAnswer(m)) {
      const v = answerVerdict[i] ?? null;
      if (v !== null) return hiddenAnswer(m, v) as M;
    } else if (m.messageType === 'tool_call') {
      const v = turnVerdict.get(turnOf[i] ?? 0);
      if (v !== undefined) return hiddenAnswer(m, v) as M;
    }
    if (!isOthers(m)) return m;
    const view: Record<string, unknown> = { ...(m as object) };
    if (m.citations)
      view.citations = m.citations.map((c, index) => {
        if (alreadyWithheld(c)) return c;
        const id = recordIdOf(c);
        const visible =
          (decided(m) && !isPage(metaOf(c))) ||
          (id !== undefined ? ok(id) : okUrl(webUrlOf(c)));
        return visible ? c : withheldCitation(index, citedDocOrData(c));
      });
    // Dropped, not replaced: the chat UI renders every attachment as a file chip.
    if (m.attachments)
      view.attachments = m.attachments.filter((a) => ok(a?.recordId));
    if (m.referenceData)
      view.referenceData = m.referenceData.map((e) =>
        refVisible(e, ok, okUrl) ? e : restrictedRef(),
      );
    if (m.appliedFilters)
      view.appliedFilters = {
        ...m.appliedFilters,
        ...Object.fromEntries(
          filterLists(m.appliedFilters).map(([k, l]) => [
            k,
            l.map((e) => (refVisible(e, ok) ? e : restrictedRef())),
          ]),
        ),
      };
    if (m.messageType !== 'user_query') {
      if ('reasoning' in view) view.reasoning = [];
      if ('tools' in view) view.tools = [];
      if (m.messageType === 'tool_call') view.content = '';
      if (m.parts)
        view.parts = m.parts
          .filter(
            (part) =>
              (part?.type === 'text' && part.isFinal === true) ||
              isJobCard(part),
          )
          .map((part) =>
            isJobCard(part)
              ? { ...part, parts: [] }
              : part && 'notFound' in part
                ? { ...part, notFound: sharedNotFound(part.notFound) }
                : part,
          );
      if (m.notFound !== undefined) view.notFound = sharedNotFound(m.notFound);
    }
    return view as M;
  });
};

/** The turn-start check is on the way to the first token: it gives up sooner and fails closed. */
const HISTORY_CHECK_TIMEOUT_MS = 5000;

/**
 * The stored history of a chat as the model may be told it: an answer that used
 * a record `viewer` can no longer read (or cannot be confirmed readable) goes
 * with no text, so a follow-up cannot make the model repeat it.
 */
export const historyForModel = async <M extends ViewerMessage>(
  appConfig: AppConfig,
  viewer: Viewer,
  messages: M[],
  known?: Readable,
): Promise<M[]> =>
  (
    await redactMessagesFor(appConfig, viewer, messages, () => viewer.userId, {
      known,
      timeoutMs: HISTORY_CHECK_TIMEOUT_MS,
    })
  ).map((m) =>
    (m as { answerHidden?: unknown }).answerHidden !== undefined
      ? { ...m, content: '' }
      : m,
  );

/**
 * A conversation about to leave in a response or an event (the final conversation
 * of a turn, the one an error event carries), as the viewer may see it: each answer
 * judged by its label for them (`readerView`), another person's steps left out, and
 * the per-record view over the rest, with one access request for both. `freshIds`
 * are the answers the run just produced: a check that fails never hides those from
 * their own asker.
 */
export const redactConversationFor = async <C extends { messages?: unknown }>(
  appConfig: AppConfig,
  viewer: Viewer,
  conversation: C,
  opts: { known?: Readable; freshIds?: unknown[] } = {},
): Promise<C> => {
  if (!Array.isArray(conversation.messages)) return conversation;
  const fresh = new Set((opts.freshIds ?? []).map(String));
  const chat = conversation as unknown as ReaderSession & {
    initiator?: unknown;
  };
  // A message with no sender is the initiator's (as a fetch reads it), never the viewer's by default.
  const ownerId =
    idOfValue(chat.initiator) ?? idOfValue(chat.userId) ?? viewer.userId;
  const { rows } = await readerView(
    { ...viewer, member: true },
    chat,
    conversation.messages as object[],
    {
      member: true,
      appConfig,
      ownerId,
      ...(opts.known && { known: opts.known }),
      ...(opts.freshIds && { freshIds: opts.freshIds }),
      // The answers no label decided alone pass the per-record view; questions, notes and cards are shaped for the
      // reader where the frame is built.
      perRecordFor: unlabelledAnswers,
      perRecordView: async <R extends object>(list: R[], known: Readable) =>
        (await redactMessagesFor(
          appConfig,
          viewer,
          list as unknown as ViewerMessage[],
          // Whose turn a row is: who wrote or asked for it, else its sender, else the owner's.
          (m) => rowAuthorOf(m as ReaderRow, messageActor(m, ownerId)),
          {
            known,
            fresh: (m) =>
              m._id !== undefined &&
              fresh.has((m._id as { toString(): string }).toString()),
          },
        )) as unknown as R[],
    },
  );
  return { ...conversation, messages: rows };
};

const plain = (s: ViewerSearch): ViewerSearch =>
  s.toObject ? s.toObject() : s;
// A saved search keys `records` by the record's `_id` or `_key`.
const keyToRecordId = (key: string): string => key.split('/').pop() ?? key;
// A hit stored as a bare id carries no text and cannot be checked.
const isBareId = (h: unknown): boolean =>
  typeof h === 'string' ||
  (typeof h === 'object' &&
    h !== null &&
    typeof (h as { toHexString?: unknown }).toHexString === 'function');
const isHit = (h: unknown): boolean => !isBareId(h);
const hitRecord = (h: unknown): string | undefined =>
  (h as CitedDoc | null)?.metadata?.recordId;
const recordEntries = (s: ViewerSearch): Array<[string, string]> =>
  s.records instanceof Map ? [...s.records] : Object.entries(s.records ?? {});

/**
 * Saved searches, for the person looking at them. A hit on a record the viewer
 * cannot read becomes a placeholder and its `records` entry is dropped. On a
 * search of another user's (a populated hit without a record is unreadable; a
 * bare id is dropped) the placeholder is `no_access`. On the viewer's own, a
 * hit with no record to check and a bare id (no text) stay, and the placeholder
 * says why: `no_longer_accessible`, or `access_check_unavailable` if the check
 * could not answer.
 */
export const redactSearchesFor = async <S extends ViewerSearch>(
  appConfig: AppConfig,
  viewer: Viewer,
  searches: S[],
): Promise<S[]> => {
  const isOthers = (s: ViewerSearch): boolean =>
    String(s.userId) !== viewer.userId;
  const all = searches.map(plain);
  const readable = await readableRecordIds(
    appConfig,
    viewer,
    all.flatMap((s) => [
      ...(s.citationIds ?? []).filter(isHit).map(hitRecord),
      ...recordEntries(s).map(([k]) => keyToRecordId(k)),
    ]) as string[],
  );
  const verdict = readable.failed === true ? ANSWER_UNCONFIRMED : ANSWER_LOST;
  return searches.map((s) => {
    const o = plain(s);
    const others = isOthers(s);
    const view: Record<string, unknown> = {
      ...o,
      // The spec types the history's `citationIds` as id strings, so another user's bare id (it
      // cannot be checked) is left out; a populated hit the viewer cannot read becomes a placeholder.
      citationIds: (o.citationIds ?? []).flatMap((h) => {
        if (!isHit(h)) return others ? [] : [h];
        const id = hitRecord(h);
        if (!others && (id === undefined || id === '')) return [h];
        return [
          readable.ids.has(id ?? '') ? h : { ...(others ? REDACTED : verdict) },
        ];
      }),
      records: apiStoredRecords(
        Object.fromEntries(
          recordEntries(o).filter(([k]) => readable.ids.has(keyToRecordId(k))),
        ),
      ),
    };
    return view as S;
  });
};
