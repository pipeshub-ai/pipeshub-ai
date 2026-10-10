import { useCallback, useEffect, useRef } from 'react';
import type { PendingChatNode } from '@/lib/store/pending-chat-store';

interface TickableRow {
  id: string;
  name: string;
  nodeType?: string;
  type?: string;
  connector?: string;
}

/**
 * The ticked rows of a listing, including ones ticked on a page of it that is
 * no longer shown: the store keeps ticked ids only.
 */
export function useTickedRows(
  visibleRows: readonly TickableRow[],
  tickedIds: ReadonlySet<string>
): () => PendingChatNode[] {
  const rowsRef = useRef(new Map<string, PendingChatNode>());

  useEffect(() => {
    const rows = rowsRef.current;
    for (const id of [...rows.keys()]) {
      if (!tickedIds.has(id)) rows.delete(id);
    }
    for (const row of visibleRows) {
      if (!tickedIds.has(row.id)) continue;
      rows.set(row.id, {
        id: row.id,
        name: row.name,
        nodeType: row.nodeType ?? (row.type === 'folder' ? 'folder' : 'record'),
        connector: row.connector ?? '',
      });
    }
  }, [visibleRows, tickedIds]);

  return useCallback(() => [...rowsRef.current.values()], []);
}
