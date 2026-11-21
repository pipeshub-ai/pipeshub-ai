import { createHash } from 'crypto';
import { newObjectId } from '../../../../../libs/repositories/ids';
import { sanitizeAnswerMarkdown } from '../../../utils/answer-sanitizer';
import {
  LABEL_DIRECT_KEYS,
  LABEL_HISTORY_MAX,
  LABEL_PARENT_MODES,
  LABEL_ROW_FIELDS,
  LABEL_SCHEMA_VERSION,
  LEGACY_LABEL_VER,
  type AnswerProvenance,
  type LabelDirect,
  type LabelDirectKey,
  type LabelHistoryEntry,
} from './label.types';

/** The label names a schema, a kind of source or a field this build does not know: only the asker may read the answer. */
export const LABEL_UNKNOWN_VERSION = 'label_unknown_version';
/** The label could not be read as one; the part that failed follows the colon. */
export const LABEL_INVALID = 'label_invalid';
/** The label was too large to store whole; it is never stored cut. */
export const LABEL_OVER_CAP = 'label_over_cap';
/** The answer text could not be sanitised before it was stored. */
export const ANSWER_SANITIZER_FAILED = 'answer_sanitizer_failed';

/** The largest label stored, as JSON text: about a hundred times a typical one, and far below a row's limit. */
export const LABEL_MAX_JSON_LENGTH = 256 * 1024;

const LABEL_KEYS: ReadonlySet<string> = new Set([
  'v',
  'ver',
  'contentHash',
  'direct',
  'parents',
  'complete',
  'tagset',
  'pub',
  'incomplete',
]);

/** Every list but the attestations narrows who may read an answer. */
const RESTRICTING: readonly LabelDirectKey[] = LABEL_DIRECT_KEYS.filter(
  (key) => key !== 'marks',
);

const LABELLED_TYPES: ReadonlySet<string> = new Set(['bot_response', 'error']);

type Plain = Record<string, unknown>;

const isPlain = (value: unknown): value is Plain => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
};

const isStrings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string');

const isListOf = (value: unknown, item: (entry: Plain) => boolean): boolean =>
  Array.isArray(value) &&
  (value as unknown[]).every((entry) => isPlain(entry) && item(entry));

const DIRECT_CHECKS: Record<LabelDirectKey, (value: unknown) => boolean> = {
  records: isStrings,
  vrids: isStrings,
  nodes: isStrings,
  saVrids: isStrings,
  personal: isStrings,
  projects: isStrings,
  agents: (value) =>
    isListOf(
      value,
      (a) =>
        typeof a.key === 'string' &&
        (a.kind === 'service' || a.kind === 'config'),
    ),
  chat: (value) =>
    isListOf(
      value,
      (c) =>
        typeof c.conversationId === 'string' && typeof c.recordId === 'string',
    ),
  marks: (value) =>
    isListOf(
      value,
      (m) =>
        typeof m.source === 'string' &&
        (typeof m.ver === 'string' || typeof m.ver === 'number'),
    ),
};

const isParent = (p: Plain): boolean =>
  typeof p.id === 'string' &&
  typeof p.ver === 'string' &&
  (LABEL_PARENT_MODES as readonly unknown[]).includes(p.mode) &&
  (p.artifactId === undefined || typeof p.artifactId === 'string') &&
  (p.runId === undefined || typeof p.runId === 'string') &&
  (p.conversationId === undefined || typeof p.conversationId === 'string') &&
  (p.writtenAt === undefined ||
    (typeof p.writtenAt === 'number' && Number.isFinite(p.writtenAt)));

/** What a label as sent or stored gets wrong; empty when nothing. Unknown names are listed before malformed values. */
export function labelProblems(label: Plain): string[] {
  const unknown: string[] = [];
  const invalid: string[] = [];
  if (label.v !== LABEL_SCHEMA_VERSION) unknown.push(LABEL_UNKNOWN_VERSION);
  for (const key of Object.keys(label)) {
    if (!LABEL_KEYS.has(key)) unknown.push(`${LABEL_UNKNOWN_VERSION}:${key}`);
  }
  const { direct } = label;
  if (isPlain(direct)) {
    for (const key of Object.keys(direct)) {
      if (!(key in DIRECT_CHECKS)) {
        unknown.push(`${LABEL_UNKNOWN_VERSION}:direct.${key}`);
      }
    }
    for (const key of LABEL_DIRECT_KEYS) {
      // A list left out is not an empty list: the source it would name could be the one that restricts.
      if (!DIRECT_CHECKS[key](direct[key])) {
        invalid.push(`${LABEL_INVALID}:direct.${key}`);
      }
    }
  } else {
    invalid.push(`${LABEL_INVALID}:direct`);
  }
  if (!isListOf(label.parents, isParent)) {
    invalid.push(`${LABEL_INVALID}:parents`);
  }
  if (typeof label.complete !== 'boolean') {
    invalid.push(`${LABEL_INVALID}:complete`);
  }
  if (!Number.isInteger(label.tagset)) invalid.push(`${LABEL_INVALID}:tagset`);
  if (typeof label.pub !== 'boolean') invalid.push(`${LABEL_INVALID}:pub`);
  if (label.incomplete !== undefined && !isStrings(label.incomplete)) {
    invalid.push(`${LABEL_INVALID}:incomplete`);
  }
  return [...unknown, ...invalid];
}

