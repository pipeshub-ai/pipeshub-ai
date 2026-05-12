'use client';

import { useEffectiveAgentId } from '@/chat/hooks/use-effective-agent-id';
import { useIsMainChatRoute } from '@/chat/hooks/use-is-main-chat-route';

/** True on main `/chat` with no `agentId` (assistant / universal agent picker). */
export function useMainChatConnectorDefaultHint(): boolean {
  return useIsMainChatRoute() && !useEffectiveAgentId();
}
