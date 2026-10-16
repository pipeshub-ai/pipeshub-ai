import { useMemo, useRef } from 'react';
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
export function useTickedRows<T extends TickableRow>(
  visibleRows: readonly T[],
  tickedIds: ReadonlySet<string>
): T[] {
  const rowsRef = useRef(new Map<string, T>());

  return useMemo(() => {
    const rows = rowsRef.current;
    for (const id of [...rows.keys()]) {
      if (!tickedIds.has(id)) rows.delete(id);
    }
    for (const row of visibleRows) {
      if (tickedIds.has(row.id)) rows.set(row.id, row);
    }
    return [...rows.values()];
  }, [visibleRows, tickedIds]);
}

/** A ticked row as a node a chat is scoped to. */
export function toPendingChatNode(row: TickableRow): PendingChatNode {
  return {
    id: row.id,
    name: row.name,
    nodeType: row.nodeType ?? (row.type === 'folder' ? 'folder' : 'record'),
    connector: row.connector ?? '',
  };
}
