'use client';

import React, { useState } from 'react';
import { Box, Flex, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { AVATAR_SIZE_MOBILE, MessageAvatar } from './message-avatar';
import { MessageTime, formatClock } from './message-time';
import { useAvatarSize } from './use-compact-avatar';

interface MessageRowProps {
  /** Name drawn in bold and used as the accessible name. */
  name: string;
  tone: 'person' | 'ai';
  avatarSrc?: string | null;
  /** ISO time; the row has no time while an answer is still streaming. */
  time?: string;
  /** False continues the previous message: no avatar or name, a time in the gutter on hover. */
  showHeader?: boolean;
  testId: string;
  /** Hover/focus toolbar, drawn at the row's top right. */
  actions?: (active: boolean) => React.ReactNode;
  /** A line above the header, in the content column ("Replying to X"). */
  lead?: React.ReactNode;
  /** Shown when hovering the name (an agent's handle). */
  nameTooltip?: string;
  children: React.ReactNode;
}

/** The Slack-style row shared by people's messages and AI replies: gutter, header line, body. */
export function MessageRow({ name, tone, avatarSrc, time, showHeader = true, testId, actions, lead, nameTooltip, children }: MessageRowProps) {
  const { t } = useTranslation();
  const size = useAvatarSize();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const active = hovered || focused;
  const right = tone === 'ai';
  const compactWidth = size <= AVATAR_SIZE_MOBILE;
  const bodyCol = right ? 1 : 2;
  const gutterCol = right ? 2 : 1;
  const clock = time ? formatClock(time) : '';
  const label = clock ? t('chat.collab.timeline.messageAria', { name, time: clock }) : t('chat.collab.timeline.messageAriaNoTime', { name });

  return (
    <article
      aria-label={label}
      data-testid={testId}
      data-side={right ? 'right' : 'left'}
      data-grouped={showHeader ? undefined : 'true'}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false);
      }}
      style={{
        display: 'grid',
        gridTemplateColumns: right ? `minmax(0, 1fr) ${size}px` : `${size}px minmax(0, 1fr)`,
        columnGap: size <= 24 ? 'var(--space-2)' : 'var(--space-3)',
        padding: `${showHeader ? 'var(--space-2)' : '1px'} var(--space-2) ${tone === 'ai' ? 'var(--space-3)' : showHeader ? 'var(--space-2)' : '1px'}`,
        margin: '0 calc(var(--space-2) * -1)',
        borderRadius: 'var(--radius-2)',
        background: active ? 'var(--slate-a2)' : 'transparent',
        position: 'relative',
      }}
    >
      {lead ? <Box style={{ gridColumn: bodyCol, gridRow: 1, marginTop: 'var(--space-1)', marginBottom: 2, textAlign: right ? 'end' : undefined }}>{lead}</Box> : null}
      <Flex justify="center" align="start" style={{ paddingTop: showHeader ? 0 : 2, gridColumn: gutterCol, gridRow: lead ? 2 : 1 }}>
        {showHeader ? (
          <MessageAvatar name={name} tone={tone} size={size} src={avatarSrc} />
        ) : time ? (
          <MessageTime iso={time} compact visible={active} />
        ) : null}
      </Flex>
      <Box style={{ minWidth: 0, gridColumn: bodyCol, gridRow: lead ? 2 : 1 }}>
        {showHeader ? (
          <Flex align="baseline" gap="2" wrap="wrap" justify={right ? 'end' : undefined} style={{ marginBottom: 2 }}>
            {nameTooltip ? (
              <Tooltip content={nameTooltip}>
                <Text size="2" weight="bold" data-testid="message-author" style={{ color: 'var(--slate-12)' }}>
                  {name}
                </Text>
              </Tooltip>
            ) : (
              <Text size="2" weight="bold" data-testid="message-author" style={{ color: 'var(--slate-12)' }}>
                {name}
              </Text>
            )}
            {time ? <MessageTime iso={time} /> : null}
          </Flex>
        ) : null}
        {right ? (
          <Box
            data-testid="message-body-block"
            data-compact={compactWidth ? 'true' : undefined}
            style={{
              ...(compactWidth
                ? { width: 'auto', marginInlineStart: '20%' }
                : { width: 'fit-content', maxWidth: '85%', marginInlineStart: 'auto' }),
              minWidth: 0,
              textAlign: 'start',
              background: 'var(--olive-2)',
              border: '1px solid var(--olive-a4)',
              borderRadius: 'var(--radius-4)',
              padding: 'var(--space-3)',
            }}
          >
            {children}
          </Box>
        ) : (
          children
        )}
      </Box>
      {actions ? (
        <Box style={{ position: 'absolute', top: 4, insetInlineEnd: 'var(--space-2)' }} data-testid="message-row-actions">
          {actions(active)}
        </Box>
      ) : null}
    </article>
  );
}
