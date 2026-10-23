/**
 * Shared chats and searches are rendered for the person looking at them, not
 * for the author. Citations, attachments and answer text built from records the
 * viewer cannot read are replaced here, before anything reaches the response:
 * a redacted field carries no original text, only `redacted: true`, a reason and
 * what the viewer needs to ask for access.
 *
 * Answer text is cut into sentences. A sentence cites sources with `[N]`
 * markers, where N is the `chunkIndex` of one of the message's citations
 * (citations.py renumbers them 1..n in order of appearance).
 *   - every citation of the sentence unreadable: the sentence becomes REDACTED_TOKEN;
 *   - mixed: the sentence stays, without the unreadable markers;
 *   - uncited, in an answer that used unreadable sources: the message is flagged
 *     `redaction.partial` so the UI shows "partially based on sources you can't access";
 *   - every citation of the message unreadable: the whole body is dropped.
 * Paraphrase in uncited or mixed sentences can still reflect unreadable content
 * (accepted residual risk, D-22 6b).
 */
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { Logger } from '../../../libs/services/logger.service';
import type { AppConfig } from '../../tokens_manager/config/config';
import Citation from '../schema/citation.schema';

const logger = Logger.getInstance({ service: 'Viewer Redaction' });

export const REDACTED_TOKEN = '{{redacted}}';
export const REDACTION_REASON = 'no_access';
export const ACCESS_REQUESTS_ENDPOINT = '/api/v1/access-requests';
const ACCESS_CHECK_BATCH = 500;

export interface RequestAccessInfo {
  recordId: string;
  /** native: the owner approves in PipesHub. connector: ask at the source. */
  kind: 'native' | 'connector';
  /** Connector only: the record's page in its source system. */
  sourceUrl?: string;
  /** Collection record: asking is asking for the whole collection (its name is not disclosed). */
  collection?: boolean;
  endpoint: string;
}

export interface RedactedItem {
  redacted: true;
  reason: typeof REDACTION_REASON;
  requestAccess: RequestAccessInfo;
}

export interface MessageRedaction {
  redacted: boolean;
  /** The answer used sources the viewer cannot read but still has some text. */
  partial: boolean;
  reason: typeof REDACTION_REASON;
}

type Json = Record<string, any>;

/**
 * POST to Python's per-user record routes, signed as `user` with the
 * conversation-permissions scope. Throws on transport errors and non-2xx.
 */
export const callRecordAccess = async <T>(
  appConfig: AppConfig,
  user: { userId: string; orgId: string },
  path: 'access' | 'access-grant',
  body: Json,
): Promise<T> => {
  const token = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  ).generateScopedToken(
    {
      userId: user.userId,
      orgId: user.orgId,
      scopes: [TokenScopes.CONVERSATION_PERMISSIONS],
    },
    '5m',
  );
  const response = await new AIServiceCommand<T>({
    uri: `${appConfig.aiBackend}/api/v1/chat/records/${path}`,
    method: HttpMethod.POST,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body,
    timeoutMs: 15000,
  }).execute();
  if (response.statusCode !== 200) {
    throw new Error(`record ${path} returned ${response.statusCode}`);
  }
  return response.data as T;
};

/** Record ids the viewer may read, from Python's `check_access`. Batched; fails closed. */
export const readableRecordIds = async (
  appConfig: AppConfig,
  viewer: { userId: string; orgId: string },
  recordIds: string[],
): Promise<Set<string>> => {
  const ids = [...new Set(recordIds.filter(Boolean))];
  const readable = new Set<string>();
  try {
    for (let i = 0; i < ids.length; i += ACCESS_CHECK_BATCH) {
      const data = await callRecordAccess<{ accessible?: string[] }>(
        appConfig,
        viewer,
        'access',
        { recordIds: ids.slice(i, i + ACCESS_CHECK_BATCH) },
      );
      for (const id of data?.accessible ?? []) readable.add(id);
    }
    return readable;
  } catch (error: any) {
    // Unknown is unreadable: a failed check must never show the author's text.
    logger.error('Viewer access check failed; redacting every source', {
      error: error.message,
    });
    return new Set();
  }
};

