'use client';

import { useState } from 'react';
import { DropdownMenu, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';

interface SharedChatItemMenuProps {
  isParentHovered: boolean;
  onOpenChange?: (open: boolean) => void;
  /** The row is archived for this user, so the entry is Unarchive. */
  isArchived?: boolean;
  onArchive: () => void;
  onUnarchive: () => void;
  onLeave: () => void;
}

/** Row menu for a chat the user does not own: per-user archive and leave. */
export function SharedChatItemMenu({
  isParentHovered,
  onOpenChange: onOpenChangeProp,
  isArchived = false,
  onArchive,
  onUnarchive,
  onLeave,
}: SharedChatItemMenuProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  const handleOpenChange = (open: boolean) => {
    setIsOpen(open);
    onOpenChangeProp?.(open);
  };

  if (!(isParentHovered || isOpen)) return null;

  return (
    <DropdownMenu.Root open={isOpen} onOpenChange={handleOpenChange} modal={false}>
      <DropdownMenu.Trigger>
        <button
          type="button"
          aria-label={t('chat.collab.sidebar.menu')}
          onClick={(e) => e.stopPropagation()}
          style={{
            appearance: 'none',
            border: 'none',
            background: isOpen ? 'var(--olive-5)' : 'transparent',
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
      <DropdownMenu.Content side="bottom" align="start" sideOffset={4} style={{ minWidth: 140 }}>
        <DropdownMenu.Item
          onClick={(e) => {
            e.stopPropagation();
            if (isArchived) onUnarchive();
            else onArchive();
          }}
        >
          <Flex align="center" gap="1">
            <MaterialIcon name={isArchived ? 'unarchive' : 'archive'} size={16} color="var(--slate-11)" />
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {isArchived ? t('chat.collab.sidebar.unarchive') : t('chat.collab.sidebar.archive')}
            </Text>
          </Flex>
        </DropdownMenu.Item>
        <DropdownMenu.Item
          color="red"
          onClick={(e) => {
            e.stopPropagation();
            onLeave();
          }}
        >
          <Flex align="center" gap="1">
            <MaterialIcon name="logout" size={16} color="var(--red-11)" />
            <Text size="2" style={{ color: 'var(--red-11)' }}>{t('chat.collab.sidebar.leave')}</Text>
          </Flex>
        </DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
