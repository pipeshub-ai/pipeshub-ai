/**
 * A chat answer is shown only while its reader can still read every record it
 * used (citations, attachments, reference data, applied filters). If any one is
 * out of reach, the WHOLE answer (text, reasoning, citations, follow-up
 * suggestions, tool steps and results) is replaced by a marker, for the owner
 * and for shared viewers alike: a blended answer cannot be cut sentence by
 * sentence. The user's own question stays. If the access check cannot answer,
 * the answers that name records are hidden with a different reason, so an
 * outage reads as temporary. Answers that name no record are left as stored.
 * Known limit: a record an answer used but did not cite is not detected.
 *
 * A shared conversation or search is also rendered for the person looking at
 * it: every structured reference to a record the viewer cannot read (citation,
 * attachment, reference data, applied filter, search hit) in another user's
 * messages loses its id, name, link and text and shows as a placeholder.
 * A saved search of the viewer's own passes through.
 */
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { Logger } from '../../../libs/services/logger.service';
import type { AppConfig } from '../../tokens_manager/config/config';
import { getConversationRepository } from '../../../libs/repositories/conversation';

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
const ACCESS_CHECK_BATCH = 500;

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
}

export const newReadable = (): Readable => ({
  ids: new Set(),
  urls: new Set(),
  unmatched: new Set(),
  asked: new Set(),
});

const isStringList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((v) => typeof v === 'string');

/**
 * Record ids and record addresses the viewer may read, from the query
 * service's `check_access`, asking only what `known` has not already settled
 * (and adding the answer to it). Fails closed: an error leaves the new ones
 * unknown, which nothing treats as readable.
 */
export const readableRecordIds = async (
  appConfig: AppConfig,
  viewer: Viewer,
  recordIds: string[],
  webUrls: string[] = [],
  known: Readable = newReadable(),
): Promise<Readable> => {
  const ids = [...new Set(recordIds.filter(Boolean))].filter(
    (i) => !known.asked.has(`i:${i}`),
  );
  const urls = [...new Set(webUrls.filter(Boolean))].filter(
    (u) => !known.asked.has(`u:${u}`),
  );
  if (ids.length === 0 && urls.length === 0) return { ...known, failed: false };
  const found = newReadable();
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
    for (
      let i = 0;
      i < Math.max(ids.length, urls.length);
      i += ACCESS_CHECK_BATCH
    ) {
      const response = await new AIServiceCommand<{
        accessible?: string[];
        accessibleUrls?: string[];
        unmatchedUrls?: string[];
      }>({
        uri: `${appConfig.aiBackend}/api/v1/chat/records/access`,
        method: HttpMethod.POST,
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: {
          recordIds: ids.slice(i, i + ACCESS_CHECK_BATCH),
          webUrls: urls.slice(i, i + ACCESS_CHECK_BATCH),
        },
        timeoutMs: 15000,
      }).execute();
      if (response.statusCode !== 200) {
        throw new Error(
          `record access returned ${String(response.statusCode)}`,
        );
      }
      const accessible: unknown = response.data?.accessible ?? [];
      const accessibleUrls: unknown = response.data?.accessibleUrls ?? [];
      const unmatchedUrls: unknown = response.data?.unmatchedUrls ?? [];
      // Only lists of strings: a string is read per character, String(['id']) is 'id'.
      if (
        !isStringList(accessible) ||
        !isStringList(accessibleUrls) ||
        !isStringList(unmatchedUrls)
      ) {
        throw new Error('record access returned a malformed body');
      }
      for (const id of accessible) found.ids.add(id);
      for (const u of accessibleUrls) found.urls.add(u);
      for (const u of unmatchedUrls) found.unmatched.add(u);
    }
    // Settled only when every batch answered.
    for (const id of ids) known.asked.add(`i:${id}`);
    for (const u of urls) known.asked.add(`u:${u}`);
    for (const id of found.ids) known.ids.add(id);
    for (const u of found.urls) known.urls.add(u);
    for (const u of found.unmatched) known.unmatched.add(u);
    return { ...known, failed: false };
  } catch (error: unknown) {
    logger.error('Viewer access check failed; redacting every source', {
      error: error instanceof Error ? error.message : String(error),
    });
    return { ...known, failed: true };
  }
};

