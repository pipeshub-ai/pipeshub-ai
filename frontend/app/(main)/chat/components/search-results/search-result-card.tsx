'use client';

import React from 'react';
import { Flex, Box, Text, Badge } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { isLocalFsConnectorType } from '@/app/(main)/workspace/connectors/utils/local-fs-helpers';
import { getConnectorConfig } from '../message-area/response-tabs/citations/utils';
import type { SearchResultItem } from '@/chat/types';

interface SearchResultCardProps {
  result: SearchResultItem;
  onOpenSource: (result: SearchResultItem) => void;
  onChat: (result: SearchResultItem) => void;
}

export function SearchResultCard({
  result,
  onOpenSource,
  onChat,
}: SearchResultCardProps) {
  const { metadata, content, score } = result;
  const config = getConnectorConfig(metadata.connector);

  const isCollectionSource = metadata.origin === 'UPLOAD';
  const isLocalFsSource = isLocalFsConnectorType(metadata.connector ?? '');
  const openInLabel = isCollectionSource
    ? 'Open in Collections'
    : `Open ${config.label}`;

  const pageNums = metadata.pageNum?.filter((p): p is number => p !== null) ?? [];
  const blockNums = metadata.blockNum?.filter((b): b is number => b !== null) ?? [];
  const hasLocationBadges = pageNums.length > 0 || blockNums.length > 0;

  const handleOpenSource = () => {
    if (!metadata.hideWeburl && metadata.webUrl) {
      window.open(metadata.webUrl, '_blank', 'noopener,noreferrer');
    }
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
            {/* "Open [Source]" outline button — hidden for Local FS (no shareable web URL). */}
            {metadata.webUrl && !metadata.hideWeburl && !isLocalFsSource && (
              <Box
                asChild
                onClick={handleOpenSource}
                style={{
                  height: '24px',
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

            {/* "Chat" solid accent button */}
            <Box
              asChild
              onClick={() => onChat(result)}
              style={{
                height: '24px',
                padding: '0 var(--space-2)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: 'var(--accent-9)',
                borderRadius: 'var(--radius-1)',
                cursor: 'pointer',
                border: 'none',
                transition: 'background-color 0.15s ease',
              }}
              onMouseEnter={(e: React.MouseEvent<HTMLElement>) => {
                (e.currentTarget as HTMLElement).style.backgroundColor =
                  'var(--accent-10)';
              }}
              onMouseLeave={(e: React.MouseEvent<HTMLElement>) => {
                (e.currentTarget as HTMLElement).style.backgroundColor =
                  'var(--accent-9)';
              }}
            >
              <button type="button">
                <Text
                  size="1"
                  weight="medium"
                  style={{ color: 'var(--accent-contrast)', whiteSpace: 'nowrap' }}
                >
                  Chat
                </Text>
              </button>
            </Box>
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
          Relevance: {Math.round(score * 100)}%
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
                Page {p}
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
                Paragraph {b}
              </Badge>
            ))}
          </>
        )}
      </Flex>
    </Flex>
  );
}
