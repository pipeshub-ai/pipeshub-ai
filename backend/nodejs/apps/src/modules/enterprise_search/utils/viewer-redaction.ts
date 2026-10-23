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
import mongoose from 'mongoose';
import Citation from '../schema/citation.schema';
import { Users } from '../../user_management/schema/users.schema';

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

/**
 * Which markers are real citations of a message: the number is one of its
 * citations' chunk indexes and, written as a link, the target is that citation's
 * own record URL (or web URL). Citations without a populated document resolve
 * nothing, so their text stays uncited (the message then has no readable source).
 */
interface CitedDoc {
  chunkIndex?: number;
  metadata?: { recordId?: string; webUrl?: string };
}

const citationResolver = (citations: Json[]): CitationResolver => {
  const byIndex = new Map<number, CitedDoc>();
  for (const c of citations) {
    const doc = citationDoc(c) as CitedDoc | undefined;
    if (typeof doc?.chunkIndex === 'number') byIndex.set(doc.chunkIndex, doc);
  }
  return (n, target) => {
    const doc = byIndex.get(n);
    if (!doc) return false;
    // Only the link form is canonical; a bare `[2]` or `[[2]]` is not trusted as a citation.
    if (target === undefined) return 'unreadable';
    const recordId = doc.metadata?.recordId;
    const web = doc.metadata?.webUrl;
    const own =
      (!!recordId && target.includes(`/record/${recordId}/`)) ||
      (!!web && target.split('#')[0] === web.split('#')[0]);
    return own ? true : 'unreadable';
  };
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

/** A citation as stored: `[N]`, `[[N]]`, or a markdown link `[N](url)`. */
const CITE = /\[{1,2}(\d+)\]{1,2}(?:\(([^)\s]*)\))?/g;
const LINE_PREFIX = /^(\s*(?:[-*+]|\d+\.|#{1,6}|>)\s+)/;
/** Only separators between the citations of one group. */
const GROUP_SEPARATOR = /^[\s,;]*$/;
/** Punctuation written right after a citation group belongs to its span. */
const TRAILING_PUNCTUATION = /^[.!?。！？…,;:)"'”’」』]*/;

/**
 * Classifies `[n]` (with link target `target`, when written as a link):
 *   - `true`: the canonical form, `[n](own record or web URL)`, a real citation;
 *   - `'unreadable'`: the number is one of the message's citations but the form is
 *     not canonical (bare `[2]`, `[[2]]`, a foreign or wrong URL). It ends a span
 *     as an UNREADABLE group, so the span is redacted;
 *   - `false`: the number is none of its citations (`arr[9]`): plain text.
 * A marker inside code is always `'unreadable'` when its number resolves.
 */
export type CitationResolver = (
  n: number,
  target?: string,
) => boolean | 'unreadable';
const anyMarker: CitationResolver = (_n, target) =>
  target === undefined ? 'unreadable' : true;

interface Cite {
  start: number;
  end: number;
  n: number;
  /** Counts as an unreadable citation whatever the viewer may read. */
  bad: boolean;
}

const INLINE_CODE = /`[^`\n]*`/g;

const citesIn = (text: string, resolves: CitationResolver): Cite[] => {
  const code = [...text.matchAll(INLINE_CODE)].map((m): [number, number] => [
    m.index,
    m.index + m[0].length,
  ]);
  const cites: Cite[] = [];
  for (const m of text.matchAll(new RegExp(CITE.source, 'g'))) {
    const verdict = resolves(Number(m[1]), m[2]);
    if (verdict === false) continue;
    const inCode = code.some(([a, b]) => m.index >= a && m.index < b);
    // `[[2]](url)` and `[2]` are not the canonical `[N](url)`.
    const canonical = /^\[\d+\]\(/.test(m[0]);
    cites.push({
      start: m.index,
      end: m.index + m[0].length,
      n: Number(m[1]),
      bad: verdict === 'unreadable' || inCode || !canonical,
    });
  }
  return cites;
};

/**
 * Fail-closed attribution by citation spans (D-22 refinement 2). In a turn that
 * used any source the viewer cannot read, only spans whose group has a readable
 * citation survive. A span is the text from the end of the previous citation
 * group (or the block start) up to and including the next group, plus the
 * punctuation right after it; a group is citations separated only by whitespace,
 * commas or semicolons. A mixed group keeps its text and shows only its readable
 * citations. Everything else, uncited text, all-unreadable groups, is replaced by
 * the redacted notice.
 */
const redactSpans = (
  text: string,
  unreadable: Set<number>,
  resolves: CitationResolver,
): string => {
  const isBad = (c: Cite): boolean => c.bad || unreadable.has(c.n);
  const groups: Cite[][] = [];
  for (const c of citesIn(text, resolves)) {
    const last = groups[groups.length - 1];
    const tail = last?.[last.length - 1];
    if (last && tail && GROUP_SEPARATOR.test(text.slice(tail.end, c.start))) {
      last.push(c);
    } else {
      groups.push([c]);
    }
  }
  /** The notice for a dropped stretch, keeping the whitespace or list marker that opened its last line. */
  const notice = (dropped: string): string => {
    const lastLine = dropped.slice(dropped.lastIndexOf('\n') + 1);
    const marker = dropped.includes('\n')
      ? (LINE_PREFIX.exec(lastLine)?.[1] ?? '')
      : '';
    const space = /^\s*/.exec(dropped)?.[0] ?? '';
    return (marker ? `\n${marker}` : space) + REDACTED_TOKEN;
  };
  let out = '';
  let pos = 0;
  for (const group of groups) {
    const first = group[0];
    const final = group[group.length - 1];
    if (!first || !final) continue;
    const start = first.start;
    const groupEnd = final.end;
    const end =
      groupEnd +
      (TRAILING_PUNCTUATION.exec(text.slice(groupEnd))?.[0].length ?? 0);
    const lead = text.slice(pos, start);
    if (group.every(isBad)) {
      out += notice(lead);
    } else {
      out += lead;
      let p = start;
      for (const c of group) {
        if (!isBad(c)) out += text.slice(p, c.end);
        p = c.end;
      }
      out += text.slice(groupEnd, end);
    }
    pos = end;
  }
  // Text after the last group is uncited.
  const rest = text.slice(pos);
  return out + (rest.trim() === '' ? rest : notice(rest));
};

/** Only notices (and the list, quote or heading marks that carried them) are left. */
const isHidden = (text: string): boolean =>
  text.trim() !== '' &&
  text
    .split('\n')
    .every(
      (l) =>
        /^(\s*([-*+]|\d+\.|#{1,6}|>)\s+)?(\{\{redacted\}\}\s*)+$/.test(l) ||
        l.trim() === '',
    );

const isTableRow = (line: string): boolean => line.trimStart().startsWith('|');
const isSeparatorRow = (line: string): boolean =>
  /^\s*\|?(\s*:?-{3,}:?\s*\|?)+\s*$/.test(line);

/** A contiguous run of plain lines, a heading, or a list item: spans may reach back across its line breaks. */
const redactRun = (
  run: string,
  unreadable: Set<number>,
  resolves: CitationResolver,
): string => {
  const prefix = LINE_PREFIX.exec(run)?.[1] ?? '';
  return prefix + redactSpans(run.slice(prefix.length), unreadable, resolves);
};

/**
 * Each cell is its own block: it keeps only what a readable citation in that cell
 * covers. A table with nothing visible left is one hidden block (the caller merges
 * it), so its rows and columns are not revealed.
 */
const redactTable = (
  rows: string[],
  unreadable: Set<number>,
  resolves: CitationResolver,
): string => {
  const shown = rows
    .map((row) =>
      isSeparatorRow(row)
        ? row
        : row
            .split('|')
            .map((cell) =>
              cell.trim() === ''
                ? cell
                : ` ${redactSpans(cell.trim(), unreadable, resolves)} `,
            )
            .join('|'),
    )
    .join('\n');
  const cells = shown
    .split('\n')
    .filter((row) => !isSeparatorRow(row))
    .flatMap((row) => row.split('|'))
    .map((cell) => cell.trim())
    .filter(Boolean);
  return cells.every((cell) => cell === REDACTED_TOKEN)
    ? REDACTED_TOKEN
    : shown;
};

type Unit = {
  kind: 'blank' | 'heading' | 'run' | 'table' | 'fence';
  lines: string[];
};

interface Part {
  text: string;
  kind: 'blank' | 'hidden' | 'shown';
}

const toUnits = (content: string): Unit[] => {
  const units: Unit[] = [];
  let fence: string[] | null = null;
  const push = (kind: Unit['kind'], line: string): void => {
    const last = units[units.length - 1];
    if (
      last &&
      last.kind === kind &&
      (kind === 'run' || kind === 'table' || kind === 'blank')
    )
      last.lines.push(line);
    else units.push({ kind, lines: [line] });
  };
  for (const line of content.split('\n')) {
    const isFence = /^\s*```/.test(line);
    if (fence) {
      fence.push(line);
      if (isFence) {
        units.push({ kind: 'fence', lines: fence });
        fence = null;
      }
    } else if (isFence) fence = [line];
    else if (line.trim() === '') push('blank', line);
    else if (/^\s*#{1,6}\s/.test(line))
      units.push({ kind: 'heading', lines: [line] });
    else if (isTableRow(line)) push('table', line);
    else push('run', line);
  }
  if (fence) units.push({ kind: 'fence', lines: fence });
  return units;
};

