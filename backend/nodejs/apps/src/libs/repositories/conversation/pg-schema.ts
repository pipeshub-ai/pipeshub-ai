/**
 * What the document schemas kept of a value: the document store dropped every field a schema does not name, filled the
 * defaults, and gave sub-documents of inline array schemas an id. The PostgreSQL columns hold the same shape, so an
 * answer built from a row has the same keys as one built from a document.
 */
import { newObjectId } from '../pg-util';

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj =>
  v !== null && typeof v === 'object' && !Array.isArray(v);

/** The named fields of `v` that are set; undefined is "not set" (JSON drops it too). */
export function pick(v: unknown, keys: readonly string[]): Obj {
  const out: Obj = {};
  if (!isObj(v)) return out;
  for (const k of keys) if (v[k] !== undefined) out[k] = v[k];
  return out;
}

const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** A nested object that came out empty is not stored (the document schemas minimise them). */
const nonEmpty = (o: Obj): Obj | undefined =>
  Object.keys(o).length ? o : undefined;

const FILTER_ITEM = [
  'id',
  'name',
  'nodeType',
  'connector',
  'extension',
  'mimeType',
  'mentioned',
] as const;
const FILTER_KEYS = [
  'apps',
  'kb',
  'recordGroups',
  'records',
  'recordsExact',
] as const;

export const shapeAppliedFilters = (v: unknown): Obj =>
  Object.fromEntries(
    FILTER_KEYS.map((k) => [
      k,
      list(isObj(v) ? v[k] : undefined).map((i) => pick(i, FILTER_ITEM)),
    ]),
  );

export const shapeCitationRef = (c: unknown): Obj =>
  pick(c, ['citationId', 'relevanceScore', 'excerpt', 'context']);

export const shapeFollowUp = (q: unknown): Obj =>
  pick(q, ['question', 'confidence', 'reasoning']);

export const shapeMessageMetadata = (v: unknown): Obj | null =>
  nonEmpty(pick(v, ['processingTimeMs', 'modelVersion', 'aiTransactionId'])) ??
  null;

export const shapeAttachment = (a: unknown): Obj =>
  pick(a, [
    'recordId',
    'recordName',
    'mimeType',
    'extension',
    'virtualRecordId',
    'source',
  ]);

export const shapeReference = (r: unknown): Obj =>
  pick(r, ['name', 'id', 'type', 'app', 'webUrl', 'metadata']);

export const shapeTool = (t: unknown): Obj =>
  pick(t, ['toolName', 'toolResult']);

export const shapeReasoning = (t: unknown): Obj =>
  pick(t, ['messageId', 'turnIndex', 'content']);

export function shapeFeedback(f: unknown): Obj {
  const v = isObj(f) ? f : {};
  const out = pick(v, [
    'isHelpful',
    'followUpQuestionsHelpful',
    'feedbackProvider',
  ]);
  const ratings = nonEmpty(
    pick(v.ratings, ['accuracy', 'relevance', 'completeness', 'clarity']),
  );
  if (ratings) out.ratings = ratings;
  out.categories = list(v.categories);
  const comments = nonEmpty(
    pick(v.comments, ['positive', 'negative', 'suggestions']),
  );
  if (comments) out.comments = comments;
  out.citationFeedback = list(v.citationFeedback).map((c) =>
    pick(c, ['citationId', 'isRelevant', 'relevanceScore', 'comment']),
  );
  out.unusedFollowUpQuestions = list(v.unusedFollowUpQuestions);
  out.source = v.source ?? 'user';
  out.timestamp = v.timestamp ?? Date.now();
  out.revisions = list(v.revisions).map((r) => ({
    ...pick(r, ['previousValues', 'updatedBy']),
    updatedFields: list(isObj(r) ? r.updatedFields : undefined),
    updatedAt: isObj(r) && r.updatedAt !== undefined ? r.updatedAt : Date.now(),
  }));
  const metrics = nonEmpty(
    pick(v.metrics, [
      'timeToFeedback',
      'userInteractionTime',
      'feedbackSessionId',
      'userAgent',
      'platform',
    ]),
  );
  if (metrics) out.metrics = metrics;
  return out;
}

const CITATION_METADATA = [
  'blockNum',
  'pageNum',
  'sheetNum',
  'sheetName',
  'subcategoryLevel1',
  'subcategoryLevel2',
  'subcategoryLevel3',
  'categories',
  'departments',
  'connector',
  'connectorId',
  'connectorInstanceName',
  'recordType',
  'orgId',
  'blockType',
  'blockText',
  'mimeType',
  'recordId',
  'recordVersion',
  'topics',
  'languages',
  'recordName',
  'origin',
  'extension',
  '_id',
  'score',
  '_collection_name',
  'webUrl',
  'previewRenderable',
  'hideWeburl',
  'updatedAt',
] as const;

export function shapeCitationMetadata(v: unknown): Obj {
  const m = pick(v, CITATION_METADATA);
  m.recordVersion ??= 0;
  m.previewRenderable ??= true;
  for (const k of ['blockNum', 'pageNum', 'departments', 'topics', 'languages'])
    m[k] ??= [];
  m.bounding_box = list(isObj(v) ? v.bounding_box : undefined).map((b) => ({
    ...pick(b, ['x', 'y']),
    _id: newObjectId(),
  }));
  return m;
}