const emptyDirect = (): LabelDirect => ({
  records: [],
  vrids: [],
  nodes: [],
  saVrids: [],
  personal: [],
  agents: [],
  chat: [],
  projects: [],
  marks: [],
});

/** A label that names nothing and is incomplete: what is stored when what arrived cannot be stored whole. */
function failClosedLabel(reasons: string[]): AnswerProvenance {
  return {
    v: LABEL_SCHEMA_VERSION,
    ver: '',
    contentHash: '',
    direct: emptyDirect(),
    parents: [],
    complete: false,
    tagset: 0,
    pub: false,
    incomplete: reasons,
  };
}

/**
 * The label to store with an answer, from the `provenance` the AI backend sent with it; undefined when it sent
 * none, so only the asker can read the answer. A label is copied whole and never cut. One this build cannot rely
 * on (an unknown version, kind of source or field, a malformed value) is kept with `complete: false` and the
 * reason added. The version id and content hash are left empty: the save fills them in.
 */
export function answerLabelFrom(raw: unknown): AnswerProvenance | undefined {
  if (raw === undefined || raw === null) return undefined;
  if (!isPlain(raw)) return failClosedLabel([`${LABEL_INVALID}:not_an_object`]);
  let json: string;
  try {
    json = JSON.stringify(raw);
  } catch {
    return failClosedLabel([`${LABEL_INVALID}:not_json`]);
  }
  if (json.length > LABEL_MAX_JSON_LENGTH) {
    return failClosedLabel([LABEL_OVER_CAP]);
  }
  const label = JSON.parse(json) as Plain;
  const problems = labelProblems(label);
  const valid = problems.length === 0;
  const complete = valid && label.complete === true;
  const direct = isPlain(label.direct) ? label.direct : {};
  const names = RESTRICTING.some(
    (key) =>
      Array.isArray(direct[key]) && (direct[key] as unknown[]).length > 0,
  );
  // An answer built on earlier rows is public only once a reader has checked them.
  const builtOnRows = Array.isArray(label.parents) && label.parents.length > 0;
  const reported = isStrings(label.incomplete) ? label.incomplete : [];
  return {
    ...label,
    ver: '',
    contentHash: '',
    complete: valid ? label.complete : false,
    pub: complete && label.pub === true && !names && !builtOnRows,
    ...(!valid && { incomplete: [...reported, ...problems] }),
  } as unknown as AnswerProvenance;
}

const withoutNul = (text: string): string =>
  text.includes('\u0000') ? text.split('\u0000').join('') : text;

/**
 * A JSON value in one form whatever store it came back from: object keys in one order (a PostgreSQL jsonb column
 * reorders them) and U+0000 removed (PostgreSQL cannot hold it, MongoDB keeps it).
 */
function canonical(value: unknown): unknown {
  if (typeof value === 'string') return withoutNul(value);
  if (value === null || typeof value !== 'object') return value;
  const toJSON = (value as { toJSON?: unknown }).toJSON;
  if (typeof toJSON === 'function') {
    return canonical((toJSON as () => unknown).call(value));
  }
  if (Array.isArray(value)) return value.map(canonical);
  const entries = Object.entries(value as Plain)
    .map(([key, item]) => [withoutNul(key), item] as const)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const out: Plain = {};
  for (const [key, item] of entries) out[key] = canonical(item);
  return out;
}

/** sha256 of a row's stored text and steps, the same in both document stores for the same row. */
export function labelContentHash(
  content: string | undefined,
  parts: unknown,
): string {
  const body = JSON.stringify([
    withoutNul(content ?? ''),
    canonical(parts ?? []),
  ]);
  return createHash('sha256').update(body, 'utf8').digest('hex');
}

/** The row kinds that carry a label: answers and the errors that end a run. */
export const isLabelledRow = (row: { messageType?: unknown }): boolean =>
  typeof row.messageType === 'string' && LABELLED_TYPES.has(row.messageType);

interface SealableRow {
  messageType?: unknown;
  content?: unknown;
  parts?: unknown;
  labelVer?: string;
  provenance?: AnswerProvenance;
}

/**
 * An answer row as it is stored: its text passed through the answer sanitiser, a fresh `labelVer`, and its label
 * (when it has one) bound to that version and to the sha256 of the stored text and steps. Every write of an
 * answer's content goes through here, in both document stores, so a label only ever describes the text beside it.
 * Other rows are returned as they are.
 */
