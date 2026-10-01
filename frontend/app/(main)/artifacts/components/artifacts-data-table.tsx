'use client';

import { Flex, Text } from '@radix-ui/themes';
import { EmptyIcon } from '@/app/components/ui';
import { useTranslation } from 'react-i18next';
import type { ArtifactListItem, ArtifactsSortField, ArtifactsViewMode } from '../types';
import { ArtifactsListView } from './artifacts-list-view';
import { ArtifactsGridView } from './artifacts-grid-view';

interface ArtifactsDataTableProps {
  items: ArtifactListItem[];
  viewMode: ArtifactsViewMode;
  isLoading: boolean;
  error: string | null;
  sortBy: ArtifactsSortField;
  sortOrder: 'asc' | 'desc';
  onSort: (field: ArtifactsSortField) => void;
  onPreview: (item: ArtifactListItem) => void;
  onDownload: (item: ArtifactListItem) => void;
  onOpenChat: (item: ArtifactListItem) => void;
  pagination: { page: number; limit: number; totalCount: number; totalPages: number };
  onPageChange: (page: number) => void;
}

export function ArtifactsDataTable({
  items,
  viewMode,
  isLoading,
  error,
  sortBy,
  sortOrder,
  onSort,
  onPreview,
  onDownload,
  onOpenChat,
  pagination,
  onPageChange,
}: ArtifactsDataTableProps) {
  const { t } = useTranslation();

  if (isLoading && items.length === 0) {
    return (
      <Flex align="center" justify="center" style={{ flex: 1 }}>
        <Text size="2" style={{ color: 'var(--slate-10)' }}>
          {t('action.loading')}
        </Text>
      </Flex>
    );
  }

  if (error) {
    return (
      <Flex align="center" justify="center" style={{ flex: 1 }}>
        <Text size="2" style={{ color: 'var(--red-11)' }}>{error}</Text>
      </Flex>
    );
  }

  if (!isLoading && items.length === 0) {
    return (
      <Flex direction="column" align="center" justify="center" gap="3" style={{ flex: 1 }}>
        <EmptyIcon />
        <Text size="3" weight="medium">{t('artifacts.empty', { defaultValue: 'No artifacts yet' })}</Text>
        <Text size="2" style={{ color: 'var(--slate-10)' }}>
          {t('artifacts.emptyHint', { defaultValue: 'Files your agents create in chat will show up here.' })}
        </Text>
      </Flex>
    );
  }

  if (viewMode === 'grid') {
    return (
      <ArtifactsGridView
        items={items}
        onPreview={onPreview}
        onDownload={onDownload}
        onOpenChat={onOpenChat}
        pagination={pagination}
        onPageChange={onPageChange}
      />
    );
  }

  return (
    <ArtifactsListView
      items={items}
      sortBy={sortBy}
      sortOrder={sortOrder}
      onSort={onSort}
      onPreview={onPreview}
      onDownload={onDownload}
      onOpenChat={onOpenChat}
      pagination={pagination}
      onPageChange={onPageChange}
    />
  );
}
