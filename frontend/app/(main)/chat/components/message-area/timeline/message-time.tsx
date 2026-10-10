'use client';

import { Text, Tooltip } from '@radix-ui/themes';

export function formatClock(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export function formatFullDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'medium' });
}

interface MessageTimeProps {
  iso: string;
  /** Gutter variant: shown only on hover, so it takes the caller's visibility. */
  compact?: boolean;
  visible?: boolean;
}

/** The absolute time of a message, with the full date in a tooltip. */
export function MessageTime({ iso, compact = false, visible = true }: MessageTimeProps) {
  const label = formatClock(iso);
  if (!label) return null;
  return (
    <Tooltip content={formatFullDate(iso)}>
      <Text
        as="span"
        size="1"
        data-testid={compact ? 'message-time-gutter' : 'message-time'}
        style={{
          color: 'var(--slate-11)',
          whiteSpace: 'nowrap',
          opacity: visible ? 1 : 0,
          transition: 'opacity 0.12s ease',
        }}
      >
        <time dateTime={iso}>{label}</time>
      </Text>
    </Tooltip>
  );
}
