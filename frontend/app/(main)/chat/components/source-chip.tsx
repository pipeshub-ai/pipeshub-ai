'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { MentionIcon } from '@/app/(main)/chat/components/mention-icon';
import type { MentionNode } from '@/app/(main)/chat/utils/node-mentions';

/**
 * A source named with `@` (an app, a Collection, a space, project, bucket or Drive, a folder or a file): its icon and
 * name in the accent's light pill, at the size of the text around it. The composer, a sent message and a question's
 * heading all draw it, so a source looks the same wherever it is named.
 */
const CHIP_STYLE: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 'var(--space-1)',
  verticalAlign: 'bottom',
  maxWidth: '100%',
  padding: '0 var(--space-1)',
  borderRadius: 'var(--radius-2)',
  backgroundColor: 'var(--accent-a3)',
  color: 'var(--accent-11)',
  fontWeight: 'var(--font-weight-medium)',
};

export function SourceChip({ source, label }: { source: MentionNode; label: string }) {
  return (
    <span
      className="ph-mention-chip ph-source-chip"
      data-testid="source-chip"
      data-source-id={source.id}
      title={source.name !== label ? source.name : undefined}
      style={CHIP_STYLE}
    >
      <span aria-hidden style={{ display: 'inline-flex', lineHeight: 0, flexShrink: 0 }}>
        <MentionIcon
          nodeType={source.nodeType}
          connector={source.connector}
          extension={source.extension}
          mimeType={source.mimeType}
          name={source.name}
        />
      </span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
    </span>
  );
}

/** A source named where the reader can't open it: the chip's place and size, a lock, and nothing of which source. */
export function WithheldSourceChip() {
  const { t } = useTranslation();
  return (
    <span
      className="ph-mention-chip"
      data-testid="withheld-source-chip"
      title={t('chat.withheldSource.tooltip')}
      style={{ ...CHIP_STYLE, backgroundColor: 'var(--gray-a3)', color: 'var(--gray-11)' }}
    >
      <span aria-hidden style={{ display: 'inline-flex', lineHeight: 0, flexShrink: 0 }}>
        {/* Drawn, not a font's glyph: a copy of the line keeps no icon's name. */}
        <svg width={13} height={13} viewBox="0 0 16 16" fill="none" stroke="var(--gray-11)" strokeWidth={1.5}>
          <rect x="3" y="7" width="10" height="7" rx="1.5" />
          <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
        </svg>
      </span>
      <span style={{ whiteSpace: 'nowrap' }}>{t('chat.withheldSource.label')}</span>
    </span>
  );
}
