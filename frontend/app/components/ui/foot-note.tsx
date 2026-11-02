'use client';

import type { CSSProperties, ReactNode } from 'react';
import { Callout } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// The small print beside a surface, as a card: tinted ground in a 1px hairline, a 13px mark
// centred on the first line. Tone carries meaning: neutral for a constraint, positive for a limit
// in the reader's favour, caution for something that happens whether or not they are watching,
// danger for something that already went wrong. Caution and danger colour the text too.

type Tone = 'neutral' | 'positive' | 'caution' | 'danger';

const TONE: Record<Tone, { color: 'gray' | 'jade' | 'orange' | 'red'; bg: string; border: string; icon: string; fg: string }> = {
  neutral: { color: 'gray', bg: 'var(--gray-a2)', border: 'var(--gray-a5)', icon: 'var(--gray-a10)', fg: 'var(--gray-11)' },
  positive: { color: 'jade', bg: 'var(--accent-a3)', border: 'var(--accent-a5)', icon: 'var(--accent-a11)', fg: 'var(--gray-11)' },
  caution: { color: 'orange', bg: 'var(--orange-a3)', border: 'var(--orange-a5)', icon: 'var(--orange-11)', fg: 'var(--orange-11)' },
  danger: { color: 'red', bg: 'var(--red-a2)', border: 'var(--red-a5)', icon: 'var(--red-11)', fg: 'var(--red-11)' },
};

// The text's own line box, so the mark sits on the first line however many lines follow.
const LINE = 'calc(var(--line-height-1) + var(--space-1) / 4)';

export function FootNote({
  tone = 'neutral',
  icon,
  action,
  children,
}: {
  tone?: Tone;
  /** Material Symbols glyph; defaults to `info`, or `error` for danger. */
  icon?: string;
  /** A button under the text, for the note's one next step. */
  action?: ReactNode;
  children: ReactNode;
}) {
  const t = TONE[tone];
  return (
    <Callout.Root
      size="1"
      variant="surface"
      color={t.color}
      style={
        {
          '--callout-icon-height': LINE,
          padding: 'calc(var(--space-3) * 5 / 6) var(--space-3)',
          borderRadius: 'var(--radius-2)',
          backgroundColor: t.bg,
          boxShadow: 'none',
          border: `1px solid ${t.border}`,
        } as CSSProperties
      }
    >
      <Callout.Icon>
        <MaterialIcon name={icon ?? (tone === 'danger' ? 'error' : 'info')} size={13} color={t.icon} />
      </Callout.Icon>
      <Callout.Text size="1" style={{ lineHeight: LINE, color: t.fg }}>
        {children}
      </Callout.Text>
      {action && <div>{action}</div>}
    </Callout.Root>
  );
}
