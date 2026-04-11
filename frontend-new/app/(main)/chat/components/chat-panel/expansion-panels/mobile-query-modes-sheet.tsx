'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { QueryModePanel } from '@/chat/components/chat-panel/expansion-panels/query-mode-panel';
import { useChatStore } from '@/chat/store';
import type { QueryMode } from '@/chat/types';

interface MobileQueryModesSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Mobile-only bottom sheet for selecting query modes (Chat, Web Search, etc.).
 * Opened from the mode switcher pill in the chat toolbar.
 */
export function MobileQueryModesSheet({ open, onOpenChange }: MobileQueryModesSheetProps) {
  const { t } = useTranslation();
  const settings = useChatStore((s) => s.settings);
  const setQueryMode = useChatStore((s) => s.setQueryMode);
  const setMode = useChatStore((s) => s.setMode);

  const handleSelect = (mode: QueryMode) => {
    setQueryMode(mode);
    if (settings.mode === 'search') setMode('chat');
    onOpenChange(false);
  };

  return (
    <MobileBottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title={t('chat.differentModesOfQuery', { defaultValue: 'Different Modes of Query' })}
    >
      <QueryModePanel
        activeMode={settings.queryMode}
        onSelect={handleSelect}
        hideHeader
      />
    </MobileBottomSheet>
  );
}
