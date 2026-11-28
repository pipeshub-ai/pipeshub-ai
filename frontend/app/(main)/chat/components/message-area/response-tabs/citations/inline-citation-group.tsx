'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { DemoSourceBadge } from '@/app/(main)/workspace/connectors/demo-data/components';
import { CITATION_CHIP_TEXT, CITATION_NUMBER_CHIP, CitationNumberCircle, GROUP_CHIP_MIN_WIDTH } from './citation-number-circle';
import { CITATION_PILL_FRAME, citationLabel } from './inline-citation-badge';
import type { CitationData, CitationCallbacks } from './types';

export interface InlineCitationGroupItem {
  /** The `[N]` number from the markdown text */
  chunkIndex: number;
  /** Per-occurrence key when the same number appears multiple times */
  occurrenceKey?: string;
  /** Full citation data for this marker */
  citation: CitationData;
}

interface InlineCitationGroupProps {
  /** Consecutive citations pointing at the same record (length >= 2) */
  items: InlineCitationGroupItem[];
  /** Interaction callbacks forwarded to each number's popover */
  callbacks?: CitationCallbacks;
}

/** Numbers shown before the rest collapse into a "+N" button. */
export const INLINE_CITATION_GROUP_VISIBLE = 2;

/**
 * The pill used when consecutive `[N]` markers all point at the same record:
 * the source's mark and name once, then one button per citation. Past two the
 * rest collapse into a "+N" button that reveals them in place.
 */
function InlineCitationGroupImpl({ items, callbacks }: InlineCitationGroupProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const chipsRef = useRef<HTMLSpanElement | null>(null);
  const focusRevealedRef = useRef(false);

  // After "+N", focus moves to the first number it revealed.
  useEffect(() => {
    if (!expanded || !focusRevealedRef.current) return;
    focusRevealedRef.current = false;
    const revealed = chipsRef.current?.querySelectorAll<HTMLButtonElement>('button')[INLINE_CITATION_GROUP_VISIBLE];
    revealed?.focus();
  }, [expanded]);

  const first = items[0]?.citation;
  if (!first) return null;

  const { full, shown } = citationLabel(first.recordName);
  const visibleItems = expanded ? items : items.slice(0, INLINE_CITATION_GROUP_VISIBLE);
  const hiddenCount = items.length - visibleItems.length;

  return (
    <span
      role="group"
      aria-label={full}
      style={{
        ...CITATION_PILL_FRAME,
        background: 'var(--surface-subtle)',
        border: `${CITATION_PILL_FRAME.borderWidth} solid var(--border-subtle)`,
      }}
    >
      <span aria-hidden style={{ display: 'inline-flex', flexShrink: 0 }}>
        <ConnectorIcon type={first.connector || ''} size={14} />
      </span>
      <span aria-hidden style={CITATION_CHIP_TEXT}>{shown}</span>
      <DemoSourceBadge connectorId={first.connectorId} />
      <span ref={chipsRef} style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)' }}>
        {visibleItems.map((item, idx) => (
          <CitationNumberCircle
            key={item.occurrenceKey ?? `cite-circle-${item.chunkIndex}-${idx}`}
            chunkIndex={item.chunkIndex}
            occurrenceKey={item.occurrenceKey}
            citation={item.citation}
            callbacks={callbacks}
          />
        ))}
        {hiddenCount > 0 && (
          <button
            type="button"
            className="citation-pill citation-overflow-chip"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              focusRevealedRef.current = true;
              setExpanded(true);
            }}
            aria-label={t('chat.showMoreCitations', { count: hiddenCount })}
            style={{
              ...CITATION_NUMBER_CHIP,
              minWidth: GROUP_CHIP_MIN_WIDTH,
              margin: 0,
              cursor: 'pointer',
              fontFamily: 'inherit',
              background: 'var(--fill-muted)',
              borderColor: 'var(--border-default)',
              color: 'var(--text-secondary)',
            }}
          >
            +{hiddenCount}
          </button>
        )}
      </span>
    </span>
  );
}

/** See `inlineCitationBadgePropsEqual` in `inline-citation-badge.tsx` for why
 * this compares identity fields instead of relying on default reference
 * equality — same rationale, applied per item in the group. */
function inlineCitationGroupPropsEqual(
  prev: InlineCitationGroupProps,
  next: InlineCitationGroupProps,
): boolean {
  if (prev.callbacks !== next.callbacks) return false;
  if (prev.items.length !== next.items.length) return false;
  for (let i = 0; i < prev.items.length; i += 1) {
    const a = prev.items[i];
    const b = next.items[i];
    if (
      a.chunkIndex !== b.chunkIndex ||
      a.occurrenceKey !== b.occurrenceKey ||
      a.citation.citationId !== b.citation.citationId ||
      a.citation.recordName !== b.citation.recordName ||
      a.citation.webUrl !== b.citation.webUrl
    ) {
      return false;
    }
  }
  return true;
}

export const InlineCitationGroup = React.memo(InlineCitationGroupImpl, inlineCitationGroupPropsEqual);