export function sealAnswerForStorage<T extends SealableRow>(
  message: T,
  mint: () => string = newObjectId,
): T {
  if (!isLabelledRow(message)) return message;
  let content = message.content;
  let sanitized = true;
  if (typeof content === 'string') {
    try {
      content = sanitizeAnswerMarkdown(content);
    } catch {
      sanitized = false;
    }
  }
  const labelVer = mint();
  const sealed: T = { ...message, content, labelVer };
  if (message.provenance) {
    const label: AnswerProvenance = sanitized
      ? message.provenance
      : {
          ...message.provenance,
          complete: false,
          pub: false,
          incomplete: [
            ...(message.provenance.incomplete ?? []),
            ANSWER_SANITIZER_FAILED,
          ],
        };
    sealed.provenance = {
      ...label,
      ver: labelVer,
      contentHash: labelContentHash(
        typeof content === 'string' ? content : undefined,
        message.parts,
      ),
    };
  }
  return sealed;
}

/** The fields of a stored row that the archive reads. */
export interface StoredLabelFields {
  messageType?: unknown;
  labelVer?: unknown;
  runId?: unknown;
  provenance?: unknown;
  labelHistory?: unknown;
  consent?: unknown;
  consentTo?: unknown;
}

/**
 * The row's `labelHistory` once its current version is archived: the newest `LABEL_HISTORY_MAX` entries, oldest
 * first, the current label copied whole. `retiredAt` marks a version that was removed rather than replaced. A row
 * written before labels archives as the legacy version. Undefined for a row that carries no label (a question, a
 * card, a note).
 */
export function archivedLabelHistory(
  existing: StoredLabelFields,
  retiredAt?: Date,
): LabelHistoryEntry[] | undefined {
  const hasLabel =
    (typeof existing.labelVer === 'string' && existing.labelVer !== '') ||
    (existing.provenance !== undefined && existing.provenance !== null);
  if (!isLabelledRow(existing) && !hasLabel) return undefined;
  const ver =
    typeof existing.labelVer === 'string' && existing.labelVer !== ''
      ? existing.labelVer
      : LEGACY_LABEL_VER;
  const consent = existing.consent as LabelHistoryEntry['consent'] | undefined;
  const sharedThen = consent != null && consent.ver === ver;
  const entry: LabelHistoryEntry = {
    ver,
    ...(typeof existing.runId === 'string' &&
      existing.runId !== '' && { runId: existing.runId }),
    ...(existing.provenance !== undefined &&
      existing.provenance !== null && {
        provenance: existing.provenance as AnswerProvenance,
      }),
    ...(retiredAt && { retiredAt }),
    ...(sharedThen && { consent }),
    ...(sharedThen &&
      Array.isArray(existing.consentTo) && {
        consentTo: existing.consentTo as LabelHistoryEntry['consentTo'],
      }),
  };
  const earlier = Array.isArray(existing.labelHistory)
    ? (existing.labelHistory as LabelHistoryEntry[])
    : [];
  return [...earlier, entry].slice(-LABEL_HISTORY_MAX);
}

/** A row without its label fields, for anything that leaves the server; the same object when it has none. */
export function withoutLabelFields<T>(row: T): T {
  if (row === null || typeof row !== 'object') return row;
  const fields = row as Plain;
  if (!LABEL_ROW_FIELDS.some((field) => field in fields)) return row;
  const out: Plain = Object.fromEntries(
    Object.entries(fields).filter(
      ([key]) => !(LABEL_ROW_FIELDS as readonly string[]).includes(key),
    ),
  );
  return out as T;
}

/** How far a reader may rely on a row's own label. Anything but `ok` means only the asker may read the answer. */
export type LabelState =
  'ok' | 'legacy' | 'missing' | 'unknown_version' | 'stale' | 'incomplete';

/**
 * The label of a row's current content, as a reader may use it: `ok` only for a label of this build's schema, of
 * the row's current version, whose hash matches the stored text and steps, and that is complete.
 */
export function labelStateOf(row: {
  labelVer?: unknown;
  provenance?: unknown;
  content?: unknown;
  parts?: unknown;
}): LabelState {
  const ver = row.labelVer;
  if (typeof ver !== 'string' || ver === '' || ver === LEGACY_LABEL_VER) {
    return 'legacy';
  }
  const label = row.provenance;
  if (label === undefined || label === null) return 'missing';
  if (!isPlain(label)) return 'unknown_version';
  const problems = labelProblems(label);
  if (problems.some((p) => p.startsWith(LABEL_UNKNOWN_VERSION))) {
    return 'unknown_version';
  }
  if (problems.length > 0) return 'incomplete';
  const content = typeof row.content === 'string' ? row.content : undefined;
  if (
    label.ver !== ver ||
    label.contentHash !== labelContentHash(content, row.parts)
  ) {
    return 'stale';
  }
  const reasons = label.incomplete as string[] | undefined;
  if (
    label.complete !== true ||
    (reasons !== undefined && reasons.length > 0)
  ) {
    return 'incomplete';
  }
  return 'ok';
}
