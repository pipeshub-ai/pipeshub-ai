'use client';

import type { KeyboardEvent } from 'react';
import { Flex, Box, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui';
import { formatSize, formatDate } from '@/lib/utils/formatters';
import { useTranslation } from 'react-i18next';
import type { ArtifactListItem, ArtifactsSortField } from '../types';

interface ArtifactsListViewProps {
  items: ArtifactListItem[];
  sortBy: ArtifactsSortField;
  sortOrder: 'asc' | 'desc';
  onSort: (field: ArtifactsSortField) => void;
  onPreview: (item: ArtifactListItem) => void;
  onDownload: (item: ArtifactListItem) => void;
  onOpenChat: (item: ArtifactListItem) => void;
  pagination: { page: number; limit: number; totalCount: number; totalPages: number };
  onPageChange: (page: number) => void;
}

function SortHeader({
  label,
  field,
  sortBy,
  sortOrder,
  onSort,
  flex,
  width,
}: {
  label: string;
  field?: ArtifactsSortField;
  sortBy: ArtifactsSortField;
  sortOrder: 'asc' | 'desc';
  onSort: (field: ArtifactsSortField) => void;
  flex?: number;
  width?: string;
}) {
  const isActive = !!field && sortBy === field;
  return (
    <Button
      variant="ghost"
      size="1"
      color="gray"
      disabled={!field}
      onClick={() => field && onSort(field)}
      style={{
        width,
        flex,
        justifyContent: 'flex-start',
        padding: '0 var(--space-2)',
        backgroundColor: 'transparent',
        cursor: field ? 'pointer' : 'default',
      }}
    >
      <Text size="1" weight="medium" style={{ color: 'var(--slate-9)' }}>
        {label}
      </Text>
      {field && (
        <MaterialIcon
          name={isActive ? (sortOrder === 'asc' ? 'arrow_drop_up' : 'arrow_drop_down') : 'unfold_more'}
          size={14}
        />
      )}
    </Button>
  );
}

export function ArtifactsListView({
  items,
  sortBy,
  sortOrder,
  onSort,
  onPreview,
  onDownload,
  onOpenChat,
  pagination,
  onPageChange,
}: ArtifactsListViewProps) {
  const { t } = useTranslation();
  const start = pagination.totalCount === 0 ? 0 : (pagination.page - 1) * pagination.limit + 1;
  const end = Math.min(pagination.page * pagination.limit, pagination.totalCount);
  const hasPrev = pagination.page > 1;
  const hasNext = pagination.page < pagination.totalPages;

  const activatePreview = (item: ArtifactListItem) => (event: KeyboardEvent) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onPreview(item);
    }
  };

  return (
    <Flex direction="column" style={{ flex: 1, minHeight: 0 }}>
      <Flex
        align="center"
        style={{
          padding: 'var(--space-2) var(--space-4)',
          borderBottom: '1px solid var(--olive-3)',
          flexShrink: 0,
        }}
      >
        <SortHeader label={t('artifacts.name', { defaultValue: 'Name' })} field="name" sortBy={sortBy} sortOrder={sortOrder} onSort={onSort} flex={2} />
        <SortHeader label={t('filter.type')} field="artifactType" sortBy={sortBy} sortOrder={sortOrder} onSort={onSort} width="140px" />
        <SortHeader label={t('artifacts.conversation', { defaultValue: 'Conversation' })} sortBy={sortBy} sortOrder={sortOrder} onSort={onSort} flex={1} />
        <SortHeader label={t('filter.dateCreated')} field="createdAtTimestamp" sortBy={sortBy} sortOrder={sortOrder} onSort={onSort} width="140px" />
        <SortHeader label={t('artifacts.size', { defaultValue: 'Size' })} sortBy={sortBy} sortOrder={sortOrder} onSort={onSort} width="90px" />
        <Box style={{ width: '88px' }} />
      </Flex>
      <Box style={{ flex: 1, overflow: 'auto' }}>
        {items.map((item) => (
          <Flex
            key={item.artifactId}
            align="center"
            role="button"
            tabIndex={0}
            aria-label={item.name}
            style={{
              padding: 'var(--space-2) var(--space-4)',
              borderBottom: '1px solid var(--olive-3)',
              cursor: 'pointer',
            }}
            onClick={() => onPreview(item)}
            onKeyDown={activatePreview(item)}
          >
            <Flex align="center" gap="2" style={{ flex: 2, minWidth: 0, paddingRight: 'var(--space-2)' }}>
              <FileIcon filename={item.name} mimeType={item.mimeType || undefined} size={18} />
              <Text size="2" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.name}
              </Text>
            </Flex>
            <Text size="2" style={{ width: '140px', color: 'var(--slate-11)' }}>
              {item.artifactType}
            </Text>
            <Text size="2" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--slate-11)' }}>
              {item.conversationTitle || t('artifacts.untitledChat', { defaultValue: '—' })}
            </Text>
            <Text size="2" style={{ width: '140px', color: 'var(--slate-11)' }}>
              {item.createdAt ? formatDate(item.createdAt) : '—'}
            </Text>
            <Text size="2" style={{ width: '90px', color: 'var(--slate-11)' }}>
              {formatSize(item.sizeInBytes ?? undefined)}
            </Text>
            <Flex gap="1" style={{ width: '88px' }} onClick={(e) => e.stopPropagation()}>
              <Button variant="ghost" size="1" color="gray" onClick={() => onDownload(item)} aria-label={t('action.download')}>
                <MaterialIcon name="download" size={16} />
              </Button>
              {item.conversationId && (
                <Button variant="ghost" size="1" color="gray" onClick={() => onOpenChat(item)} aria-label={t('artifacts.openInChat', { defaultValue: 'Open in chat' })}>
                  <MaterialIcon name="chat" size={16} />
                </Button>
              )}
            </Flex>
          </Flex>
        ))}
      </Box>
      <Flex
        justify="between"
        align="center"
        style={{
          padding: 'var(--space-2) var(--space-4)',
          borderTop: '1px solid var(--olive-3)',
          background: 'var(--olive-2)',
          flexShrink: 0,
        }}
      >
        <Text size="2" style={{ color: 'var(--slate-9)' }}>
          {t('artifacts.showing', {
            defaultValue: 'Showing {{start}}-{{end}} of {{total}}',
            start,
            end,
            total: pagination.totalCount,
          })}
        </Text>
        <Flex gap="3" align="center">
          <Button
            variant="ghost"
            size="1"
            color="gray"
            disabled={!hasPrev}
            onClick={() => onPageChange(pagination.page - 1)}
          >
            <MaterialIcon name="chevron_left" size={16} />
            {t('common.previous')}
          </Button>
          <Box style={{ padding: 'var(--space-1) var(--space-3)', backgroundColor: 'var(--slate-3)', borderRadius: 'var(--radius-2)' }}>
            <Text size="2" weight="medium">{pagination.page}</Text>
          </Box>
          <Button
            variant="ghost"
            size="1"
            color="gray"
            disabled={!hasNext}
            onClick={() => onPageChange(pagination.page + 1)}
          >
            {t('common.next')}
            <MaterialIcon name="chevron_right" size={16} />
          </Button>
        </Flex>
      </Flex>
    </Flex>
  );
}
