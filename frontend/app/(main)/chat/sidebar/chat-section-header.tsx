'use client';

import { Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ELEMENT_HEIGHT, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';

interface ChatSectionHeaderProps {
  title: string;
  onAdd?: () => void;
  /** `aria-label` for the add button (required for a11y when `onAdd` is set). */
  addAriaLabel?: string;
  /** Called when the title is clicked (opens "More Chats") */
  onTitleClick?: () => void;
  /** Whether the section is currently collapsed */
  isCollapsed?: boolean;
  /** Called when the collapse chevron is clicked */
  onToggleCollapse?: () => void;
  /**
   * `rail`: the main chat rail's group heading. A 40px row (the 32px label row with a 4px band above
   * and below), a plain-text title, and a + with a touch-sized hit area under a coarse pointer.
   */
  size?: 'default' | 'rail';
}

/**
 * Section header for a chat group ("Shared Chats", "Your Chats").
 * Shares the same label styling as TimeGroup sub-headings,
 * with an optional "+" action button and collapse chevron on the right.
 */
export function ChatSectionHeader({
  title,
  onAdd,
  addAriaLabel,
  onTitleClick,
  isCollapsed,
  onToggleCollapse,
  size = 'default',
}: ChatSectionHeaderProps) {
  const { t } = useTranslation();
  const isRail = size === 'rail';
  return (
    <Flex
      align="center"
      justify="between"
      style={{
        height: isRail ? `calc(${ELEMENT_HEIGHT}px + var(--space-2))` : ELEMENT_HEIGHT,
        padding: '0 var(--space-3)',
        flexShrink: 0,
      }}
    >
      <Text
        size="1"
        onClick={onToggleCollapse ?? onTitleClick}
        style={{
          color: 'var(--text-secondary)',
          cursor: (onToggleCollapse ?? onTitleClick) ? 'pointer' : 'default',
          flex: 1,
        }}
      >
        {title}
      </Text>
      <Flex align="center" gap="1">
        {onAdd && !isCollapsed && (
          <IconButton
            variant="ghost"
            size="1"
            color="gray"
            className={isRail ? 'touch-hit' : undefined}
            onClick={(e) => { e.stopPropagation(); onAdd(); }}
            aria-label={addAriaLabel ?? t('chat.addToSection', { section: title })}
            style={{ margin: 0 }}
          >
            <MaterialIcon name="add" size={ICON_SIZE_DEFAULT} color="var(--text-secondary)" />
          </IconButton>
        )}
        {onToggleCollapse && (
          <IconButton
            variant="ghost"
            size="1"
            color="gray"
            onClick={onToggleCollapse}
            aria-label={isCollapsed ? t('chat.expandSection') : t('chat.collapseSection')}
            style={{ margin: 0 }}
          >
            <MaterialIcon
              name="expand_more"
              size={ICON_SIZE_DEFAULT}
              color="var(--text-secondary)"
              style={{
                transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
                transition: 'transform var(--motion-control) var(--motion-ease)',
                display: 'block',
              }}
            />
          </IconButton>
        )}
      </Flex>
    </Flex>
  );
}