/**
 * Redacts answer text for a viewer who cannot read every source of the turn
 * (`unreadable` is the set of their unreadable citation numbers). Blocks are never
 * crossed: blank lines, headings, table cells and fenced code. Inside one
 * contiguous run of lines a span reaches back across line breaks and list items to
 * the previous citation group. Fenced code is dropped whole: markers inside code
 * never count as citations. A turn the viewer can read in full is returned as is.
 * One function for the viewer response, the model history and the SSE complete
 * frame; tokens only ever stream to the person the turn belongs to.
 */
export const redactAnswerText = (
  content: string,
  unreadable: Set<number>,
  resolves: CitationResolver = anyMarker,
): string => {
  if (unreadable.size === 0) return content;
  const parts = toUnits(content).map((unit): Part => {
    if (unit.kind === 'blank')
      return { text: unit.lines.join('\n'), kind: 'blank' };
    if (unit.kind === 'fence') return { text: REDACTED_TOKEN, kind: 'hidden' };
    const text =
      unit.kind === 'table'
        ? redactTable(unit.lines, unreadable, resolves)
        : redactRun(unit.lines.join('\n'), unreadable, resolves);
    return isHidden(text)
      ? { text: REDACTED_TOKEN, kind: 'hidden' }
      : { text, kind: 'shown' };
  });
  // Hidden blocks with nothing visible between them become one notice.
  const out: string[] = [];
  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    if (!part) continue;
    if (part.kind === 'hidden') {
      let k = i;
      while (parts[k + 1]?.kind === 'blank' && parts[k + 2]?.kind === 'hidden')
        k += 2;
      out.push(REDACTED_TOKEN);
      i = k;
    } else out.push(part.text);
  }
  return out
    .join('\n')
    .replace(
      /(\{\{redacted\}\}\s*){2,}/g,
      (m) => REDACTED_TOKEN + (/\s$/.test(m) ? ' ' : ''),
    );
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
 * Redacts messages for a viewer who is not their author. Besides
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

    // A citation with no populated document cannot be judged: nothing of the answer is shown.
    const everyCitationUnreadable =
      citations.length > 0 &&
      (newCitations.every((c) => c.redacted) ||
        citations.some((c) => !citationDoc(c)));
    const redaction: MessageRedaction = {
      redacted: everyCitationUnreadable,
      partial: !everyCitationUnreadable,
      reason: REDACTION_REASON,
    };
    return {
      ...base,
      content: everyCitationUnreadable
        ? ''
        : redactAnswerText(
            message.content ?? '',
            unreadable,
            citationResolver(citations),
          ),
      citations: newCitations,
      attachments: newAttachments,
      redaction,
    };
  });

