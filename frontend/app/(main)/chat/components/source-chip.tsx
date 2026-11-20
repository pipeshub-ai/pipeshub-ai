'use client';

import React from 'react';
import { MentionIcon } from '@/app/(main)/chat/components/mention-icon';
import type { MentionNode } from '@/app/(main)/chat/utils/node-mentions';

/**
 * A source named with `@` (an app, a Collection, a space, project, bucket or Drive, a folder or a file): its icon and
 * name in the accent's light pill, at the size of the text around it. The composer, a sent message and a question's
 * heading all draw it, so a source looks the same wherever it is named.
 */
export function SourceChip({ source, label }: { source: MentionNode; label: string }) {
  return (
    <span
      className="ph-mention-chip ph-source-chip"
      data-testid="source-chip"
      data-source-id={source.id}
      title={source.name !== label ? source.name : undefined}
      style={{
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
      }}
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
