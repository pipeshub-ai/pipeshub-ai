import type { ChatKnowledgeFilters } from '@/chat/types';

/** What a connected app (an OAuth app or a personal access token) may see, as the API takes it. */
export interface KnowledgeLimit {
  apps: string[];
  recordGroups: string[];
  records: string[];
}

/** The API refuses a limit with more ids: it is signed into every token the app gets. */
export const MAX_KNOWLEDGE_LIMIT_IDS = 50;

/** The choice on screen: null for everything the user can see, otherwise the picker's selection. */
export type KnowledgeLimitValue = ChatKnowledgeFilters | null;

export const EMPTY_SELECTION: ChatKnowledgeFilters = { apps: [], kb: [], recordGroups: [], records: [] };

/** The picker's selection as the API's limit. Collections are apps there, and the
 * picker offers no way to pick a record without what is under it. */
export function limitFromSelection(selection: ChatKnowledgeFilters): KnowledgeLimit {
  const unique = (ids: string[]) => [...new Set(ids)];
  return {
    apps: unique([...selection.apps, ...selection.kb]),
    recordGroups: unique(selection.recordGroups ?? []),
    records: unique([...(selection.records ?? []), ...(selection.recordsExact ?? [])]),
  };
}

export function limitSize(limit: KnowledgeLimit): number {
  return limit.apps.length + limit.recordGroups.length + limit.records.length;
}

/** A stored limit as the picker's selection, for editing it. */
export function selectionFromLimit(limit?: KnowledgeLimit | null): KnowledgeLimitValue {
  if (!limit) return null;
  return { apps: [...limit.apps], kb: [], recordGroups: [...limit.recordGroups], records: [...limit.records] };
}

/** The limit to send, or undefined for everything the user can see. */
export function knowledgeLimitForApi(value: KnowledgeLimitValue): KnowledgeLimit | undefined {
  return value === null ? undefined : limitFromSelection(value);
}

/** Whether the API will take this choice: a pick must name between one and the most ids. */
export function isKnowledgeLimitValid(value: KnowledgeLimitValue): boolean {
  if (value === null) return true;
  const size = limitSize(limitFromSelection(value));
  return size > 0 && size <= MAX_KNOWLEDGE_LIMIT_IDS;
}
