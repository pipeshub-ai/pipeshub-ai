'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { useTranslation } from 'react-i18next';
import { getModifierSymbol } from '@/lib/utils/platform';

interface CommandPaletteProps {
  onClick: () => void;
}

/**
 * Command Palette row — the "New Chat" action entry inside the search view.
 * Displays the keyboard shortcut ⌘+Shift+K and navigates to a new chat on click.
 * Extensible: additional command palette actions can be added here later.
 */
export function CommandPalette({ onClick }: CommandPaletteProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const modKey = getModifierSymbol();

  return (
    <Flex
      align="center"
      justify="between"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
         padding: '10px var(--space-4)',
        margin: 'var(--space-2) var(--space-3)',
        borderRadius: 'var(--radius-1)',
        cursor: 'pointer',
        backgroundColor: isHovered ? 'var(--slate-a3)' : 'transparent',
        transition: 'background-color 120ms ease',
      }}
    >
      <Flex align="center" gap="2">
        <ChatStarIcon
          size={16}
          color="var(--slate-11)"
        />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {t('chat.newChat')}
        </Text>
      </Flex>
      <Text size="1" 
      style={{border: '1px solid var(--slate-3)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--slate-1)', padding: '0 var(--space-1)'}}>{modKey} + Shift + K</Text>
    </Flex>
  );
}
