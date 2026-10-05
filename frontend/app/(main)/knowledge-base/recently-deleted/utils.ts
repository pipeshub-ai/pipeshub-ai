import type { TFunction } from 'i18next';
import { getUserFacingErrorMessage, isProcessedError } from '@/lib/api/api-error';
import type { BulkRestoreResponse, RestoreResponse, RestoreResultEntry, TrashItem, TrashListResponse } from './types';

const DAY_MS = 24 * 60 * 60 * 1000;
/** The purge's default minimum age (softDeletePurge.minAgeDays). */
export const DEFAULT_RETENTION_DAYS = 14;
/** Failures named one by one in a message before the rest are counted. */
const MAX_FAILURES_LISTED = 3;

export function formatTrashDate(timestamp: number, locale?: string): string {
  return new Date(timestamp).toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** Whole days the trash keeps an item, or null when unknown or shorter than a day (test overrides). */
export function retentionDays(retention: TrashListResponse['retention']): number | null {
  const minAgeMs = retention?.minAgeMs;
  if (typeof minAgeMs !== 'number' || minAgeMs < DAY_MS) return null;
  return Math.round(minAgeMs / DAY_MS);
}

export function retentionNote(retention: TrashListResponse['retention'], t: TFunction): string {
  return t('collections.trash.retentionNote', { days: retentionDays(retention) ?? DEFAULT_RETENTION_DAYS });
}

export function itemDisplayName(item: TrashItem, t: TFunction): string {
  return item.name?.trim() || t('collections.trash.untitled');
}

/** What else the delete took with it: a folder's contents, or a file's attachments. */
export function itemsInside(item: TrashItem): number {
  return Math.max((item.itemCount || 1) - 1, 0);
}

export function itemTypeLabel(item: TrashItem, t: TFunction): string {
  const inside = itemsInside(item);
  if (item.isFolder) {
    return inside > 0 ? t('collections.trash.folderWithItems', { count: inside }) : t('collections.trash.typeFolder');
  }
  return inside > 0 ? t('collections.trash.fileWithAttachments', { count: inside }) : t('collections.trash.typeFile');
}

export function locationLabel(item: TrashItem, collectionName: string, t: TFunction): string {
  return item.parentName?.trim() || collectionName || t('collections.trash.topLevel');
}

export function deletedByLabel(item: TrashItem, t: TFunction): string {
  return item.deletedBy?.name?.trim() || item.deletedBy?.email?.trim() || t('collections.trash.unknownUser');
}

export function removalLabel(item: TrashItem, t: TFunction, locale?: string): string {
  if (typeof item.removableAfterTimestamp === 'number') {
    return t('collections.trash.removedAfterDate', { date: formatTrashDate(item.removableAfterTimestamp, locale) });
  }
  return t('collections.trash.removedAfterDays', { days: DEFAULT_RETENTION_DAYS });
}

export interface RestoreFailure {
  id: string;
  name: string;
  reason: string;
}

export interface RestoreOutcome {
  /** How many listed items came back. */
  restored: number;
  /** How many listed items were asked for. */
  requested: number;
  failures: RestoreFailure[];
  renamed: Array<{ from: string; to: string }>;
  /** Some restored files could not be queued for indexing yet. */
  reindexPending: boolean;
  /** The one item restored, when exactly one was asked for and it came back. */
  singleName?: string;
}

function fallbackReason(code: number | undefined, t: TFunction): string {
  if (code === 403) return t('collections.trash.noPermission');
  if (code === 404) return t('collections.trash.gone');
  if (code === 409) return t('collections.trash.changed');
  return t('collections.trash.restoreFailed');
}

/** Why a restore request failed, in the server's words when they are fit to show. */
export function reasonFromError(error: unknown, t: TFunction): string {
  const code = isProcessedError(error) ? error.statusCode : undefined;
  return getUserFacingErrorMessage(error, fallbackReason(code, t));
}

function renamesOf(response: RestoreResponse): Array<{ from: string; to: string }> {
  return (response.restoredRecords ?? [])
    .filter((r) => r.renamedFrom && r.name)
    .map((r) => ({ from: r.renamedFrom as string, to: r.name as string }));
}

export function outcomeOfSingle(item: TrashItem, response: RestoreResponse, t: TFunction): RestoreOutcome {
  return {
    restored: 1,
    requested: 1,
    failures: [],
    renamed: renamesOf(response),
    reindexPending: response.reindexPending === true,
    singleName: itemDisplayName(item, t),
  };
}

export function outcomeOfSingleError(item: TrashItem, error: unknown, t: TFunction): RestoreOutcome {
  return {
    restored: 0,
    requested: 1,
    failures: [{ id: item.id, name: itemDisplayName(item, t), reason: reasonFromError(error, t) }],
    renamed: [],
    reindexPending: false,
  };
}

function entryReason(entry: RestoreResultEntry | undefined, t: TFunction): string {
  const reason = entry?.reason?.trim();
  return reason || fallbackReason(entry?.code, t);
}

export function outcomeOfBulk(items: TrashItem[], response: BulkRestoreResponse, t: TFunction): RestoreOutcome {
  const byId = new Map((response.results ?? []).map((entry) => [entry.recordId, entry]));
  const failures: RestoreFailure[] = [];
  const renamed: Array<{ from: string; to: string }> = [];
  let reindexPending = false;
  let restored = 0;
  for (const item of items) {
    const entry = byId.get(item.id);
    if (entry?.success) {
      restored += 1;
      renamed.push(...renamesOf(entry));
      reindexPending ||= entry.reindexPending === true;
    } else {
      failures.push({ id: item.id, name: itemDisplayName(item, t), reason: entryReason(entry, t) });
    }
  }
  return {
    restored,
    requested: items.length,
    failures,
    renamed,
    reindexPending,
    singleName: items.length === 1 && restored === 1 ? itemDisplayName(items[0], t) : undefined,
  };
}

export function outcomeOfBulkError(items: TrashItem[], error: unknown, t: TFunction): RestoreOutcome {
  const reason = reasonFromError(error, t);
  return {
    restored: 0,
    requested: items.length,
    failures: items.map((item) => ({ id: item.id, name: itemDisplayName(item, t), reason })),
    renamed: [],
    reindexPending: false,
  };
}

export interface RestoreMessage {
  variant: 'success' | 'warning' | 'error';
  title: string;
  description: string;
}

function failureLines(failures: RestoreFailure[], t: TFunction): string {
  const shown = failures.slice(0, MAX_FAILURES_LISTED).map((f) => t('collections.trash.failureLine', { name: f.name, reason: f.reason }));
  const more = failures.length - shown.length;
  if (more > 0) shown.push(t('collections.trash.moreFailures', { count: more }));
  return shown.join('\n');
}

/** The toast a restore ends with: what came back, what didn't and why, and when search catches up. */
export function restoreMessage(outcome: RestoreOutcome, t: TFunction): RestoreMessage {
  const { restored, requested, failures } = outcome;
  if (restored === 0) {
    if (requested === 1 && failures.length === 1) {
      return {
        variant: 'error',
        title: t('collections.trash.restoreFailedTitle', { name: failures[0].name }),
        description: failures[0].reason,
      };
    }
    return { variant: 'error', title: t('collections.trash.noneRestored', { count: requested }), description: failureLines(failures, t) };
  }

  const notes: string[] = [
    outcome.reindexPending ? t('collections.trash.reindexPending') : t('collections.trash.searchableAfterIndexing'),
    ...outcome.renamed.map((r) => t('collections.trash.renamed', { from: r.from, to: r.to })),
  ];
  if (failures.length > 0) {
    return {
      variant: 'warning',
      title: t('collections.trash.partiallyRestored', { restored, total: requested }),
      description: [failureLines(failures, t), ...notes].join('\n'),
    };
  }
  return {
    variant: 'success',
    title: outcome.singleName
      ? t('collections.trash.restoredOne', { name: outcome.singleName })
      : t('collections.trash.restoredMany', { count: restored }),
    description: notes.join('\n'),
  };
}

/** The page to show after rows left the list: the last one that still has rows. */
export function pageAfterRemoval(page: number, limit: number, totalAfter: number): number {
  const lastPage = Math.max(Math.ceil(totalAfter / limit), 1);
  return Math.min(page, lastPage);
}

/**
 * Folders before what was deleted from inside them, so one bulk restore brings
 * back both: the server refuses an item while its folder is still in the trash.
 */
export function orderForRestore(items: TrashItem[]): TrashItem[] {
  const pending = new Map(items.map((item) => [item.id, item]));
  const ordered: TrashItem[] = [];
  while (pending.size > 0) {
    const ready = [...pending.values()].filter((item) => !item.parentId || !pending.has(item.parentId));
    // A cycle cannot come from a folder tree; keep the rest as listed rather than loop.
    const next = ready.length > 0 ? ready : [...pending.values()];
    for (const item of next) {
      ordered.push(item);
      pending.delete(item.id);
    }
  }
  return ordered;
}
