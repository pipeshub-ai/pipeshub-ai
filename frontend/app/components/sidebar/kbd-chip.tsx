'use client';

import type { ReactNode } from 'react';
import { Kbd } from '@radix-ui/themes';

/** The separator between keys, so every hint reads the same way ("⌘ + K", "Ctrl + Shift + K"). */
const KEY_SEPARATOR = ' + ';

interface KbdChipProps {
  /** The keys in order, joined with " + " (["⌘", "K"] reads "⌘ + K"). */
  keys?: string[];
  /** Free content, for a hint that is not a key combination. Ignored when `keys` is set. */
  children?: ReactNode;
}

/**
 * The keyboard shortcut chip shown next to a navigation row or a command.
 *
 * Radix Kbd at size 1, flattened into a chip: a raised ground with a hairline
 * border in place of the keycap shadow, 17px tall, regular weight. Colours and
 * type come from the theme roles, so it follows light and dark.
 */
export function KbdChip({ keys, children }: KbdChipProps) {
  return (
    <Kbd
      size="1"
      style={{
        height: 'calc(var(--space-4) + var(--space-1) / 4)',
        minWidth: 'auto',
        top: 'auto',
        paddingInline: 'var(--space-1)',
        paddingBottom: 'initial',
        backgroundColor: 'var(--surface-raised)',
        boxShadow: 'none',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-2)',
        color: 'var(--text-primary)',
        fontWeight: 'var(--font-weight-regular)',
        fontSize: 'var(--font-size-1)',
        lineHeight: 'var(--line-height-1)',
        letterSpacing: 'var(--letter-spacing-1)',
        wordSpacing: 'normal',
        verticalAlign: 'middle',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        // Kbd does not shrink by default; the chip gives way to its row.
        flexShrink: 1,
      }}
    >
      {keys ? keys.join(KEY_SEPARATOR) : children}
    </Kbd>
  );
}
