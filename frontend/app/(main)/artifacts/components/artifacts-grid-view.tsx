'use client';

import type { KeyboardEvent } from 'react';
import { Flex, Box, Text, IconButton, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui';
import { formatSize } from '@/lib/utils/formatters';
import { useTranslation } from 'react-i18next';
import type { ArtifactListItem } from '../types';

interface ArtifactsGridViewProps {
  items: ArtifactListItem[];
  onPreview: (item: ArtifactListItem) => void;
  onDownload: (item: ArtifactListItem) => void;
  onOpenChat: (item: ArtifactListItem) => void;
  pagination: { page: number; limit: number; totalCount: number; totalPages: number };
  onPageChange: (page: number) => void;
}

export function ArtifactsGridView({
  items,
  onPreview,
  onDownload,
  onOpenChat,
  pagination,
  onPageChange,
}: ArtifactsGridViewProps) {
  const { t } = useTranslation();
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
      <Box
        style={{
          flex: 1,
          overflow: 'auto',
          padding: 'var(--space-4)',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
          gap: 'var(--space-3)',
          alignContent: 'start',
        }}
      >
        {items.map((item) => (
          <Flex
            key={item.artifactId}
            direction="column"
            gap="2"
            role="button"
            tabIndex={0}
            aria-label={item.name}
            onClick={() => onPreview(item)}
            onKeyDown={activatePreview(item)}
            style={{
              padding: 'var(--space-3)',
              border: '1px solid var(--olive-4)',
              borderRadius: 'var(--radius-2)',
              background: 'var(--slate-1)',
              cursor: 'pointer',
              minHeight: 140,
            }}
          >
            <Flex justify="between" align="start">
              <FileIcon filename={item.name} mimeType={item.mimeType || undefined} size={28} />
              <Flex gap="1" onClick={(e) => e.stopPropagation()}>
                <IconButton
                  variant="ghost"
                  size="1"
                  color="gray"
                  aria-label={t('action.download')}
                  onClick={() => onDownload(item)}
                >
                  <MaterialIcon name="download" size={16} />
                </IconButton>
                {item.conversationId && (
                  <IconButton
                    variant="ghost"
                    size="1"
                    color="gray"
                    aria-label={t('artifacts.openInChat', { defaultValue: 'Open in chat' })}
                    onClick={() => onOpenChat(item)}
                  >
                    <MaterialIcon name="chat" size={16} />
                  </IconButton>
                )}
              </Flex>
            </Flex>
            <Text size="2" weight="medium" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.name}
            </Text>
            <Text size="1" style={{ color: 'var(--slate-10)' }}>
              {item.artifactType} · {formatSize(item.sizeInBytes ?? undefined)}
            </Text>
          </Flex>
        ))}
      </Box>
      <Flex
        justify="end"
        align="center"
        gap="3"
        style={{
          padding: 'var(--space-2) var(--space-4)',
          borderTop: '1px solid var(--olive-3)',
          background: 'var(--olive-2)',
          flexShrink: 0,
        }}
      >
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
        <Text size="2">{pagination.page}</Text>
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
  );
}
