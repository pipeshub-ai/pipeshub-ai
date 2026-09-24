'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Badge, Button } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { isLocalFsConnectorType } from '@/app/(main)/workspace/connectors/utils/local-fs-helpers';
import { openRecordSource } from '@/chat/utils/open-record-source';
import { getConnectorConfig } from '../message-area/response-tabs/citations/utils';
import type { SearchResultItem } from '@/chat/types';

interface SearchResultCardProps {
  result: SearchResultItem;
  onOpenSource: (result: SearchResultItem) => void;
  onPreview: (result: SearchResultItem) => void;
}

export function SearchResultCard({
  result,
  onOpenSource,
  onPreview,
}: SearchResultCardProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const { metadata, content, score } = result;
  const config = getConnectorConfig(metadata.connector);

  const isCollectionSource = metadata.origin === 'UPLOAD';
  const isLocalFsSource = isLocalFsConnectorType(metadata.connector ?? '');
  let openInLabel = t('chat.recordActions.open', { source: config.label });
  if (isLocalFsSource) openInLabel = t('chat.recordActions.openIn', { source: config.label });
  if (isCollectionSource) openInLabel = t('chat.recordActions.openInCollections');

  // 44px minimum touch target on mobile (frontend/CLAUDE.md). Both header
  // actions share the value so they stay the same height side by side.
  const actionHeight = isMobile ? '44px' : '24px';

  const pageNums = metadata.pageNum?.filter((p): p is number => p !== null) ?? [];
  const blockNums = metadata.blockNum?.filter((b): b is number => b !== null) ?? [];
  const hasLocationBadges = pageNums.length > 0 || blockNums.length > 0;
  const canOpenSource =
    isLocalFsSource ||
    (!metadata.hideWeburl && !!metadata.webUrl);
  // Same gate as the Chat citation card (citation-card.tsx) — only files (not web
  // links) can be streamed into the preview panel.
  const showPreview =
    metadata.recordType?.toUpperCase() === 'FILE' &&
    metadata.connector?.toUpperCase() !== 'WEB';

  const handleOpenSource = async () => {
    await openRecordSource({
      recordId: metadata.recordId,
      connector: metadata.connector,
      origin: metadata.origin,
      webUrl: metadata.webUrl,
      hideWeburl: metadata.hideWeburl,
    });
    onOpenSource(result);
  };

  return (
    <Flex
      direction="column"
      style={{
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
        gap: 'var(--space-4)',
      }}
    >
      {/* ── HEADER — record name + action buttons ──────────────────── */}
      <Flex direction="column" gap="1">
        <Flex align="center" justify="between">
          {/* Left: connector icon + record name */}
          <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
            <ConnectorIcon type={metadata.connector} size={16} />
            <Text
              size="2"
              style={{
                color: 'var(--slate-a11)',
                lineHeight: 'var(--line-height-2)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                flex: 1,
              }}
            >
              {metadata.recordName}
            </Text>
          </Flex>

          {/* Right: action buttons */}
          <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
            {/* "Open [Source]" outline button. Local FS uses a native desktop reveal when available. */}
            {canOpenSource && (
              <Box
                asChild
                onClick={handleOpenSource}
                style={{
                  height: actionHeight,
                  padding: '0 var(--space-2)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  border: '1px solid var(--slate-a7)',
                  borderRadius: 'var(--radius-1)',
                  cursor: 'pointer',
                  backgroundColor: 'transparent',
                  transition: 'background-color 0.15s ease',
                }}
                onMouseEnter={(e: React.MouseEvent<HTMLElement>) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor =
                    'var(--slate-a3)';
                }}
                onMouseLeave={(e: React.MouseEvent<HTMLElement>) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor =
                    'transparent';
                }}
              >
                <button type="button">
                  <Text
                    size="1"
                    weight="medium"
                    style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}
                  >
                    {openInLabel}
                  </Text>
                </button>
              </Box>
            )}

            {/* "Preview" button — only for previewable files, same as citation-card.tsx */}
            {showPreview && (
              <Button
                size="1"
                variant="solid"
                onClick={() => onPreview(result)}
                style={{ height: actionHeight, cursor: 'pointer' }}
              >
                {t('chat.recordActions.preview')}
              </Button>
            )}
          </Flex>
        </Flex>

        {/* Metadata row: connector name */}
        <Flex align="center" gap="2" style={{ height: '24px' }}>
          <Text
            size="2"
            weight="medium"
            style={{ color: 'var(--slate-10)' }}
          >
            {config.label}
          </Text>
        </Flex>
      </Flex>

      {/* ── BODY — blockquote of content ───────────────────────────── */}
      {content && (
        <Box
          style={{
            borderLeft: '4px solid var(--accent-a6)',
            paddingLeft: 'var(--space-3)',
          }}
        >
          <Text
            size="2"
            style={{
              color: 'var(--slate-12)',
              lineHeight: 'var(--line-height-2)',
              display: '-webkit-box',
              WebkitLineClamp: 4,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {content}
          </Text>
        </Box>
      )}

      {/* ── FOOTER — relevance score + location badges ─────────────── */}
      <Flex gap="2" wrap="wrap">
        {/* Relevance score badge */}
        <Badge
          size="1"
          variant="soft"
          style={{
            background: 'var(--accent-a3)',
            color: 'var(--accent-a11)',
            fontWeight: 500,
            borderRadius: 'var(--radius-2)',
          }}
        >
          {t('chat.recordActions.relevance', { percent: Math.round(score * 100) })}
        </Badge>

        {/* Page / paragraph location badges */}
        {hasLocationBadges && (
          <>
            {pageNums.map((p) => (
              <Badge
                key={`page-${p}`}
                size="1"
                variant="soft"
                color="gray"
                style={{ fontWeight: 500 }}
              >
                {t('filePreview.page', { number: p })}
              </Badge>
            ))}
            {blockNums.map((b) => (
              <Badge
                key={`block-${b}`}
                size="1"
                variant="soft"
                color="gray"
                style={{ fontWeight: 500 }}
              >
                {t('filePreview.paragraph', { number: b })}
              </Badge>
            ))}
          </>
        )}
      </Flex>
    </Flex>
  );
}
