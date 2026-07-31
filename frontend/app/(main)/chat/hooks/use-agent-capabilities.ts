'use client';

import { useChatStore } from '@/chat/store';
import { DEFAULT_AGENT_CAPABILITIES } from '@/chat/types';

export interface AgentCapabilitiesController {
  internalSearch: boolean;
  webSearch: boolean;
  /** `false` = this capability isn't configured for the current agent; its toggle is disabled. */
  agentHasInternalSearch: boolean;
  agentHasWebSearch: boolean;
  setInternalSearch: (enabled: boolean) => void;
  setWebSearch: (enabled: boolean) => void;
}

/**
 * Single source of truth for the Indexed Data Search / Web Search capability
 * toggles, shared between the toolbar (chat-input.tsx) and the resources
 * panels so both read/write the same state for a given context:
 * - Universal assistant (no agentId): `settings.agentCapabilities`.
 * - Scoped agent (`agentId` present): `scopedAgentCapabilities[agentId]`,
 *   gated by what the agent was actually built with (connectors/collections
 *   for internal search, `agentHasWebSearch` for web search).
 */
export function useAgentCapabilitiesForContext(
  isAgentChat: boolean,
  agentId?: string | null
): AgentCapabilitiesController {
  const universalCaps = useChatStore((s) => s.settings.agentCapabilities);
  const setAgentCapabilities = useChatStore((s) => s.setAgentCapabilities);

  const connectors = useChatStore((s) => s.agentChatConnectors);
  const kbIds = useChatStore((s) => s.agentChatKbIds);
  const agentHasWebSearchFlag = useChatStore((s) => s.agentHasWebSearch);
  const scopedCaps = useChatStore((s) =>
    agentId ? s.scopedAgentCapabilities[agentId] ?? DEFAULT_AGENT_CAPABILITIES : DEFAULT_AGENT_CAPABILITIES
  );
  const setScopedAgentCapabilities = useChatStore((s) => s.setScopedAgentCapabilities);

  if (isAgentChat && agentId) {
    const agentHasInternalSearch = connectors.length > 0 || kbIds.length > 0;
    return {
      internalSearch: agentHasInternalSearch ? scopedCaps.internalSearch : false,
      webSearch: agentHasWebSearchFlag ? scopedCaps.webSearch : false,
      agentHasInternalSearch,
      agentHasWebSearch: agentHasWebSearchFlag,
      setInternalSearch: (enabled) => setScopedAgentCapabilities(agentId, { internalSearch: enabled }),
      setWebSearch: (enabled) => setScopedAgentCapabilities(agentId, { webSearch: enabled }),
    };
  }

  return {
    internalSearch: universalCaps.internalSearch,
    webSearch: universalCaps.webSearch,
    agentHasInternalSearch: true,
    agentHasWebSearch: true,
    setInternalSearch: (enabled) => setAgentCapabilities({ internalSearch: enabled }),
    setWebSearch: (enabled) => setAgentCapabilities({ webSearch: enabled }),
  };
}
