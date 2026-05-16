'use client';

import React, { useState, useEffect, useRef } from 'react';
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
          Citations
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
    const [isHovered, setIsHovered] = useState(false);
    const highlighted = isActive || isHovered;

    const hasLocationBadges =
      (citation.pageNumbers && citation.pageNumbers.length > 0) ||
      (citation.paragraphNumbers && citation.paragraphNumbers.length > 0);

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
          Citation {index}
        </Text>

        {/* Blockquote */}
        {citation.content && (
          <Blockquote
            size="1"
            style={{
              borderLeftColor: isActive ? 'var(--accent-9)' : 'var(--accent-a6)',
              borderLeftWidth: '4px',
              color: 'var(--slate-12)',
              lineHeight: 'var(--line-height-1)',
              paddingLeft: 'var(--space-3)',
              margin: 0,
            }}
          >
            {citation.content}
          </Blockquote>
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
                Page {p}
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
                Paragraph {b}
              </Badge>
            ))}
          </Flex>
        )}
      </Flex>
    );
  },
);