/** Who a message was generated for: its stamp, else the conversation's initiator. */
export const messageAuthorId = (
  message: Json,
  initiatorId: string | undefined,
): string | undefined => {
  const stamp = message.authorUserId as { toString(): string } | undefined;
  return stamp?.toString() ?? initiatorId;
};

/**
 * THE choke point for messages leaving for a viewer. Redaction is per turn: a
 * message the viewer authored is theirs, in full, tool results and reasoning
 * included; every other message, the conversation owner's view of someone else's
 * turn included, is checked against the viewer's own access (one batched call)
 * and redacted. Every response, stream frame and model history that carries a
 * shared conversation's messages goes through this.
 */
export const redactMessagesFor = async (
  appConfig: AppConfig,
  messages: Json[],
  initiatorId: string | undefined,
  viewer: { userId: string; orgId: string },
): Promise<Json[]> => {
  const isOthers = (m: Json): boolean => {
    const author = messageAuthorId(m, initiatorId);
    // Callers may hand over an ObjectId where a string is typed.
    const viewerId: unknown = viewer.userId;
    return !!author && author !== String(viewerId);
  };
  const others = messages.filter(isOthers);
  if (others.length === 0) return messages;
  const readable = await readableRecordIds(
    appConfig,
    viewer,
    collectMessageRecordIds(others),
  );
  const askers = await askerNames(
    others.filter((m) => m.messageType === 'user_query'),
    initiatorId,
    viewer.orgId,
  );
  return messages.map((m) => {
    if (!isOthers(m)) return m;
    const [redacted] = redactMessagesForViewer([m], readable);
    if (!redacted || m.messageType !== 'user_query') return redacted ?? m;
    const userId = messageAuthorId(m, initiatorId) ?? '';
    return { ...redacted, askedBy: { userId, name: askers.get(userId) ?? '' } };
  });
};

