'use client';

import type { ReactNode, RefObject } from 'react';
import Link from 'next/link';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { EmptyIcon } from '@/app/components/ui';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { TableSkeleton } from '@/app/components/data-display';
import { useTranslation } from 'react-i18next';
import type { ArtifactListItem, ArtifactsOwnership, ArtifactsSortField, ArtifactsViewMode } from '../types';
import type { ArtifactActionHandlers } from './artifact-actions';
import type { PaginationState } from '@/app/components/data-display/pagination-footer';
import { ArtifactsListView } from './artifacts-list-view';
import { ArtifactsGridView } from './artifacts-grid-view';

interface ArtifactsDataTableProps extends ArtifactActionHandlers {
  items: ArtifactListItem[];
  viewMode: ArtifactsViewMode;
  ownership: ArtifactsOwnership;
  isLoading: boolean;
  error: string | null;
  /** Search, type or date filters are narrowing the list. */
  hasActiveFilters: boolean;
  sortBy: ArtifactsSortField;
  sortOrder: 'asc' | 'desc';
  onSort: (field: ArtifactsSortField) => void;
  onRetry: () => void;
  onClearFilters: () => void;
  /** Focus lands here after a delete removes the focused row. */
  listRef?: RefObject<HTMLDivElement | null>;
  pagination: PaginationState;
  onPageChange: (page: number) => void;
}

function CenteredState({ icon, title, hint, action }: { icon: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <Flex direction="column" align="center" justify="center" gap="2" style={{ flex: 1, padding: 'var(--space-6)', textAlign: 'center' }}>
      {icon}
      <Text size="3" weight="medium" style={{ marginTop: 'var(--space-2)' }}>
        {title}
      </Text>
      {hint && (
        <Text size="2" style={{ color: 'var(--slate-10)', maxWidth: 360 }}>
          {hint}
        </Text>
      )}
      {action && <Box style={{ marginTop: 'var(--space-3)' }}>{action}</Box>}
    </Flex>
  );
}

function GridSkeleton() {
  return (
    <Box
      aria-busy="true"
      style={{
        padding: 'var(--space-4)',
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
        gap: 'var(--space-4)',
      }}
    >
      {Array.from({ length: 8 }).map((_, i) => (
        <Box
          key={i}
          style={{
            height: 212,
            borderRadius: 'var(--radius-3)',
            border: '1px solid var(--olive-3)',
            background: 'var(--olive-2)',
            animation: 'shimmer-pulse 1.4s ease-in-out infinite',
            animationDelay: `${i * 80}ms`,
          }}
        />
      ))}
    </Box>
  );
}

export function ArtifactsDataTable({
  items,
  viewMode,
  ownership,
  isLoading,
  error,
  hasActiveFilters,
  sortBy,
  sortOrder,
  onSort,
  onRetry,
  onClearFilters,
  listRef,
  pagination,
  onPageChange,
  ...handlers
}: ArtifactsDataTableProps) {
  const { t } = useTranslation();
  const region = (content: ReactNode, busy = false) => (
    <Flex
      ref={listRef}
      tabIndex={-1}
      direction="column"
      aria-label={t('nav.allArtifacts')}
      aria-busy={busy}
      style={{ flex: 1, minHeight: 0, outline: 'none', opacity: busy ? 0.6 : 1, transition: 'opacity 150ms ease' }}
    >
      {content}
    </Flex>
  );

  if (error) {
    return region(
      <CenteredState
        icon={<MaterialIcon name="cloud_off" size={40} color="var(--slate-9)" />}
        title={error}
        hint={t('artifacts.loadFailedHint')}
        action={
          <Button variant="soft" color="gray" onClick={onRetry}>
            <MaterialIcon name="refresh" size={16} />
            {t('action.tryAgain')}
          </Button>
        }
      />,
    );
  }

  if (isLoading && items.length === 0) {
    return region(
      viewMode === 'grid' ? (
        <GridSkeleton />
      ) : (
        <TableSkeleton rows={8} rowHeight={48} hasCheckbox={false} columns={[{ barWidth: 0.5 }, { width: '140px' }, { barWidth: 0.7 }, { width: '120px' }, { width: '90px' }]} />
      ),
    );
  }

  if (items.length === 0) {
    return region(hasActiveFilters ? (
      <CenteredState
        icon={<MaterialIcon name="search_off" size={40} color="var(--slate-9)" />}
        title={t('artifacts.noMatches')}
        hint={t('artifacts.noMatchesHint')}
        action={
          <Button variant="soft" color="gray" onClick={onClearFilters}>
            {t('artifacts.clearFilters')}
          </Button>
        }
      />
    ) : (
      <CenteredState
        icon={<EmptyIcon />}
        title={ownership === 'shared' ? t('artifacts.emptyShared') : t('artifacts.empty')}
        hint={ownership === 'shared' ? t('artifacts.emptySharedHint') : t('artifacts.emptyHint')}
        action={
          ownership === 'shared' ? undefined : (
            <Button asChild variant="soft">
              <Link href="/chat/">
                <MaterialIcon name="chat_bubble_outline" size={16} />
                {t('chat.startChat')}
              </Link>
            </Button>
          )
        }
      />
    ));
  }

  const view =
    viewMode === 'grid' ? (
      <ArtifactsGridView items={items} pagination={pagination} onPageChange={onPageChange} {...handlers} />
    ) : (
      <ArtifactsListView
        items={items}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={onSort}
        pagination={pagination}
        onPageChange={onPageChange}
        {...handlers}
      />
    );

  return region(view, isLoading);
}
