'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { useTranslation } from 'react-i18next';
import { getModifierSymbol } from '@/lib/utils/platform';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { TOUCH_TARGET_PX } from '@/app/components/ui/responsive';

interface CommandPaletteProps {
  onClick: () => void;
}

/**
 * Command Palette row — the "New Chat" action entry inside the search view.
 * Shows the keyboard shortcut ⌘+Shift+K where a keyboard is likely (hover-capable input).
 * Extensible: additional command palette actions can be added here later.
 */
export function CommandPalette({ onClick }: CommandPaletteProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const canHover = useCanHover();
  const modKey = getModifierSymbol();

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        font: 'inherit',
        color: 'inherit',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        minHeight: canHover ? undefined : TOUCH_TARGET_PX,
        padding: '10px var(--space-4)',
        margin: 'var(--space-2) var(--space-3)',
        borderRadius: 'var(--radius-1)',
        cursor: 'pointer',
        backgroundColor: isHovered ? 'var(--slate-a3)' : 'transparent',
        transition: 'background-color 120ms ease',
      }}
    >
      <Flex as="span" align="center" gap="2">
        <ChatStarIcon
          size={16}
          color="var(--slate-11)"
        />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {t('chat.newChat')}
        </Text>
      </Flex>
      {canHover && (
        <Text
          size="1"
          style={{ border: '1px solid var(--slate-3)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--slate-1)', padding: '0 var(--space-1)' }}
        >
          {modKey} + {t('common.shiftKey')} + K
        </Text>
      )}
    </button>
  );
}