export const userDisplayName = (user: {
  fullName?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
}): string => {
  const fullName = user.fullName?.trim();
  if (fullName) return fullName;
  const parts = [user.firstName, user.lastName]
    .filter((part): part is string => Boolean(part?.trim()))
    .join(' ')
    .trim();
  return parts || user.email?.trim() || '';
};

/** Display names of the users who asked these questions, looked up in the viewer's own org only. */
const askerNames = async (
  questions: Json[],
  initiatorId: string | undefined,
  orgId: string,
): Promise<Map<string, string>> => {
  const ids = [
    ...new Set(
      questions.map((m) => messageAuthorId(m, initiatorId)).filter(Boolean),
    ),
  ] as string[];
  if (ids.length === 0 || !mongoose.isValidObjectId(orgId)) return new Map();
  try {
    const users = await Users.find({
      orgId: new mongoose.Types.ObjectId(orgId),
      isDeleted: false,
      _id: { $in: ids.filter((id) => mongoose.isValidObjectId(id)) },
    })
      .select('fullName firstName lastName email')
      .lean()
      .exec();
    return new Map(users.map((u) => [String(u._id), userDisplayName(u)]));
  } catch (error: unknown) {
    logger.warn('Could not resolve who asked', {
      error: error instanceof Error ? error.message : String(error),
    });
    return new Map();
  }
};

/** A conversation object `{ initiator, messages, ... }` for the viewer. */
export const redactConversationFor = async <T extends Json>(
  appConfig: AppConfig,
  conversation: T,
  viewer: { userId: string; orgId: string },
): Promise<T> => {
  const initiator = conversation?.initiator?.toString();
  if (!initiator) return conversation;
  const messages = await redactMessagesFor(
    appConfig,
    conversation.messages ?? [],
    initiator,
    viewer,
  );
  return messages === conversation.messages
    ? conversation
    : { ...conversation, messages };
};

/**
 * Error frames carry a whole conversation only when every message in it is the
 * receiving user's own. Anyone else's client refetches through the redacted GET.
 */
export const conversationForAuthorOnly = <T extends Json>(
  conversation: T,
  userId: string | undefined,
): T | undefined => {
  const initiator = (
    conversation.initiator as { toString(): string } | undefined
  )?.toString();
  return userId &&
    initiator &&
    ((conversation.messages as Json[] | undefined) ?? []).every(
      (m) => messageAuthorId(m, initiator) === userId,
    )
    ? conversation
    : undefined;
};

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
