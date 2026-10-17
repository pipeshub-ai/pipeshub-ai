'use client';

import { useState, type KeyboardEvent } from 'react';
import { Flex, Box, Text, Button, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui';
import { formatSize, formatDate } from '@/lib/utils/formatters';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useTranslation } from 'react-i18next';
import type { ArtifactListItem, ArtifactsSortField } from '../types';
import { ArtifactActionMenu, type ArtifactActionHandlers } from './artifact-actions';
import { PaginationFooter, type ArtifactsPagination } from './pagination-footer';

interface ArtifactsListViewProps extends ArtifactActionHandlers {
  items: ArtifactListItem[];
  sortBy: ArtifactsSortField;
  sortOrder: 'asc' | 'desc';
  onSort: (field: ArtifactsSortField) => void;
  pagination: ArtifactsPagination;
  onPageChange: (page: number) => void;
}

const COLUMN = { type: '140px', date: '140px', size: '90px', actions: '48px' };

function HeaderCell({
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
  const cellStyle = { width, flex: flex ? `${flex} 1 0` : undefined, flexShrink: width ? 0 : undefined, minWidth: 0, padding: '0 var(--space-2)', whiteSpace: 'nowrap' as const, boxSizing: 'border-box' as const, display: 'flex', alignItems: 'center' };
  const text = (
    <Text size="1" weight="medium" style={{ color: 'var(--slate-10)' }}>
      {label}
    </Text>
  );
  if (!field) {
    return (
      <Box style={cellStyle}>
        {text}
      </Box>
    );
  }
  const isActive = sortBy === field;
  return (
    <Box style={cellStyle}>
      <Button
        variant="ghost"
        size="1"
        color="gray"
        onClick={() => onSort(field)}
        style={{ margin: 0, padding: 0, gap: 2, height: 'auto' }}
      >
        {text}
        <MaterialIcon
          name={isActive ? (sortOrder === 'asc' ? 'arrow_drop_up' : 'arrow_drop_down') : 'unfold_more'}
          size={14}
          color={isActive ? 'var(--slate-12)' : 'var(--slate-8)'}
        />
      </Button>
    </Box>
  );
}

function ListRow({
  item,
  isMobile,
  handlers,
}: {
  item: ArtifactListItem;
  isMobile: boolean;
  handlers: ArtifactActionHandlers;
}) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const typeLabel = t(`artifacts.types.${item.artifactType}`, { defaultValue: item.artifactType });
  const chatTitle = item.conversationTitle || t('artifacts.untitledChat');
  // border-box: these are inline spans, and padding would otherwise widen the fixed columns past the header's.
  const muted = { color: 'var(--slate-10)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', boxSizing: 'border-box' } as const;

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      handlers.onPreview(item);
    }
  };

  return (
    <Flex
      align="center"
      role="button"
      tabIndex={0}
      aria-label={item.name}
      onClick={() => handlers.onPreview(item)}
      onKeyDown={onKeyDown}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        minHeight: isMobile ? 60 : 48,
        padding: '0 var(--space-4)',
        borderBottom: '1px solid var(--olive-3)',
        backgroundColor: isHovered ? 'var(--olive-2)' : 'transparent',
        cursor: 'pointer',
        transition: 'background-color 120ms ease',
      }}
    >
      <Flex align="center" gap="3" style={{ flex: '2 1 0', minWidth: 0, padding: '0 var(--space-2)' }}>
        <FileIcon filename={item.name} mimeType={item.mimeType || undefined} size={20} />
        <Flex direction="column" style={{ minWidth: 0 }}>
          <Flex align="center" gap="2" style={{ minWidth: 0 }}>
            <Text size="2" weight="medium" style={{ ...muted, color: 'var(--slate-12)' }}>
              {item.name}
            </Text>
            {item.version > 1 && (
              <Badge size="1" color="gray" variant="soft" style={{ flexShrink: 0 }}>
                v{item.version}
              </Badge>
            )}
          </Flex>
          {isMobile && (
            <Text size="1" style={muted}>
              {[typeLabel, formatSize(item.sizeInBytes ?? undefined), chatTitle].join(' · ')}
            </Text>
          )}
        </Flex>
      </Flex>
      {!isMobile && (
        <>
          <Text size="2" style={{ ...muted, width: COLUMN.type, flexShrink: 0, padding: '0 var(--space-2)' }}>
            {typeLabel}
          </Text>
          <Text size="2" style={{ ...muted, flex: '1 1 0', minWidth: 0, padding: '0 var(--space-2)' }} title={chatTitle}>
            {chatTitle}
          </Text>
          <Text size="2" style={{ ...muted, width: COLUMN.date, flexShrink: 0, padding: '0 var(--space-2)' }}>
            {item.createdAt ? formatDate(item.createdAt) : '—'}
          </Text>
          <Text size="2" style={{ ...muted, width: COLUMN.size, flexShrink: 0, padding: '0 var(--space-2)' }}>
            {formatSize(item.sizeInBytes ?? undefined)}
          </Text>
        </>
      )}
      <Flex justify="end" style={{ width: COLUMN.actions, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
        <ArtifactActionMenu item={item} handlers={handlers} t={t} />
      </Flex>
    </Flex>
  );
}

export function ArtifactsListView({
  items,
  sortBy,
  sortOrder,
  onSort,
  pagination,
  onPageChange,
  ...handlers
}: ArtifactsListViewProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const sortProps = { sortBy, sortOrder, onSort };

  return (
    <Flex direction="column" style={{ flex: 1, minHeight: 0 }}>
      <Flex
        align="center"
        style={{
          height: 36,
          padding: '0 var(--space-4)',
          borderBottom: '1px solid var(--olive-3)',
          background: 'var(--olive-2)',
          flexShrink: 0,
        }}
      >
        <HeaderCell label={t('artifacts.name')} field="name" flex={2} {...sortProps} />
        {!isMobile && (
          <>
            <HeaderCell label={t('filter.type')} field="artifactType" width={COLUMN.type} {...sortProps} />
            <HeaderCell label={t('artifacts.conversation')} flex={1} {...sortProps} />
            <HeaderCell label={t('filter.dateCreated')} field="createdAtTimestamp" width={COLUMN.date} {...sortProps} />
            <HeaderCell label={t('artifacts.size')} width={COLUMN.size} {...sortProps} />
          </>
        )}
        <Box style={{ width: COLUMN.actions, flexShrink: 0 }} />
      </Flex>
      <Box style={{ flex: 1, overflow: 'auto' }}>
        {items.map((item) => (
          <ListRow key={item.artifactId} item={item} isMobile={isMobile} handlers={handlers} />
        ))}
      </Box>
      <PaginationFooter pagination={pagination} onPageChange={onPageChange} />
    </Flex>
  );
}
