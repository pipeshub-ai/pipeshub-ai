/**
 * A shared conversation or search is rendered for the person looking at it: as
 * the owner sees it, except that every structured reference to a record the
 * viewer cannot read (citation, attachment, reference data, applied filter,
 * tool result, search hit) loses its id, name, link and text and shows as a
 * placeholder. The owner sees everything.
 * Free text stays as written: the answer, reasoning prose and follow-up
 * questions (accepted residual risk, as before). Tool steps and their results
 * are shown as written too; per-record redaction of them is a follow-up.
 */
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { Logger } from '../../../libs/services/logger.service';
import type { AppConfig } from '../../tokens_manager/config/config';
import Citation from '../schema/citation.schema';

const logger = Logger.getInstance({ service: 'Viewer Redaction' });

export const REDACTED = { redacted: true, reason: 'no_access' } as const;
const ACCESS_CHECK_BATCH = 500;

type Viewer = { userId: string; orgId: string };

export const viewerOf = (userId: unknown, orgId: unknown): Viewer => ({
  userId: String(userId),
  orgId: String(orgId),
});

/** Record ids the viewer may read, from the query service's `check_access`. Fails closed: an error means none. */
export const readableRecordIds = async (
  appConfig: AppConfig,
  viewer: Viewer,
  recordIds: string[],
): Promise<Set<string>> => {
  const ids = [...new Set(recordIds.filter(Boolean))];
  const readable = new Set<string>();
  if (ids.length === 0) return readable;
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
    for (let i = 0; i < ids.length; i += ACCESS_CHECK_BATCH) {
      const response = await new AIServiceCommand<{ accessible?: string[] }>({
        uri: `${appConfig.aiBackend}/api/v1/chat/records/access`,
        method: HttpMethod.POST,
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: { recordIds: ids.slice(i, i + ACCESS_CHECK_BATCH) },
        timeoutMs: 15000,
      }).execute();
      if (response.statusCode !== 200) {
        throw new Error(
          `record access returned ${String(response.statusCode)}`,
        );
      }
      for (const id of response.data?.accessible ?? []) readable.add(id);
    }
    return readable;
  } catch (error: unknown) {
    logger.error('Viewer access check failed; redacting every source', {
      error: error instanceof Error ? error.message : String(error),
    });
    return new Set();
  }
};

/** A cited document: populated, or just an id. Only the record id is read. */
interface CitedDoc {
  _id?: { toString: () => string };
  metadata?: { recordId?: string };
}
interface CitationEntry {
  citationId?: CitedDoc | null;
  citationData?: CitedDoc | null;
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

const refId = (e: RefEntry): string | undefined => {
  const meta =
    e.metadata instanceof Map
      ? e.metadata.get('recordId')
      : e.metadata?.recordId;
  return e.recordId ?? meta ?? e.id;
};
/** Shown only if its record is readable; an entry naming something with no readable id is hidden. */
const refVisible = (
  e: RefEntry | null,
  ok: (id?: string) => boolean,
): boolean => {
  if (!e) return true;
  const id = refId(e);
  if (id !== undefined && id !== '') return ok(id);
  return (e.name ?? '') === '' && (e.webUrl ?? '') === '';
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
  if (d.metadata) return d;
  const id: { toString: () => string } = d._id ?? d;
  return id.toString();
};

/**
 * Redacts what `viewer` cannot read in the messages of conversations they do not
 * own. `ownerOf` gives a message's conversation owner; the owner's own pass through.
 */
export const redactMessagesFor = async <M extends ViewerMessage>(
  appConfig: AppConfig,
  viewer: Viewer,
  messages: M[],
  ownerOf: (message: M) => string | undefined,
): Promise<M[]> => {
  const others = messages.filter((m) => ownerOf(m) !== viewer.userId);
  if (others.length === 0) return messages;

  // Citations stored as bare ids (archives) are resolved to their record here.
  const bare = new Set<string>();
  for (const m of others) {
    for (const c of m.citations ?? []) {
      const d = citedDoc(c);
      if (typeof d === 'string') bare.add(d);
    }
  }
  const recordOfCitation = new Map<string, string>();
  if (bare.size > 0) {
    const docs: CitedDoc[] = await Citation.find({ _id: { $in: [...bare] } })
      .select('metadata.recordId')
      .lean();
    for (const d of docs) {
      const recordId = d.metadata?.recordId;
      if (d._id && recordId !== undefined && recordId !== '')
        recordOfCitation.set(d._id.toString(), recordId);
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
    others.flatMap((m) => [
      ...(m.citations ?? []).map(recordIdOf),
      ...(m.attachments ?? []).map((a) => a?.recordId),
      ...(m.referenceData ?? []).flatMap(refIds),
      ...filterLists(m.appliedFilters).flatMap(([, l]) => l.flatMap(refIds)),
    ]) as string[],
  );
  const ok = (id?: string): boolean => id !== undefined && readable.has(id);
  return messages.map((m) => {
    if (ownerOf(m) === viewer.userId) return m;
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
        refVisible(e, ok) ? e : restrictedRef(),
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

const plain = (s: ViewerSearch): ViewerSearch =>
  s.toObject ? s.toObject() : s;
// A saved search keys `records` by the record's `_id` or `_key`.
const keyToRecordId = (key: string): string => key.split('/').pop() ?? key;
// A hit stored as a bare id carries no text; any other object is a hit document.
const isBareId = (h: unknown): boolean =>
  typeof h === 'string' ||
  (typeof h === 'object' &&
    h !== null &&
    typeof (h as { toHexString?: unknown }).toHexString === 'function');
const hitRecord = (h: unknown): string | undefined =>
  (h as CitedDoc | null)?.metadata?.recordId;
const recordEntries = (s: ViewerSearch): Array<[string, string]> =>
  s.records instanceof Map ? [...s.records] : Object.entries(s.records ?? {});

/**
 * Saved searches of other users shared with the viewer: unreadable hits become
 * placeholders (a hit without a record is unreadable) and unreadable entries
 * leave `records`.
 */
export const redactSearchesFor = async <S extends ViewerSearch>(
  appConfig: AppConfig,
  viewer: Viewer,
  searches: S[],
): Promise<S[]> => {
  const isOthers = (s: ViewerSearch): boolean =>
    String(s.userId) !== viewer.userId;
  const others = searches.filter(isOthers).map(plain);
  if (others.length === 0) return searches;
  const readable = await readableRecordIds(
    appConfig,
    viewer,
    others.flatMap((s) => [
      ...(s.citationIds ?? []).filter((h) => !isBareId(h)).map(hitRecord),
      ...recordEntries(s).map(([k]) => keyToRecordId(k)),
    ]) as string[],
  );
  return searches.map((s) => {
    if (!isOthers(s)) return s;
    const o = plain(s);
    const view: Record<string, unknown> = {
      ...o,
      citationIds: (o.citationIds ?? []).map((h) =>
        isBareId(h) || readable.has(hitRecord(h) ?? '') ? h : { ...REDACTED },
      ),
      records: Object.fromEntries(
        recordEntries(o).filter(([k]) => readable.has(keyToRecordId(k))),
      ),
    };
    return view as S;
  });
};
