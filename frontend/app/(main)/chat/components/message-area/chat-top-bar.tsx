'use client';

import React, { createContext, type ReactNode } from 'react';
import { Flex } from '@radix-ui/themes';
import { ProgressiveBlur } from '@/app/components/ui/progressive-blur';

/** The bar's height: 40px, and 52px on a phone, where its controls take 44px targets. */
export const CHAT_TOP_BAR_HEIGHT = 'var(--space-7)';
export const CHAT_TOP_BAR_PHONE_HEIGHT = 'calc(var(--space-7) + var(--space-3))';
/**
 * The fade under the bar: the transcript runs beneath it and dissolves into the page over this height, as the design's
 * top band does, so text never meets a hard edge. The newest question rests just below its heavy part.
 */
export const CHAT_TOP_FADE_HEIGHT_PX = 96;
/**
 * How far below the top a list that runs under the bar starts its content: the question rests 20 px above the fade's
 * end, less the column's own 16 px top padding.
 */
export const CHAT_TOP_FADE_INSET = `calc(${CHAT_TOP_FADE_HEIGHT_PX}px - var(--space-4) - var(--space-1) - var(--space-4))`;
/**
 * How far below the bar's top the page starts the chat's frame: nothing where the list runs under the bar, more where
 * the bar or an agent's header keeps its own room. The fade is drawn from the bar's top, so what the frame holds finds
 * the fade's end from this.
 */
export const ChatFrameTopContext = createContext('0px');
/** Left inset that clears the sidebar's reopen button, which sits in this corner while the nav is shut. */
const NAV_BUTTON_INSET = 'calc(var(--space-8) + var(--space-1))';

/**
 * The bar over a conversation: what the chat is on the left, its controls on the right. It floats over the column
 * with no surface of its own, and a fade lets the transcript dissolve under it instead of meeting a hard edge.
 * Only the two clusters take a pointer, so the transcript under the empty middle stays selectable.
 */
export function ChatTopBar({
  leading,
  actions,
  phone = false,
  clearNavButton = false,
  actionsGap = '1',
  testId,
}: {
  /** The title side: a glyph and the chat's name, or nothing. */
  leading?: ReactNode;
  /** The control side, right-aligned. */
  actions?: ReactNode;
  /** Phone layout: a 52px bar and a tighter right edge. */
  phone?: boolean;
  /** The nav is shut (or this is a phone): the title starts clear of its reopen button. */
  clearNavButton?: boolean;
  actionsGap?: '1' | '2' | '3' | '4';
  testId?: string;
}) {
  return (
    <Flex
      align="center"
      justify="between"
      gap="2"
      data-testid={testId}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: phone ? CHAT_TOP_BAR_PHONE_HEIGHT : CHAT_TOP_BAR_HEIGHT,
        paddingLeft: phone || clearNavButton ? NAV_BUTTON_INSET : 'var(--space-3)',
        paddingRight: phone ? 'var(--space-1)' : 'var(--space-3)',
        boxSizing: 'border-box',
        zIndex: 20,
        // Chrome, not a container: the chat scrolls beneath it, so only the controls take a pointer.
        pointerEvents: 'none',
      }}
    >
      {/* Static, at every width: the transcript dissolves into the canvas's top colour under the bar's controls. */}
      <ProgressiveBlur placement="top" height={CHAT_TOP_FADE_HEIGHT_PX} zIndex={-1} scrim="var(--surface-subtle)" />
      <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1, pointerEvents: 'auto' }}>
        {leading}
      </Flex>
      <Flex align="center" gap={actionsGap} style={{ flexShrink: 0, pointerEvents: 'auto' }}>
        {actions}
      </Flex>
    </Flex>
  );
}
