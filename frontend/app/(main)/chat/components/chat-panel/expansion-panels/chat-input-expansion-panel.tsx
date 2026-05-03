'use client';

import React, { useEffect } from 'react';
import { Flex } from '@radix-ui/themes';

interface ChatInputExpansionPanelProps {
  /** Whether the panel is open */
  open: boolean;
  /** Called when the panel should close */
  onClose: () => void;
  /** Panel content */
  children: React.ReactNode;
  /** Optional header content (tabs, expand button) rendered above children */
  header?: React.ReactNode;
  /** Override the default panel height. Default: '380px' */
  height?: string;
  /** Override the minimum panel height. Default: none */
  minHeight?: string;
}

/**
 * Reusable inline expansion area for the chat input container.
 *
 * When open, renders its children inline within the parent flex layout.
 * The parent ChatInput container hides the textarea when this is open,
 * so the panel content replaces the input area while keeping the bottom
 * toolbar visible.
 *
 * Features:
 * - Renders inline (not floating/absolute)
 * - Closes on Escape key
 * - Reusable for different panel types (query mode, KB filter, etc.)
 */
export function ChatInputExpansionPanel({
  open,
  onClose,
  children,
  header,
  height = '380px',
  minHeight,
}: ChatInputExpansionPanelProps) {
  // Close on Escape
  useEffect(() => {
    if (!open) return;

    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <Flex direction="column" gap="2" style={{ height, minHeight, overflow: 'hidden' }}>
      {header}
      <Flex direction="column" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {children}
      </Flex>
    </Flex>
  );
}
