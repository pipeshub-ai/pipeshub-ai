/**
 * A shared conversation or search is rendered for the person looking at it.
 * - A citation (with its excerpt and quoted context) or search hit whose record
 *   the viewer cannot read becomes a bare `{ redacted: true }` placeholder; an
 *   unreadable attachment is dropped. No id, title, text or link remains.
 * - Everything else a viewer receives is on an allow-list (MESSAGE_FIELDS,
 *   PART_FIELDS): tool results, previews, reasoning, reference data, applied
 *   filters and follow-up questions can name records no citation covers, so
 *   they are not sent. A field added to messages later stays out until allowed.
 * Answer text (`content`, `text` parts) is left as written (accepted residual
 * risk). The owner sees everything.
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
/** One entry of the agent-activity transcript (see IMessagePart). */
interface PartEntry {
  type?: string;
  content?: string;
  parts?: PartEntry[];
}
/** The message fields redaction reads; the rest is dropped unless allow-listed. */
export interface ViewerMessage {
  citations?: Array<CitationEntry | null>;
  attachments?: Array<AttachmentEntry | null>;
  parts?: PartEntry[];
}
export interface ViewerSearch {
  userId?: unknown;
  citationIds?: unknown[];
  records?: Map<string, string> | Record<string, string>;
  toObject?: () => ViewerSearch;
}

// Internal addressing fields (sessionId, orgId, seq) are stripped later by attachMessages.
const MESSAGE_FIELDS = [
  '_id',
  'sessionId',
  'orgId',
  'seq',
  'messageType',
  'content',
  'contentFormat',
  'status',
  'confidence',
  'metadata',
  'createdAt',
  'updatedAt',
  'modelInfo',
];
// A tool call keeps its name and state, never its arguments or results.
const PART_FIELDS = [
  'type',
  'toolCallId',
  'toolName',
  'displayName',
  'status',
  'runId',
  'roleName',
];

const pick = (source: object, fields: string[]): Record<string, unknown> => {
  const record = source as Record<string, unknown>;
  return Object.fromEntries(
    fields.filter((f) => f in record).map((f) => [f, record[f]]),
  );
};

const viewerParts = (parts: PartEntry[]): PartEntry[] =>
  parts.flatMap((part): PartEntry[] => {
    if (!part) return [];
    if (part.type === 'text') {
      return [{ ...pick(part, PART_FIELDS), content: part.content }];
    }
    if (part.type === 'tool_call') return [pick(part, PART_FIELDS)];
    if (part.type === 'sub_agent') {
      return [
        { ...pick(part, PART_FIELDS), parts: viewerParts(part.parts ?? []) },
      ];
    }
    return []; // reasoning, sign-in cards and unknown kinds
  });

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

  const readable = await readableRecordIds(
    appConfig,
    viewer,
    others.flatMap((m) => [
      ...(m.citations ?? []).map(recordIdOf),
      ...(m.attachments ?? []).map((a) => a?.recordId),
    ]) as string[],
  );
  const ok = (id?: string): boolean => id !== undefined && readable.has(id);
  return messages.map((m) => {
    if (ownerOf(m) === viewer.userId) return m;
    const view: Record<string, unknown> = {
      ...pick(m, MESSAGE_FIELDS),
      ...(m.citations && {
        citations: m.citations.map((c) =>
          ok(recordIdOf(c)) ? c : { ...REDACTED },
        ),
      }),
      // Dropped, not replaced: the chat UI renders every attachment as a file chip.
      ...(m.attachments && {
        attachments: m.attachments.filter((a) => ok(a?.recordId)),
      }),
      ...(m.parts && { parts: viewerParts(m.parts) }),
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
