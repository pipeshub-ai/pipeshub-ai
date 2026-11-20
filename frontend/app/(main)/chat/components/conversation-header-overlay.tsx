'use client';

import type { ReactNode } from 'react';
import { Flex } from '@radix-ui/themes';

export interface ConversationHeaderOverlayProps {
  conversationId: string | null;
  /** Share controls; null when the person cannot share. */
  shareGroup: ReactNode;
  /** The chat's Background agents button; null while the chat has no background jobs. */
  backgroundAgents?: ReactNode;
  /** "Permissions in this chat"; null unless the owner's chat holds an "Allow for this chat". */
  grants?: ReactNode;
  /** The Context opener for the column beside the chat, open or shut; drawn last, at the right edge. */
  panelToggle?: ReactNode;
}

/** Top-right items over the conversation, in one row. Editions plug in via config.ts. */
export function ConversationHeaderOverlay({ shareGroup, panelToggle, backgroundAgents = null, grants = null }: ConversationHeaderOverlayProps) {
  if (!shareGroup && !panelToggle && !backgroundAgents && !grants) return null;
  return (
    // gap 4 matches ShareHeaderGroup; the ghost Share button's negative margin eats part of it.
    <Flex align="center" gap="4" style={{ position: 'absolute', top: 12, right: 16, zIndex: 20 }}>
      {grants}
      {backgroundAgents}
      {shareGroup}
      {panelToggle}
    </Flex>
  );
}
