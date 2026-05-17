'use client';

import React, { useState, useEffect, useLayoutEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Badge, Blockquote } from '@radix-ui/themes';
import type { PreviewCitation } from './types';

interface CitationsPanelProps {
  citations: PreviewCitation[];
  /** Currently active / synced citation ID */
  activeCitationId?: string | null;
  /** Called when a citation card is clicked */
  onCitationClick?: (citation: PreviewCitation) => void;
}

/**
 * Right-side citations panel shown alongside the document preview.
 *
 * Each citation card displays:
 *   – Label ("Citation 1", "Citation 2", …)
 *   – Blockquote of the cited text
 *   – Page / Paragraph location badges
 *
 * Syncs with the PDF viewer:
 *   – Active citation auto-scrolls into view (delayed 350ms for smooth UX)
 *   – Clicking a card triggers `onCitationClick` (parent navigates PDF)
 *
 * Visible only when `citations` is non-empty; the parent layout
 * (fixed-width column) sizes this panel — it fills the column width.
 */
export function CitationsPanel({
  citations,
  activeCitationId,
  onCitationClick,
}: CitationsPanelProps) {
  const { t } = useTranslation();
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // Auto-scroll active citation into view (delayed for smoother UX)
  useEffect(() => {
    if (!activeCitationId) return;

    const timer = setTimeout(() => {
      const el = cardRefs.current.get(activeCitationId);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }, 350);

    return () => clearTimeout(timer);
  }, [activeCitationId]);

  if (citations.length === 0) return null;

  return (
    <Flex
      direction="column"
      style={{
        width: '100%',
        minWidth: 0,
        alignSelf: 'stretch',
        flexShrink: 0,
        height: '100%',
        overflow: 'hidden',
        background:
          'linear-gradient(180deg, var(--olive-2) 0%, var(--olive-1) 100%)',
      }}
    >
      {/* Header */}
      <Flex
        align="center"
        style={{
          height: '40px',
          padding: '0 var(--space-2)',
          backdropFilter: 'blur(8px)',
          backgroundColor: 'var(--color-panel-translucent)',
          borderBottom: '1px solid var(--olive-3)',
          flexShrink: 0,
        }}
      >
        <Text size="2" weight="medium">
          {t('chat.citations')}
        </Text>
      </Flex>

      {/* Citation cards */}
      <Flex
        direction="column"
        gap="2"
        className="file-preview-scroll-area"
        style={{
          flex: 1,
          overflow: 'auto',
          padding: 'var(--space-2)',
        }}
      >
        {citations.map((citation, index) => (
          <CitationCard
            key={citation.id}
            ref={(el: HTMLDivElement | null) => {
              if (el) cardRefs.current.set(citation.id, el);
              else cardRefs.current.delete(citation.id);
            }}
            citation={citation}
            index={index + 1}
            isActive={activeCitationId === citation.id}
            onClick={
              onCitationClick ? () => onCitationClick(citation) : undefined
            }
          />
        ))}
      </Flex>
    </Flex>
  );
}

// ── Individual citation card ────────────────────────────────────────────

/** Lines of citation text shown before "Show more" appears. */
const CITATION_CLAMP_LINES = 4;

export interface CitationCardProps {
  citation: PreviewCitation;
  /** 1-based display index */
  index: number;
  /** Whether this card is the active / synced citation */
  isActive?: boolean;
  /** Click handler */
  onClick?: () => void;
}

export const CitationCard = React.forwardRef<HTMLDivElement, CitationCardProps>(
  function CitationCard({ citation, index, isActive, onClick }, ref) {
    const { t } = useTranslation();
    const [isHovered, setIsHovered] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const [isTruncated, setIsTruncated] = useState(false);
    const blockquoteRef = useRef<HTMLQuoteElement>(null);

    const highlighted = isActive || isHovered;

    const hasLocationBadges =
      (citation.pageNumbers && citation.pageNumbers.length > 0) ||
      (citation.paragraphNumbers && citation.paragraphNumbers.length > 0);

    // Measure overflow and watch for container resizes so isTruncated stays
    // correct even when the sidebar is narrowed or the window is resized.
    // useLayoutEffect ensures the initial check runs before the first paint,
    // preventing a one-frame flash of the "Show more" button appearing late.
    useLayoutEffect(() => {
      if (expanded) return;
      const el = blockquoteRef.current;
      if (!el) return;

      const check = () => setIsTruncated(el.scrollHeight > el.clientHeight);
      check();

      const ro = new ResizeObserver(check);
      ro.observe(el);
      return () => ro.disconnect();
    }, [citation.content, expanded]);

    return (
      <Flex
        ref={ref}
        direction="column"
        gap="4"
        onClick={onClick}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        style={{
          backgroundColor: highlighted ? 'var(--olive-3)' : 'var(--olive-2)',
          border: `1px solid ${highlighted ? 'var(--olive-4)' : 'var(--olive-3)'}`,
          borderRadius: 'var(--radius-1)',
          padding: 'var(--space-4)',
          cursor: onClick ? 'pointer' : 'default',
          transition: 'background-color 0.15s ease, border-color 0.15s ease',
        }}
      >
        {/* Label */}
        <Text
          size="1"
          style={{
            color: 'var(--slate-a11)',
            lineHeight: 'var(--line-height-1)',
          }}
        >
          {t('filePreview.citationLabel', { index })}
        </Text>

        {/* Blockquote — clamped to CITATION_CLAMP_LINES when not expanded */}
        {citation.content && (
          <Flex direction="column" gap="1">
            <Blockquote
              ref={blockquoteRef as React.Ref<HTMLQuoteElement>}
              size="1"
              style={{
                borderLeftColor: isActive ? 'var(--accent-9)' : 'var(--accent-a6)',
                borderLeftWidth: '4px',
                color: 'var(--slate-12)',
                lineHeight: 'var(--line-height-1)',
                paddingLeft: 'var(--space-3)',
                margin: 0,
                ...(!expanded && {
                  overflow: 'hidden',
                  display: '-webkit-box',
                  WebkitBoxOrient: 'vertical',
                  WebkitLineClamp: CITATION_CLAMP_LINES,
                }),
              }}
            >
              {citation.content}
            </Blockquote>

            {(isTruncated || expanded) && (
              <Text
                as="span"
                size="1"
                role="button"
                tabIndex={0}
                aria-expanded={expanded}
                style={{
                  color: 'var(--accent-11)',
                  cursor: 'pointer',
                  userSelect: 'none',
                  alignSelf: 'flex-start',
                  paddingLeft: 'var(--space-3)',
                  marginTop: 'var(--space-1)',
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  setExpanded((prev) => !prev);
                }}
              >
                {expanded ? t('filePreview.showLess') : t('filePreview.showMore')}
              </Text>
            )}
          </Flex>
        )}

        {/* Location badges */}
        {hasLocationBadges && (
          <Flex gap="2" wrap="wrap">
            {citation.pageNumbers?.map((p) => (
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
            {citation.paragraphNumbers?.map((b) => (
              <Badge
                key={`para-${b}`}
                size="1"
                variant="soft"
                color="gray"
                style={{ fontWeight: 500 }}
              >
                {t('filePreview.paragraph', { number: b })}
              </Badge>
            ))}
          </Flex>
        )}
      </Flex>
    );
  },
);