/** The populated citation document, whichever way the message was shaped. */
const citationDoc = (citation: Json): Json | undefined => {
  const doc = citation?.citationData ?? citation?.citationId;
  return doc && typeof doc === 'object' && 'metadata' in doc ? doc : undefined;
};

const citationRecordId = (citation: Json): string | undefined =>
  citationDoc(citation)?.metadata?.recordId;

const requestAccessFor = (
  recordId: string | undefined,
  metadata: Json | undefined,
  native: boolean,
): RequestAccessInfo => {
  const kind = native || metadata?.origin !== 'CONNECTOR' ? 'native' : 'connector';
  const collection =
    kind === 'native' && String(metadata?.connector).toUpperCase() === 'KB';
  return {
    recordId: recordId ?? '',
    kind,
    ...(kind === 'connector' && metadata?.webUrl && !metadata?.hideWeburl
      ? { sourceUrl: metadata.webUrl }
      : {}),
    ...(collection ? { collection } : {}),
    endpoint: ACCESS_REQUESTS_ENDPOINT,
  };
};

const redactedItem = (
  extra: Json,
  info: RequestAccessInfo,
): Json & RedactedItem => ({
  ...extra,
  redacted: true,
  reason: REDACTION_REASON,
  requestAccess: info,
});

const SENTENCE =
  /[^\n]+?(?:[.!?。！？]+["')\]]*(?:\s*\[{1,2}\d+\]{1,2})*(?=\s|$)|$)\s*/g;
const MARKER = /\[{1,2}(\d+)\]{1,2}/g;
const LINE_PREFIX = /^(\s*(?:[-*+]|\d+\.|#{1,6}|>)\s+)/;

const markersOf = (text: string): number[] =>
  [...text.matchAll(MARKER)].map((m) => Number(m[1]));

const redactUnit = (unit: string, unreadable: Set<number>): string => {
  const nums = markersOf(unit);
  if (nums.length === 0) return unit;
  if (nums.every((n) => unreadable.has(n)))
    return REDACTED_TOKEN + (/\s$/.test(unit) ? ' ' : '');
  return unit.replace(MARKER, (m, n) => (unreadable.has(Number(n)) ? '' : m));
};

const redactLine = (line: string, unreadable: Set<number>): string => {
  if (line.trimStart().startsWith('|')) {
    return markersOf(line).length > 0 &&
      markersOf(line).every((n) => unreadable.has(n))
      ? `| ${REDACTED_TOKEN} |`
      : line.replace(MARKER, (m, n) => (unreadable.has(Number(n)) ? '' : m));
  }
  const prefix = LINE_PREFIX.exec(line)?.[1] ?? '';
  const body = line.slice(prefix.length);
  return (
    prefix +
    (body.match(SENTENCE) ?? [body])
      .map((u) => redactUnit(u, unreadable))
      .join('')
  );
};

/** Redacts answer text per the sentence rule above. Fenced code is one unit. */
export const redactAnswerText = (
  content: string,
  unreadable: Set<number>,
): string => {
  if (unreadable.size === 0) return content;
  let inFence = false;
  let fenceUnits: string[] = [];
  const out: string[] = [];
  for (const line of content.split('\n')) {
    if (/^\s*```/.test(line)) {
      fenceUnits.push(line);
      inFence = !inFence;
      if (!inFence) {
        const block = fenceUnits.join('\n');
        out.push(redactUnit(block, unreadable));
        fenceUnits = [];
      }
    } else if (inFence) {
      fenceUnits.push(line);
    } else {
      out.push(redactLine(line, unreadable));
    }
  }
  if (fenceUnits.length > 0)
    out.push(redactUnit(fenceUnits.join('\n'), unreadable));
  return out
    .join('\n')
    .replace(/(\{\{redacted\}\}\s*){2,}/g, `${REDACTED_TOKEN} `);
};

/** Every record id a page of messages points at (citations and attachments). */
export const collectMessageRecordIds = (messages: Json[]): string[] => {
  const ids: string[] = [];
  for (const m of messages) {
    for (const c of m.citations ?? []) {
      const id = citationRecordId(c);
      if (id) ids.push(id);
    }
    for (const a of m.attachments ?? []) if (a.recordId) ids.push(a.recordId);
  }
  return ids;
};

/**
 * Redacts messages for a viewer who is not the conversation's author. Besides
 * unreadable sources, tool results, reasoning and the activity transcript are
 * always dropped (`detailsHidden`): tool output can carry record content that no
 * citation lets us verify per viewer.
 */
export const redactMessagesForViewer = (
  messages: Json[],
  readable: Set<string>,
): Json[] =>
  messages.map((message) => {
    const citations: Json[] = message.citations ?? [];
    const attachments: Json[] = message.attachments ?? [];
    const isReadable = (id?: string) => !!id && readable.has(id);

    const unreadable = new Set<number>();
    let anyUnreadable = false;
    const newCitations = citations.map((c) => {
      const id = citationRecordId(c);
      if (isReadable(id)) return c;
      anyUnreadable = true;
      const doc = citationDoc(c);
      if (typeof doc?.chunkIndex === 'number') unreadable.add(doc.chunkIndex);
      const raw = c?.citationId;
      return redactedItem(
        { citationId: raw?._id ?? raw },
        requestAccessFor(id, doc?.metadata, false),
      );
    });
    const newAttachments = attachments.map((a) => {
      if (isReadable(a.recordId)) return a;
      anyUnreadable = true;
      return redactedItem(
        { recordId: a.recordId },
        requestAccessFor(a.recordId, undefined, true),
      );
    });

    const {
      tools,
      reasoning,
      parts,
      referenceData,
      followUpQuestions,
      ...rest
    } = message;
    const hadDetails = [tools, reasoning, parts, referenceData].some(
      (v) => Array.isArray(v) && v.length > 0,
    );
    const base = {
      ...rest,
      ...(Array.isArray(followUpQuestions)
        ? { followUpQuestions: anyUnreadable ? [] : followUpQuestions }
        : {}),
      ...(hadDetails ? { detailsHidden: true } : {}),
    };
    if (!anyUnreadable) return base;
    // Only an assistant answer is "based on" sources; a question with a hidden
    // attachment just shows that attachment's request row.
    if (message.messageType !== 'bot_response') {
      return { ...base, citations: newCitations, attachments: newAttachments };
    }

    const everyCitationUnreadable =
      citations.length > 0 && newCitations.every((c) => c.redacted);
    // A marker with no matching citation cannot be vouched for.
    for (const n of markersOf(message.content ?? '')) {
      if (!citations.some((c) => citationDoc(c)?.chunkIndex === n))
        unreadable.add(n);
    }
    const redaction: MessageRedaction = {
      redacted: everyCitationUnreadable,
      partial: !everyCitationUnreadable,
      reason: REDACTION_REASON,
    };
    return {
      ...base,
      content: everyCitationUnreadable
        ? ''
        : redactAnswerText(message.content ?? '', unreadable),
      citations: newCitations,
      attachments: newAttachments,
      redaction,
    };
  });

/**
 * THE choke point for messages leaving for someone other than the author: the
 * author gets them as they are; anyone else gets them checked against their own
 * access (one batched call) and redacted. Every response, stream frame and model
 * history that carries a shared conversation's messages goes through this.
 */
export const redactMessagesFor = async (
  appConfig: AppConfig,
  messages: Json[],
  initiatorId: string | undefined,
  viewer: { userId: string; orgId: string },
): Promise<Json[]> => {
  if (!initiatorId || initiatorId === viewer.userId) return messages;
  return redactMessagesForViewer(
    messages,
    await readableRecordIds(appConfig, viewer, collectMessageRecordIds(messages)),
  );
};

/** A conversation object `{ initiator, messages, ... }` for the viewer. */
export const redactConversationFor = async <T extends Json>(
  appConfig: AppConfig,
  conversation: T,
  viewer: { userId: string; orgId: string },
): Promise<T> => {
  const initiator = conversation?.initiator?.toString();
  if (!initiator || initiator === viewer.userId) return conversation;
  return {
    ...conversation,
    messages: await redactMessagesFor(
      appConfig,
      conversation.messages ?? [],
      initiator,
      viewer,
    ),
  };
};

/**
 * Error frames carry a whole conversation only for its author. A viewer's
 * client refetches through the redacted GET instead.
 */
export const conversationForAuthorOnly = <T extends Json>(
  conversation: T,
  userId: string | undefined,
): T | undefined =>
  userId && conversation?.initiator?.toString() === userId
    ? conversation
    : undefined;

/** A search's stored hits: unreadable citations lose their text, name and URL. */
export const redactSearchForViewer = (
  search: Json,
  readable: Set<string>,
): Json => {
  const citations: Json[] = search.citationIds ?? [];
  let changed = false;
  const citationIds = citations.map((c) => {
    const id = c?.metadata?.recordId;
    if (id && readable.has(id)) return c;
    changed = true;
    return redactedItem(
      { _id: c?._id ?? c },
      requestAccessFor(id, c?.metadata, false),
    );
  });
  // `records` can name records no hit cites, so it is filtered on its own.
  const entries = Object.entries(search.records ?? {});
  const records = Object.fromEntries(entries.filter(([id]) => readable.has(id)));
  if (!changed && Object.keys(records).length === entries.length) return search;
  return {
    ...search,
    citationIds,
    records,
    redaction: { redacted: true, reason: REDACTION_REASON },
  };
};

export const collectSearchRecordIds = (search: Json): string[] => [
  ...(search.citationIds ?? [])
    .map((c: Json) => c?.metadata?.recordId)
    .filter(Boolean),
  ...Object.keys(search.records ?? {}),
];

const isCitationDoc = (c: unknown): boolean =>
  !!c && typeof c === 'object' && 'metadata' in c;

/**
 * Saved searches for their viewer (by id and in the history list): the author's
 * own come back as they are; a shared one has its hits checked against the
 * viewer's access, populating hits that were stored as bare ids first.
 */
export const redactSearchesFor = async (
  appConfig: AppConfig,
  searches: Json[],
  viewer: { userId: string; orgId: string },
): Promise<Json[]> => {
  const shared = searches.filter((s) => String(s.userId) !== viewer.userId);
  if (shared.length === 0) return searches;

  const bareIds = shared.flatMap((s) =>
    (s.citationIds ?? []).filter((c: unknown) => !isCitationDoc(c)),
  );
  const docs = bareIds.length
    ? await Citation.find({
        _id: { $in: bareIds },
        'metadata.orgId': viewer.orgId,
      })
        .select('-__v')
        .lean()
    : [];
  const byId = new Map(docs.map((d) => [String(d._id), d as Json]));
  const withDocs = (s: Json): Json => ({
    ...s,
    // A hit whose document is gone or foreign stays a bare id: unreadable.
    citationIds: (s.citationIds ?? []).map((c: unknown) =>
      isCitationDoc(c) ? c : (byId.get(String(c)) ?? c),
    ),
  });

  const hydrated = searches.map((s) =>
    String(s.userId) === viewer.userId ? s : withDocs(s),
  );
  const readable = await readableRecordIds(
    appConfig,
    viewer,
    hydrated
      .filter((s) => String(s.userId) !== viewer.userId)
      .flatMap(collectSearchRecordIds),
  );
  return hydrated.map((s) =>
    String(s.userId) === viewer.userId ? s : redactSearchForViewer(s, readable),
  );
};
