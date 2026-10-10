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
  /** False continues the previous message: no avatar or name, a time at the start on hover. */
  showHeader?: boolean;
  testId: string;
  /** Hover/focus toolbar, drawn at the row's top right. */
  actions?: (active: boolean) => React.ReactNode;
  /** A line above the answer inside an AI reply's block ("Replying to X"). */
  lead?: React.ReactNode;
  /** Drawn after the time in the header line (an AI reply's access note or live status). */
  headerExtra?: React.ReactNode;
  /** Shown when hovering the name (an agent's handle). */
  nameTooltip?: string;
  children: React.ReactNode;
}

/** The Slack-style row shared by people's messages and AI replies: a header line with an inline avatar, then the body. */
export function MessageRow({ name, tone, avatarSrc, time, showHeader = true, testId, actions, lead, headerExtra, nameTooltip, children }: MessageRowProps) {
  const { t } = useTranslation();
  const size = useAvatarSize();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const active = hovered || focused;
  const right = tone === 'ai';
  const compactWidth = size <= AVATAR_SIZE_MOBILE;
  const indent = size + 8;
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
        padding: `${showHeader ? 'var(--space-2)' : '1px'} var(--space-2) ${tone === 'ai' ? 'var(--space-2)' : showHeader ? 'var(--space-1)' : '1px'}`,
        margin: '0 calc(var(--space-2) * -1)',
        borderRadius: 'var(--radius-2)',
        background: active ? 'var(--slate-a2)' : 'transparent',
        position: 'relative',
      }}
    >
      {showHeader ? (
        <Flex align="center" gap="2" wrap="nowrap" justify={right ? 'end' : undefined} style={{ marginBottom: 2, minWidth: 0 }}>
          <MessageAvatar name={name} tone={tone} size={size} src={avatarSrc} />
          {nameTooltip ? (
            <Tooltip content={nameTooltip}>
              <Text size="2" weight="bold" data-testid="message-author" style={{ color: 'var(--slate-12)', flexShrink: 0 }}>
                {name}
              </Text>
            </Tooltip>
          ) : (
            <Text size="2" weight="bold" data-testid="message-author" style={{ color: 'var(--slate-12)', flexShrink: 0 }}>
              {name}
            </Text>
          )}
          {time ? <Box style={{ flexShrink: 0 }}><MessageTime iso={time} /></Box> : null}
          {/* Only the extra note gives way on a narrow header, so it truncates instead of wrapping onto a line of its own. */}
          {headerExtra ? <Box style={{ minWidth: 0, flexShrink: 1, display: 'flex' }}>{headerExtra}</Box> : null}
        </Flex>
      ) : time ? (
        <Box
          style={{ position: 'absolute', top: 2, insetInlineStart: 0, width: indent + 8, textAlign: 'end', pointerEvents: 'none', fontSize: 10 }}
        >
          <MessageTime iso={time} compact visible={active} />
        </Box>
      ) : null}
      {right ? (
        <Box
          data-testid="message-body-block"
          data-compact={compactWidth ? 'true' : undefined}
          style={{
            marginInlineStart: compactWidth ? 'var(--space-4)' : 'var(--space-8)',
            minWidth: 0,
            position: 'relative',
            textAlign: 'start',
            background: 'var(--olive-2)',
            border: '1px solid var(--olive-a4)',
            borderRadius: 'var(--radius-4)',
            padding: compactWidth ? 'var(--space-2)' : 'var(--space-2) var(--space-3)',
          }}
        >
          {lead}
          {children}
        </Box>
      ) : (
        <Box style={{ minWidth: 0, marginInlineStart: indent }}>{children}</Box>
      )}
      {actions ? (
        <Box style={{ position: 'absolute', top: 4, insetInlineEnd: 'var(--space-2)' }} data-testid="message-row-actions">
          {actions(active)}
        </Box>
      ) : null}
    </article>
  );
}
