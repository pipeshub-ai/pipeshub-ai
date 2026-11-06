'use client';

import React, { useState } from 'react';
import { Button, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface JumpToLatestProps {
  /** "Latest messages", or the count of what arrived while the reader was scrolled up. */
  label: string;
  /** Something new arrived: the pill takes the brand tint, because that is the reason to go back down. */
  fresh?: boolean;
  onJump: () => void;
  'data-testid'?: string;
}

/**
 * The way back to the end of a long chat, shown above the composer once the reader has scrolled up.
 * The caller decides when it shows and where it scrolls; this is only the pill. It is the surface
 * every floating thing over the column uses: a subtle card with an edge, the large radius and the
 * menu shadow. On a touch screen it keeps a 44px target around the 28px pill.
 */
export function JumpToLatest({ label, fresh = false, onJump, 'data-testid': testId }: JumpToLatestProps) {
  const [hover, setHover] = useState(false);
  // Emerald, not accent: this is a gray Radix control.
  const tone = fresh ? 'var(--emerald-11)' : undefined;
  return (
    <Button
      type="button"
      variant="ghost"
      color="gray"
      size="1"
      data-testid={testId}
      onClick={onJump}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="ghost-box conversation-fade-in touch-hit"
      style={{
        pointerEvents: 'auto',
        height: 'calc(var(--space-6) - var(--space-1))',
        paddingInline: 'var(--space-2) calc(var(--space-2) + var(--space-1) / 2)',
        gap: 'var(--space-1)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: hover ? 'var(--fill-muted)' : 'var(--surface-subtle)',
        border: '1px solid var(--border-default)',
        boxShadow: 'var(--shadow-4)',
        cursor: 'pointer',
      }}
    >
      <span aria-hidden style={{ display: 'inline-flex' }}>
        <MaterialIcon name="arrow_downward" size={14} color={tone ?? 'var(--text-secondary)'} />
      </span>
      <Text size="1" weight="medium" style={{ color: tone ?? 'var(--text-primary)' }}>
        {label}
      </Text>
    </Button>
  );
}
