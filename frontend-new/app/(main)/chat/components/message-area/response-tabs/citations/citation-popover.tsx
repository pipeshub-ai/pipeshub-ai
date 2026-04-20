'use client';

import React from 'react';
import { Flex, Box, Text, Badge } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { getConnectorConfig } from './utils';
import { FileIcon } from '@/app/components/ui/file-icon';
import type { CitationData } from './types';

interface CitationPopoverContentProps {
  citation: CitationData;
  onPreview?: (citation: CitationData) => void;
  onOpenInCollection?: (citation: CitationData) => void;
}

/**
 * Expanded citation preview shown inside a HoverCard.
 */
export function CitationPopoverContent({
  citation,
  onPreview,
  onOpenInCollection,
}: CitationPopoverContentProps) {
  const config = getConnectorConfig(citation.connector);

  // Determine if this is a collection (UPLOAD) or external connector source
  const isCollectionSource = citation.origin === 'UPLOAD';
  const openInLabel = isCollectionSource ? 'Open in Collections' : `Open in ${config.label}`;

  const handleOpenInSource = () => {
    if (isCollectionSource) {
      // Navigate to collections page
      onOpenInCollection?.(citation);
    } else {
      // Open external connector URL
      if (citation.webUrl && !citation.hideWeburl) {
        window.open(citation.webUrl, '_blank', 'noopener,noreferrer');
      }
    }
  };

  const handlePreview = () => {
    if (onPreview) {
      onPreview(citation);
    }
  };

  return (
    <Flex direction="column" gap="4">
      {/* ── Header: source + action buttons ── */}
      <Flex align="center" justify="between">
        <Flex align="center" gap="2">
          <ConnectorIcon type={citation.connector} size={20} />
          <Text
            size="1"
            style={{
              color: 'var(--slate-a11)',
              lineHeight: 'var(--line-height-1)',
            }}
          >
            {config.label}
          </Text>
        </Flex>

        <Flex align="center" gap="2">
          <Box
            asChild
            onClick={handleOpenInSource}
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
              (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--slate-a3)';
            }}
            onMouseLeave={(e: React.MouseEvent<HTMLElement>) => {
              (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent';
            }}
          >
            <button tabIndex={-1}>
              <Text
                size="1"
                weight="medium"
                style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}
              >
                {openInLabel}
              </Text>
            </button>
          </Box>

          <Box
            asChild
            onClick={handlePreview}
            style={{
              height: '24px',
              padding: '0 var(--space-2)',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: 'var(--emerald-9)',
              borderRadius: 'var(--radius-1)',
              cursor: 'pointer',
              border: 'none',
              transition: 'background-color 0.15s ease',
            }}
            onMouseEnter={(e: React.MouseEvent<HTMLElement>) => {
              (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent-10)';
            }}
            onMouseLeave={(e: React.MouseEvent<HTMLElement>) => {
              (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent-9)';
            }}
          >
            <button tabIndex={-1}>
              <Text
                size="1"
                weight="medium"
                style={{ color: 'var(--slate-12)', whiteSpace: 'nowrap' }}
              >
                Preview
              </Text>
            </button>
          </Box>
        </Flex>
      </Flex>

      {/* ── Record info + cited content ── */}
      <Flex direction="column" gap="1">
        {/* Record name with file icon */}
        <Flex align="start" gap="2">
          <FileIcon extension={citation.extension} size={16} />
          <Text
            size="2"
            weight="medium"
            style={{
              color: 'var(--slate-12)',
              lineHeight: 'var(--line-height-2)',
              wordBreak: 'break-word',
            }}
          >
            {citation.recordName}
          </Text>
        </Flex>

        {/* Cited text as blockquote */}
        {citation.content && (
          <Box
            style={{
              borderLeft: '4px solid var(--accent-a6)',
              paddingLeft: 'var(--space-3)',
              marginTop: 'var(--space-1)',
            }}
          >
            <Text
              size="1"
              style={{
                color: 'var(--slate-12)',
                lineHeight: 'var(--line-height-1)',
                display: '-webkit-box',
                WebkitLineClamp: 4,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {citation.content}
            </Text>
          </Box>
        )}
      </Flex>

      {/* ── Location badges (page / paragraph) ── */}
      {(citation.pageNum?.length || citation.blockNum?.length) ? (
        <Flex gap="2" wrap="wrap">
          {citation.pageNum?.map((p) => (
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
          {citation.blockNum?.map((b) => (
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
        </Flex>
      ) : null}
    </Flex>
  );
}
