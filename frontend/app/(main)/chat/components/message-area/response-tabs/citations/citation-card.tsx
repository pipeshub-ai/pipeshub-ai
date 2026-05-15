'use client';

import React from 'react';
import { Flex, Box, Text, Badge, Button } from '@radix-ui/themes';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { getConnectorConfig, formatSyncLabel } from './utils';
import { FileIcon } from '@/app/components/ui/file-icon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { ResponseTab } from '@/chat/types';
import type { CitationData, CitationCallbacks } from './types';

// ---------------------------------------------------------------------------
// ReferenceCard — single card used in both Sources and Citations tabs.
//
// Structure:
//   ┌─ HEADER ──────────────────────────────────────────────────────────────┐
//   │  [connector icon + label]          [sync badge?] [Open in…] [Preview]│
//   ├─ BODY ────────────────────────────────────────────────────────────────┤
//   │  [file icon + record name]                                           │
//   │  │ blockquote of cited text                                          │
//   │  [AI reason?]                                                        │
//   ├─ FOOTER ──────────────────────────────────────────────────────────────┤
//   │  [citation count badge?]  or  [page/paragraph badges?]               │
//   └──────────────────────────────────────────────────────────────────────-┘
//
// Elements marked with "?" are conditionally rendered based on `currentTab`.
// ---------------------------------------------------------------------------

interface ReferenceCardProps {
  citation: CitationData;
  callbacks?: CitationCallbacks;
  /** Which tab this card is being rendered in */
  currentTab: ResponseTab;
  /** Number of citations referencing this source (Sources tab footer) */
  citationCount?: number;
  /** AI-generated reason for this citation (Citations tab body) */
  reason?: string;
}

