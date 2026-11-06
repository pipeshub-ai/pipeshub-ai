'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { DemoSourceBadge } from '@/app/(main)/workspace/connectors/demo-data/components';
import { CITATION_CHIP_TEXT, CITATION_NUMBER_CHIP, useCitationPopoverTrigger } from './citation-number-circle';
import type { CitationData, CitationCallbacks } from './types';

interface InlineCitationBadgeProps {
  /** The `[N]` number from the markdown text */
  chunkIndex: number;
  /** Per-occurrence key when the same number appears multiple times in one message */
  occurrenceKey?: string;
  /** Full citation data — may be undefined while streaming before data arrives */
  citation?: CitationData;
  /** Interaction callbacks */
  callbacks?: CitationCallbacks;
}

/** Characters of a record name a pill shows before it is cut with "…". */
export const CITATION_LABEL_MAX_CHARS = 24;

/** The record's name without its extension, cut to the pill's length. */
export function citationLabel(recordName: string | undefined): { full: string; shown: string } {
  const full = recordName ? recordName.replace(/\.[^/.]+$/, '') : '';
  const shown = full.length > CITATION_LABEL_MAX_CHARS ? `${full.slice(0, CITATION_LABEL_MAX_CHARS)}…` : full;
  return { full, shown };
}

/**
 * The pill's frame: a quiet bubble that sits in a line of text without making
 * the line taller, while `.citation-pill`'s hit area still reaches 24px.
 */
export const CITATION_PILL_FRAME: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 'var(--space-1)',
  boxSizing: 'content-box',
  paddingBlock: 'calc(var(--space-1) / 2)',
  paddingInline: 'var(--space-1)',
  // Pulls the frame's padding and edge back out of the line box, so a line holding a pill is no taller.
  marginBlock: 'calc(var(--space-1) / -2)',
  marginInlineStart: 'var(--space-1)',
  marginInlineEnd: 'calc(var(--space-1) / 2)',
  borderRadius: 'var(--radius-1)',
  borderWidth: 'calc(var(--space-1) / 6)',
  verticalAlign: 'middle',
  whiteSpace: 'nowrap',
};

/**
 * One citation inside the answer: one button holding the source's mark, its
 * name and the marker's number, opening the citation's preview.
 */
function InlineCitationBadgeImpl({ chunkIndex, occurrenceKey, citation, callbacks }: InlineCitationBadgeProps) {
  // No citation data yet (early streaming): only the number, quiet, and nothing to open.
  if (!citation) {
    return (
      <span
        style={{
          ...CITATION_PILL_FRAME,
          paddingInline: 'calc(var(--space-1) / 2)',
          background: 'var(--surface-subtle)',
          border: `${CITATION_PILL_FRAME.borderWidth} solid var(--border-subtle)`,
        }}
      >
        <span
          style={{
            ...CITATION_NUMBER_CHIP,
            background: 'var(--fill-muted)',
            borderColor: 'var(--border-default)',
            color: 'var(--text-secondary)',
          }}
        >
          {chunkIndex}
        </span>
      </span>
    );
  }
  return <ResolvedCitationPill chunkIndex={chunkIndex} occurrenceKey={occurrenceKey} citation={citation} callbacks={callbacks} />;
}

function ResolvedCitationPill({
  chunkIndex,
  occurrenceKey,
  citation,
  callbacks,
}: InlineCitationBadgeProps & { citation: CitationData }) {
  const { t } = useTranslation();
  const { ref, isOpen, onClick } = useCitationPopoverTrigger({ chunkIndex, occurrenceKey, citation, callbacks });
  const { full, shown } = citationLabel(citation.recordName);
  return (
    <button
      ref={ref}
      type="button"
      className="citation-pill"
      data-citation-pill=""
      onClick={onClick}
      aria-haspopup="dialog"
      aria-expanded={isOpen}
      aria-label={t('chat.citationPill.number', { number: chunkIndex, name: full })}
      style={{ ...CITATION_PILL_FRAME, cursor: 'pointer', font: 'inherit' }}
    >
      <span aria-hidden style={{ display: 'inline-flex', flexShrink: 0 }}>
        <ConnectorIcon type={citation.connector || ''} size={14} />
      </span>
      <span style={CITATION_CHIP_TEXT}>{shown}</span>
      <DemoSourceBadge connectorId={citation.connectorId} />
      <span style={CITATION_NUMBER_CHIP}>{chunkIndex}</span>
    </button>
  );
}

/**
 * A completed block's markdown source stops changing once it settles (see
 * `MarkdownBlock` in `answer-content.tsx`), but its citation badges are
 * still recreated as plain JSX on every parent render. Memoizing here
 * avoids re-rendering an unaffected badge just because a sibling row's
 * citation data arrived. Compares by the citation's identity fields rather
 * than object identity.
 */
function inlineCitationBadgePropsEqual(
  prev: InlineCitationBadgeProps,
  next: InlineCitationBadgeProps,
): boolean {
  return (
    prev.chunkIndex === next.chunkIndex &&
    prev.occurrenceKey === next.occurrenceKey &&
    prev.callbacks === next.callbacks &&
    prev.citation?.citationId === next.citation?.citationId &&
    prev.citation?.recordName === next.citation?.recordName &&
    prev.citation?.webUrl === next.citation?.webUrl
  );
}

export const InlineCitationBadge = React.memo(InlineCitationBadgeImpl, inlineCitationBadgePropsEqual);
