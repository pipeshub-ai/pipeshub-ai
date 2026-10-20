'use client';

import { useState, type KeyboardEvent } from 'react';
import { Flex, Box, Text, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { formatSize, formatDate } from '@/lib/utils/formatters';
import { useTranslation } from 'react-i18next';
import type { ArtifactListItem } from '../types';
import { ArtifactActionMenu, type ArtifactActionHandlers } from './artifact-actions';
import { ArtifactThumbnail } from './artifact-thumbnail';
import { PaginationFooter, type ArtifactsPagination } from './pagination-footer';

interface ArtifactsGridViewProps extends ArtifactActionHandlers {
  items: ArtifactListItem[];
  pagination: ArtifactsPagination;
  onPageChange: (page: number) => void;
}

const ellipsis = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } as const;

function GridCard({ item, handlers }: { item: ArtifactListItem; handlers: ArtifactActionHandlers }) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const typeLabel = t(`artifacts.types.${item.artifactType}`, { defaultValue: item.artifactType });

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      handlers.onPreview(item);
    }
  };

  return (
    <Flex
      direction="column"
      role="button"
      tabIndex={0}
      aria-label={item.name}
      onClick={() => handlers.onPreview(item)}
      onKeyDown={onKeyDown}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        position: 'relative',
        border: `1px solid ${isHovered ? 'var(--olive-6)' : 'var(--olive-4)'}`,
        borderRadius: 'var(--radius-3)',
        background: 'var(--color-panel-solid)',
        overflow: 'hidden',
        cursor: 'pointer',
        boxShadow: isHovered ? 'var(--shadow-3)' : 'none',
        transform: isHovered ? 'translateY(-1px)' : 'none',
        transition: 'box-shadow 150ms ease, border-color 150ms ease, transform 150ms ease',
      }}
    >
      <Box style={{ height: 132, background: 'var(--olive-2)', borderBottom: '1px solid var(--olive-3)' }}>
        <ArtifactThumbnail item={item} />
      </Box>
      <Box
        onClick={(e) => e.stopPropagation()}
        style={{
          position: 'absolute',
          top: 'var(--space-2)',
          right: 'var(--space-2)',
          background: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-2)',
          boxShadow: 'var(--shadow-1)',
          // Ghost buttons cancel their padding with a negative margin; without this the chip hugs the icon.
          padding: 'var(--space-1)',
          display: 'flex',
        }}
      >
        <ArtifactActionMenu item={item} handlers={handlers} t={t} />
      </Box>
      <Flex direction="column" gap="1" style={{ padding: 'var(--space-3)', minWidth: 0 }}>
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <Text size="2" weight="medium" style={{ ...ellipsis, color: 'var(--slate-12)' }} title={item.name}>
            {item.name}
          </Text>
          {item.version > 1 && (
            <Badge size="1" color="gray" variant="soft" style={{ flexShrink: 0 }}>
              v{item.version}
            </Badge>
          )}
        </Flex>
        <Text size="1" style={{ ...ellipsis, color: 'var(--slate-10)' }}>
          {[typeLabel, formatSize(item.sizeInBytes ?? undefined), item.createdAt ? formatDate(item.createdAt) : null]
            .filter(Boolean)
            .join(' · ')}
        </Text>
        {item.conversationTitle && (
          <Flex align="center" gap="1" style={{ minWidth: 0, color: 'var(--slate-9)' }}>
            <MaterialIcon name="chat_bubble_outline" size={12} />
            <Text size="1" style={ellipsis} title={item.conversationTitle}>
              {item.conversationTitle}
            </Text>
          </Flex>
        )}
      </Flex>
    </Flex>
  );
}

export function ArtifactsGridView({ items, pagination, onPageChange, ...handlers }: ArtifactsGridViewProps) {
  return (
    <Flex direction="column" style={{ flex: 1, minHeight: 0 }}>
      <Box
        style={{
          flex: 1,
          overflow: 'auto',
          padding: 'var(--space-4)',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
          // Cards clip their overflow, so auto rows would squeeze them to fit a short viewport.
          gridAutoRows: 'max-content',
          gap: 'var(--space-4)',
          alignContent: 'start',
        }}
      >
        {items.map((item) => (
          <GridCard key={item.artifactId} item={item} handlers={handlers} />
        ))}
      </Box>
      <PaginationFooter pagination={pagination} onPageChange={onPageChange} />
    </Flex>
  );
}
