import type { PendingChatNode } from '@/lib/store/pending-chat-store';
import type { AllRecordsFilter, FilterOption, KnowledgeBaseFilter } from '../types';

const isCollection = (option: FilterOption) =>
  ['KB', 'COLLECTION'].includes((option.connectorType ?? '').toUpperCase());

/**
 * The filter bar's source and connector choices as the sources a chat searches.
 * Type, status, size and date filters have no chat equivalent and are left out.
 * Empty when the bar does not narrow the sources.
 */
export function filterBarChatScope(
  filter: KnowledgeBaseFilter | AllRecordsFilter,
  sources: readonly FilterOption[]
): PendingChatNode[] {
  const ids = filter.connectorIds ?? [];
  const origins = new Set(filter.origins ?? []);
  const narrowsOrigin = origins.size === 1;
  if (ids.length === 0 && !narrowsOrigin) return [];

  const byId = new Map(sources.map((option) => [option.id, option]));
  // A chosen source missing from the options is kept: its kind is unknown.
  const chosen = ids.length > 0 ? ids.map((id) => byId.get(id) ?? { id, label: id }) : sources;
  return chosen
    .filter(
      (option) =>
        !narrowsOrigin ||
        !byId.has(option.id) ||
        origins.has(isCollection(option) ? 'COLLECTION' : 'CONNECTOR')
    )
    .map((option) => ({
      id: option.id,
      name: option.label,
      nodeType: 'app',
      connector: isCollection(option) ? 'KB' : (option.connectorType ?? ''),
    }));
}
