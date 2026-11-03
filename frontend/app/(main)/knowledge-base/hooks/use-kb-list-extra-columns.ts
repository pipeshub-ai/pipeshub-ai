import type { KbListExtraColumns } from '../components/kb-list-view';

/** Extra list columns for the knowledge-base table. Editions plug in via config.ts. */
export function useKbListExtraColumns(_items: unknown[], _refreshKey?: number): KbListExtraColumns | null {
  return null;
}