export function ReferenceCard({
  citation,
  callbacks,
  currentTab,
  citationCount,
  reason,
}: ReferenceCardProps) {
  const isSourcesTab = currentTab === 'sources';
  const isCitationsTab = currentTab === 'citation';
  const isMobile = useIsMobile();

  const config = getConnectorConfig(citation.connector);

  // Determine if this is a collection (UPLOAD) or external connector source
  const isCollectionSource = citation.origin === 'UPLOAD';
  const openInLabel = isCollectionSource ? 'Open in Collections' : `Open in ${config.label}`;

  // Chat attachments are stored with connector === "ATTACHMENTS". They live in
  // the user's chat session only and have no Collections page to navigate to,
  // so the "Open in Collections" button should not be shown for them.
  const isAttachment = citation.connector?.toUpperCase() === 'ATTACHMENTS';

  // ── handlers ──────────────────────────────────────────────────────────
  const handleOpenInSource = () => {
    if (isCollectionSource) {
      // Navigate to collections page
      callbacks?.onOpenInCollection?.(citation);
    } else {
      // Open external connector URL
      if (citation.webUrl && !citation.hideWeburl) {
        window.open(citation.webUrl, '_blank', 'noopener,noreferrer');
      }
    }
  };

  const handlePreview = () => {
    if (callbacks?.onPreview) {
      callbacks.onPreview(citation);
    }
  };

  // ── derived values ────────────────────────────────────────────────────
  const showPreview =
    citation.recordType?.toUpperCase() === 'FILE' &&
    citation.connector?.toUpperCase() !== 'WEB';
  const syncLabel = isSourcesTab ? formatSyncLabel(citation.updatedAt) : undefined;
  const hasLocationBadges =
    isCitationsTab && (citation.pageNum?.length || citation.blockNum?.length);

  // ── render ────────────────────────────────────────────────────────────
  return (
    <Flex
      direction="column"
      style={{
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
        gap: 'var(--space-6)',
      }}
    >
      {/* ── HEADER + BODY ───────────────────────────────────────────── */}
      <Flex direction="column" gap="4">
        {/* HEADER — connector icon/label, sync badge, action buttons */}
        <Flex align="center" justify="between">
          {/* Left: connector icon + label */}
          <Flex align="center" gap="2">
            <ConnectorIcon type={citation.connector} size={18} />
            <Text
              size="2"
              style={{
                color: 'var(--slate-a11)',
                lineHeight: 'var(--line-height-2)',
              }}
            >
              {config.label}
            </Text>
          </Flex>

          {/* Right: sync badge (always) + action buttons (desktop only) */}
          <Flex align="center" gap="2">
            {/* Sync badge — sources tab only */}
            {syncLabel && (
              <Badge
                size="2"
                variant="soft"
                style={{ fontWeight: 500, backgroundColor: 'var(--slate-a3)', color: 'var(--slate-a11)' }}
              >
                {syncLabel}
              </Badge>
            )}

            {/* Action buttons in header — desktop only; on mobile they move to the footer */}
            {!isMobile && (
              <>
                {/* "Open in {Source}" outline button — hidden for chat attachments */}
                {!citation.hideWeburl && !isAttachment &&
                  (<Button
                    size="1"
                    variant="outline"
                    color="gray"
                    onClick={handleOpenInSource}
                    style={{ cursor: 'pointer', whiteSpace: 'nowrap', border: `0px solid var(--slate-a7)`, color: 'var(--slate-11)' }}
                  >
                    {openInLabel}
                  </Button>)}

                
                {/* "Preview" solid accent button — only for file records, not WEB */}
                {showPreview && (
                  <Button
                  size="1"
                  variant="solid"
                  onClick={handlePreview}
                  style={{ cursor: 'pointer' }}
                >
                  Preview
                </Button>
                )}
              </>
            )}
          </Flex>
        </Flex>

        {/* BODY — file icon + record name, blockquote */}
        <Flex direction="column" gap={'2'}>
          {/* Record name with file icon */}
          <Flex align="center" gap="2">
            <FileIcon extension={citation.extension} size={16} />
            <Text
              size="3"
              weight="medium"
              style={{
                color: 'var(--slate-12)',
                lineHeight: 'var(--line-height-3)',
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
                {citation.content}
              </Text>
            </Box>
          )}
        </Flex>
      </Flex>

      {/* AI reason — citations tab only */}
      {isCitationsTab && reason && (
        <Flex align="center" gap="1" style={{ width: '100%' }}>
          <ChatStarIcon
            size={16}
            color="var(--accent-11)"
          />
          <Text
            size="2"
            style={{
              flex: 1,
              background:
                'linear-gradient(to right, var(--accent-12), var(--accent-7))',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
              lineHeight: 'var(--line-height-2)',
            }}
          >
            &ldquo;{reason}&rdquo;
          </Text>
        </Flex>
      )}

      {/* ── FOOTER ──────────────────────────────────────────────────── */}

      {/* On mobile: single row with badges on left, action buttons on right.
          On desktop: badges in their own rows, action buttons stay in the header. */}
      {(isSourcesTab || hasLocationBadges || isMobile) && (
        <Flex align="center" justify="between" gap="2">
          {/* Left: citation count badge (sources) or location badges (citations) */}
          <Flex gap="2" wrap="wrap" align="center">
            {isSourcesTab && citationCount != null && citationCount > 0 && (
              <Badge
                size="1"
                variant="soft"
                style={{
                  background: 'var(--sky-a3)',
                  color: 'var(--sky-a11)',
                  fontWeight: 500,
                  borderRadius: 'var(--radius-2)',
                }}
              >
                {citationCount} {citationCount === 1 ? 'Citation' : 'Citations'}
              </Badge>
            )}
            {hasLocationBadges && (
              <>
                {citation.pageNum?.map((p) => (
                  <Badge
                    key={`page-${p}`}
                    size="2"
                    variant="soft"
                    color="gray"
                    style={{ fontWeight: 500, backgroundColor: 'var(--slate-a3)', color: 'var(--slate-a11)' }}
                  >
                    Page {p}
                  </Badge>
                ))}
                {citation.blockNum?.map((b) => (
                  <Badge
                    key={`block-${b}`}
                    size="2"
                    variant="soft"
                    color="gray"
                    style={{ fontWeight: 500, backgroundColor: 'var(--slate-a3)', color: 'var(--slate-a11)' }}
                  >
                    Paragraph {b}
                  </Badge>
                ))}
              </>
            )}
          </Flex>

          {/* Right: action buttons — mobile only (desktop has them in the header) */}
          {isMobile && (
            <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
              {/* "Open in {Source}" outline button — hidden for chat attachments */}
              {!citation.hideWeburl && !isAttachment && (<Button
                size="1"
                variant="outline"
                color="gray"
                onClick={handleOpenInSource}
                style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}
              >
                {openInLabel}
              </Button>)}

              {/* "Preview" button — only for previewable files */}
              {showPreview && (
                <Button
                  size="1"
                  variant="solid"
                  onClick={handlePreview}
                  style={{ cursor: 'pointer' }}
                >
                  Preview
                </Button>
              )}
            </Flex>
          )}
        </Flex>
      )}
    </Flex>
  );
}
