'use client';

import { PaginationFooter } from '@/app/components/data-display/pagination-footer';

interface KbPaginationState {
  limit: number;
  totalItems: number;
  startIndex?: number;
  endIndex?: number;
  hasNext: boolean;
  hasPrev: boolean;
  nextCursor?: string | null;
  prevCursor?: string | null;
}

/** Keyset paging has no page number of its own; derive one from the item index for display. */
export function KbPaginationFooter({
  pagination,
  onCursorChange,
  onLimitChange,
}: {
  pagination: KbPaginationState;
  onCursorChange?: (cursor: string | null, direction?: 'next' | 'prev') => void;
  onLimitChange?: (limit: number) => void;
}) {
  const limit = Math.max(1, pagination.limit);
  const page = Math.floor(((pagination.startIndex ?? 1) - 1) / limit) + 1;
  return (
    <div style={{ position: 'relative', zIndex: 16, flexShrink: 0 }}>
      <PaginationFooter
        pagination={{
          page,
          limit,
          totalCount: pagination.totalItems,
          totalPages: Math.max(1, Math.ceil(pagination.totalItems / limit)),
          startIndex: pagination.startIndex ?? 0,
          endIndex: pagination.endIndex ?? 0,
          hasPrev: pagination.hasPrev,
          hasNext: pagination.hasNext,
        }}
        // The footer only fires page±1, so the sign alone picks the cursor direction.
        onPageChange={(next) =>
          next > page
            ? onCursorChange?.(pagination.nextCursor ?? null, 'next')
            : onCursorChange?.(pagination.prevCursor ?? null, 'prev')
        }
        onLimitChange={onLimitChange}
      />
    </div>
  );
}
