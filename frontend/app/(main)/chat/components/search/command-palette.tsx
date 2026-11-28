'use client';

import React from 'react';
import { Card, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KbdChip } from '@/app/components/sidebar/kbd-chip';
import { getModifierSymbol } from '@/lib/utils/platform';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { TOUCH_TARGET_PX } from '@/app/components/ui/responsive';
import { CMDK_ROW_CLASS, cmdkRowStyle } from './row-styles';

interface CommandPaletteProps {
  onClick: () => void;
}

/**
 * Command Palette row: the "New chat" action inside the search dialog.
 * Shows the shortcut the chat page binds (⌘ + Shift + K, Ctrl on Windows and
 * Linux) where a keyboard is likely, that is on a hover-capable pointer.
 */
export function CommandPalette({ onClick }: CommandPaletteProps) {
  const { t } = useTranslation();
  const canHover = useCanHover();
  const modKey = getModifierSymbol();

  return (
    <Card asChild variant="ghost">
      <button
        type="button"
        className={CMDK_ROW_CLASS}
        onClick={onClick}
        aria-keyshortcuts={modKey === '⌘' ? 'Meta+Shift+K' : 'Control+Shift+K'}
        style={{
          ...cmdkRowStyle,
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--space-2)',
          padding: 'var(--space-2) var(--space-1)',
          minHeight: canHover ? cmdkRowStyle.minHeight : TOUCH_TARGET_PX,
        }}
      >
        <Flex as="span" align="center" gap="2" style={{ minWidth: 0 }}>
          <MaterialIcon name="assistant" size={16} color="var(--accent-a11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--text-secondary)' }}>
            {t('chat.newChat')}
          </Text>
        </Flex>
        {canHover && <KbdChip keys={[modKey, t('common.shiftKey'), 'K']} />}
      </button>
    </Card>
  );
}
