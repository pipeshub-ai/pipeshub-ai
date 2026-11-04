'use client';

import type { ReactNode } from 'react';
import { Flex } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

export interface ConversationHeaderOverlayProps {
  conversationId: string | null;
  /** Who made the agent this conversation runs on; null when not shown. */
  agentChip: ReactNode;
  /** Share controls; null when the person cannot share. */
  shareGroup: ReactNode;
  /** Temporary-chat toggle; only before the first message of a plain chat. */
  temporaryToggle?: ReactNode;
}

/** Top-right items over the conversation, in one row. Editions plug in via config.ts. */
export function ConversationHeaderOverlay({
  agentChip,
  shareGroup,
  temporaryToggle,
}: ConversationHeaderOverlayProps) {
  // No room on a phone; the agent header menu shows the creator instead.
  const showChip = !useIsMobile() && !!agentChip;
  if (!showChip && !shareGroup && !temporaryToggle) return null;
  return (
    // gap 4 matches ShareHeaderGroup; the ghost Share button's negative margin eats part of it.
    <Flex align="center" gap="4" style={{ position: 'absolute', top: 12, right: 16, zIndex: 20 }}>
      {temporaryToggle}
      {showChip && agentChip}
      {shareGroup}
    </Flex>
  );
}