/** A cited document: populated, or just an id. Only the record id is read. */
interface CitedDoc {
  _id?: { toString: () => string };
  metadata?: { recordId?: string };
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
export interface ViewerMessage {
  messageType?: string;
  content?: string;
  citations?: Array<CitationEntry | null>;
  attachments?: Array<AttachmentEntry | null>;
  referenceData?: Array<RefEntry | null>;
  appliedFilters?: object;
}
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

/** The cited document of a message citation: populated, or a bare id to look up. */
const citedDoc = (c: CitationEntry | null): CitedDoc | string | undefined => {
  const d = c?.citationData ?? c?.citationId;
  if (!d) return undefined;
  if (typeof d === 'string') return d;
  if (d.metadata) return d;
  const id: { toString: () => string } = d._id ?? d;
  return id.toString();
};

const isAnswer = (m: ViewerMessage): boolean =>
  m.messageType === 'bot_response';

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
  known?: Readable,
): Promise<M[]> => {
  const isOthers = (m: M): boolean => ownerOf(m) !== viewer.userId;
  const checked = messages.filter((m) => isOthers(m) || isAnswer(m));
  if (checked.length === 0) return messages;

  // Citations stored as bare ids (archives) are resolved to their record here.
  const bare = new Set<string>();
  for (const m of checked) {
    for (const c of m.citations ?? []) {
      const d = citedDoc(c);
      if (typeof d === 'string') bare.add(d);
    }
  }
  const recordOfCitation = new Map<string, string>();
  if (bare.size > 0) {
    const docs = await getConversationRepository().getCitations(
      [...bare],
      viewer.orgId,
    );
    for (const d of docs) {
      const recordId = (d.metadata as { recordId?: string } | undefined)
        ?.recordId;
      if (d._id && recordId !== undefined && recordId !== '')
        recordOfCitation.set(d._id, recordId);
    }
  }
  const recordIdOf = (c: CitationEntry | null): string | undefined => {
    const d = citedDoc(c);
    return typeof d === 'string'
      ? recordOfCitation.get(d)
      : d?.metadata?.recordId;
  };

  // One batched check for every record the viewer is shown a reference to.
  const readable = await readableRecordIds(
    appConfig,
    viewer,
    checked.flatMap((m) => [
      ...(m.citations ?? []).map(recordIdOf),
      ...(m.attachments ?? []).map((a) => a?.recordId),
      ...(m.referenceData ?? []).flatMap((e) => {
        const id = e?.recordId ?? (e ? refMeta(e) : undefined);
        return id === undefined || id === '' ? [] : [id];
      }),
      ...filterLists(m.appliedFilters).flatMap(([, l]) => l.flatMap(refIds)),
    ]) as string[],
    checked.flatMap((m) => (m.referenceData ?? []).map((e) => e?.webUrl ?? '')),
    known,
  );
  const ok = (id?: string): boolean => id !== undefined && readable.ids.has(id);
  const okUrl = (u?: string): boolean =>
    u !== undefined && readable.urls.has(u);
  // What a message used: record ids, and addresses where a reference has no record id.
  // An address no record is stored at (a public web page) is no record the viewer could have lost,
  // and neither is a citation without a record id (a web citation, a deleted citation row).
  const usedBy = (m: M): { ids: string[]; urls: string[] } => {
    const ids: string[] = [];
    const urls: string[] = [];
    for (const c of m.citations ?? []) {
      const id = recordIdOf(c);
      if (id !== undefined) ids.push(id);
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
    return { ids, urls };
  };
  // Every message of a hidden answer's turn goes with it: a `tool_call` row (an ask_user_question
  // card) follows its answer, and the chat shows it as that answer's. A `User selections:` question
  // is the user's own and keeps the turn open; any other question or a new answer ends it.
  let hiddenTurn: typeof ANSWER_LOST | typeof ANSWER_UNCONFIRMED | null = null;
  return messages.map((m) => {
    if (isAnswer(m)) {
      const { ids, urls } = usedBy(m);
      const unverified =
        ids.some((id) => !readable.asked.has(`i:${id}`)) ||
        urls.some((u) => !readable.asked.has(`u:${u}`));
      hiddenTurn =
        readable.failed === true && unverified
          ? ANSWER_UNCONFIRMED
          : ids.some((id) => !ok(id)) || urls.some((u) => !okUrl(u))
            ? ANSWER_LOST
            : null;
      if (hiddenTurn !== null) return hiddenAnswer(m, hiddenTurn) as M;
    } else if (m.messageType === 'tool_call') {
      if (hiddenTurn !== null) return hiddenAnswer(m, hiddenTurn) as M;
    } else if (
      m.messageType === 'user_query' &&
      !(m as { content?: string }).content?.startsWith('User selections:')
    ) {
      hiddenTurn = null;
    }
    if (!isOthers(m)) return m;
    const view: Record<string, unknown> = { ...(m as object) };
    if (m.citations)
      view.citations = m.citations.map((c) =>
        ok(recordIdOf(c)) ? c : { ...REDACTED },
      );
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
    return view as M;
  });
};

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
    await redactMessagesFor(
      appConfig,
      viewer,
      messages,
      () => viewer.userId,
      known,
    )
  ).map((m) =>
    (m as { answerHidden?: unknown }).answerHidden !== undefined
      ? { ...m, content: '' }
      : m,
  );

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
      records: Object.fromEntries(
        recordEntries(o).filter(([k]) => readable.ids.has(keyToRecordId(k))),
      ),
    };
    return view as S;
  });
};
