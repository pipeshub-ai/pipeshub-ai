'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { QueryModePanel } from '@/chat/components/chat-panel/expansion-panels/query-mode-panel';
import { AgentStrategyModePanel } from '@/chat/components/chat-panel/expansion-panels/agent-strategy-mode-panel';
import { useChatStore } from '@/chat/store';
import type { AgentStrategy, QueryMode } from '@/chat/types';

interface MobileQueryModesSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Agent-scoped chat (`?agentId=`) — sheet picks strategy (auto / verify / …)
   * instead of query mode (internal search / web / agent).
   */
  agentChat?: boolean;
}

/**
 * Mobile-only bottom sheet for selecting query modes (Chat, Web Search, etc.).
 * Opened from the mode switcher pill in the chat toolbar.
 *
 * In agent chat, the same trigger opens agent strategy instead.
 */
export function MobileQueryModesSheet({
  open,
  onOpenChange,
  agentChat = false,
}: MobileQueryModesSheetProps) {
  const { t } = useTranslation();
  const settings = useChatStore((s) => s.settings);
  const setQueryMode = useChatStore((s) => s.setQueryMode);
  const setMode = useChatStore((s) => s.setMode);
  const setAgentStrategy = useChatStore((s) => s.setAgentStrategy);

  const handleSelectQueryMode = (mode: QueryMode) => {
    setQueryMode(mode);
    if (settings.mode === 'search') setMode('chat');
    onOpenChange(false);
  };

  const handleSelectStrategy = (strategy: AgentStrategy) => {
    setAgentStrategy(strategy);
    onOpenChange(false);
  };

  return (
    <MobileBottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title={t('chat.differentModesOfQuery', { defaultValue: 'Different Modes of Query' })}
    >
      {agentChat ? (
        <AgentStrategyModePanel
          activeStrategy={settings.agentStrategy}
          onSelect={handleSelectStrategy}
          hideHeader
        />
      ) : (
        <QueryModePanel
          activeMode={settings.queryMode}
          onSelect={handleSelectQueryMode}
          hideHeader
        />
      )}
    </MobileBottomSheet>
  );
}
