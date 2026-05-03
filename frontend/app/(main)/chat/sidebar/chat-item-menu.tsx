'use client';

import { useState } from 'react';
import { DropdownMenu, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';

interface ChatItemMenuProps {
  /** Whether the parent item is hovered (controls meatball visibility) */
  isParentHovered: boolean;
  /** Called when the dropdown open state changes (keep parent highlighted) */
  onOpenChange?: (open: boolean) => void;
  onRename: () => void;
  onArchive: () => void;
  onDelete: () => void;
  /** Agent-scoped sidebar: only delete is supported by the API */
  showRename?: boolean;
  showArchive?: boolean;
}

/**
 * Meatball (three-dot) dropdown menu for a chat sidebar item.
 *
 * Shows on hover of the parent row. Options: Rename, Archive, Delete.
 * Matches Figma spec (node 2496:29091).
 */
export function ChatItemMenu({
  isParentHovered,
  onOpenChange: onOpenChangeProp,
  onRename,
  onArchive,
  onDelete,
  showRename = true,
  showArchive = true,
}: ChatItemMenuProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [isMeatballHovered, setIsMeatballHovered] = useState(false);
  const visible = isParentHovered || isOpen;

  const handleOpenChange = (open: boolean) => {
    setIsOpen(open);
    onOpenChangeProp?.(open);
  };

  if (!visible) return null;

  return (
    <DropdownMenu.Root open={isOpen} onOpenChange={handleOpenChange} modal={false}>
      <DropdownMenu.Trigger>
        <button
          type="button"
          onMouseEnter={() => setIsMeatballHovered(true)}
          onMouseLeave={() => setIsMeatballHovered(false)}
          onClick={(e) => {
            e.stopPropagation();
          }}
          style={{
            appearance: 'none',
            border: 'none',
            background: isMeatballHovered || isOpen ? 'var(--olive-5)' : 'transparent',
            borderRadius: 'var(--radius-1)',
            padding: 2,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="more_horiz" size={18} color="var(--slate-11)" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content
        side="bottom"
        align="start"
        sideOffset={4}
        style={{ minWidth: 140 }}
      >
        {showRename && (
          <DropdownMenu.Item
            onClick={(e) => {
              e.stopPropagation();
              onRename();
            }}
          >
            <Flex align="center" gap="1">
              <MaterialIcon name="drive_file_rename_outline" size={16} color="var(--slate-11)" />
              <Text size="2" style={{ color: 'var(--slate-11)' }}>{t('chat.rename')}</Text>
            </Flex>
          </DropdownMenu.Item>
        )}
        {showArchive && (
          <DropdownMenu.Item
            onClick={(e) => {
              e.stopPropagation();
              onArchive();
            }}
          >
            <Flex align="center" gap="1">
              <MaterialIcon name="archive" size={16} color="var(--slate-11)" />
              <Text size="2" style={{ color: 'var(--slate-11)' }}>{t('chat.archive')}</Text>
            </Flex>
          </DropdownMenu.Item>
        )}
        <DropdownMenu.Item
          color="red"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          <Flex align="center" gap="1">
            <MaterialIcon name="delete" size={16} color="var(--red-11)" />
            <Text size="2" style={{ color: 'var(--red-11)' }}>{t('chat.deleteChat')}</Text>
          </Flex>
        </DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
