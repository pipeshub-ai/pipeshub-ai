'use client';

import { Flex, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ELEMENT_HEIGHT, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';

interface ChatSectionHeaderProps {
  title: string;
  onAdd?: () => void;
  /** `aria-label` for the add button (required for a11y when `onAdd` is set). */
  addAriaLabel?: string;
  /** Called when the title is clicked (opens "More Chats") */
  onTitleClick?: () => void;
}

/**
 * Section header for a chat group ("Shared Chats", "Your Chats").
 * Shares the same label styling as TimeGroup sub-headings,
 * with an optional "+" action button on the right.
 */
export function ChatSectionHeader({ title, onAdd, addAriaLabel, onTitleClick }: ChatSectionHeaderProps) {
  return (
    <Flex
      align="center"
      justify="between"
      style={{
        height: ELEMENT_HEIGHT,
        padding: '0 var(--space-3)',
      }}
    >
      <span
        onClick={onTitleClick}
        style={{
          fontSize: 12,
          fontWeight: 500,
          lineHeight: 'var(--line-height-1)',
          letterSpacing: '0.04px',
          color: 'var(--slate-11)',
          cursor: onTitleClick ? 'pointer' : 'default',
        }}
      >
        {title}
      </span>
      {onAdd && (
        <IconButton
          variant="ghost"
          size="1"
          color="gray"
          onClick={onAdd}
          aria-label={addAriaLabel ?? 'Add'}
        >
          <MaterialIcon name="add" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />
        </IconButton>
      )}
    </Flex>
  );
}
