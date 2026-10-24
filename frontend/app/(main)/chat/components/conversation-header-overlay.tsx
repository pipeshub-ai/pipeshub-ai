'use client';

import type { ReactNode } from 'react';
import { Box } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

export interface ConversationHeaderOverlayProps {
  conversationId: string | null;
  /** Who made the agent this conversation runs on; null when not shown. */
  agentChip: ReactNode;
  /** Share controls; null when the person cannot share. */
  shareGroup: ReactNode;
}

/** Top-right items over the conversation. Editions plug in via config.ts. */
export function ConversationHeaderOverlay({ agentChip, shareGroup }: ConversationHeaderOverlayProps) {
  // No room on a phone; the agent header menu shows the creator instead.
  const showChip = !useIsMobile() && !!agentChip;
  return (
    <>
      {showChip && (
        <Box
          style={{
            position: 'absolute',
            top: 10,
            right: shareGroup ? 200 : 16,
            zIndex: 19,
          }}
        >
          {agentChip}
        </Box>
      )}

      {shareGroup && (
        <Box style={{ position: 'absolute', top: 12, right: 16, zIndex: 20 }}>{shareGroup}</Box>
      )}
    </>
  );
}
