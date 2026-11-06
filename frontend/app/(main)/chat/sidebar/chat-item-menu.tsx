'use client';

import { useState } from 'react';
import { DropdownMenu, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { IconMenuItem } from '@/app/components/ui/icon-menu-item';
import { useTranslation } from 'react-i18next';
import { useCanHover } from '@/lib/hooks/use-can-hover';

interface ChatItemMenuProps {
  /** The chat's title, for the trigger's name ("Actions for <title>"). */
  title: string;
  /** Whether the parent item is hovered or holds keyboard focus (controls meatball visibility) */
  isParentHovered: boolean;
  /** Called when the dropdown open state changes (keep parent highlighted) */
  onOpenChange?: (open: boolean) => void;
  onRename: () => void;
  onArchive: () => void;
  onDelete: () => void;
  /** Saves the whole chat as a Markdown file. Omit to hide the option. */
  onExport?: () => void;
  /** Pins the chat, or unpins it when `isPinned`. Omit to hide the option. */
  onTogglePin?: () => void;
  isPinned?: boolean;
  /** Agent-scoped sidebar: only delete is supported by the API */
  showRename?: boolean;
  showArchive?: boolean;
  /** Opens the move-to-project picker. Omit to hide the option entirely. */
  onMoveToProject?: () => void;
  /** Unlinks the conversation from its current project. Only shown when the row is already linked. */
  onRemoveFromProject?: () => void;
}

/** The 20px square of a row's ⋯ trigger: Radix's size 1 box, its ghost margin cleared. */
export const ROW_TRIGGER_STYLE = {
  width: 'calc(var(--space-5) - var(--space-1))',
  height: 'calc(var(--space-5) - var(--space-1))',
  padding: 0,
  margin: 0,
  flexShrink: 0,
  borderRadius: 'var(--radius-1)',
} as const;

/**
 * The ⋯ menu of your own chat in the sidebar. Each item leads with its glyph, and the one action
 * that cannot be undone comes last, in red, after a line: Rename, Pin, Export, Move to project,
 * Remove from project, Archive, then Delete.
 *
 * The trigger stays in place and fades in on row hover, on keyboard focus in the row and while
 * its menu is open, so the title never reflows. A screen with no hover (touch) always shows it,
 * since a tap on the row would only open the chat.
 */
export function ChatItemMenu({
  title,
  isParentHovered,
  onOpenChange: onOpenChangeProp,
  onRename,
  onArchive,
  onDelete,
  onExport,
  onTogglePin,
  isPinned = false,
  showRename = true,
  showArchive = true,
  onMoveToProject,
  onRemoveFromProject,
}: ChatItemMenuProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const canHover = useCanHover();
  const visible = isParentHovered || isOpen || !canHover;

  const handleOpenChange = (open: boolean) => {
    setIsOpen(open);
    onOpenChangeProp?.(open);
  };

  // The list is portalled, but React still bubbles its clicks to the row's link: each item stops its own.
  const item = (icon: string, label: string, onPick: () => void, danger = false) => (
    <IconMenuItem
      icon={icon}
      color={danger ? 'red' : undefined}
      onClick={(e) => {
        e.stopPropagation();
        onPick();
      }}
    >
      {label}
    </IconMenuItem>
  );

  return (
    <DropdownMenu.Root open={isOpen} onOpenChange={handleOpenChange} modal={false}>
      <DropdownMenu.Trigger>
        <IconButton
          type="button"
          className="touch-hit"
          variant="ghost"
          color="gray"
          size="1"
          aria-label={t('chat.rowActions', { title })}
          onClick={(e) => {
            e.stopPropagation();
          }}
          style={{
            ...ROW_TRIGGER_STYLE,
            opacity: visible ? 1 : 0,
            transition: 'opacity var(--motion-micro) var(--motion-ease)',
          }}
        >
          <MaterialIcon name="more_horiz" size={16} color="var(--text-secondary)" />
        </IconButton>
      </DropdownMenu.Trigger>
      {/* A finger's 44px tap area reaches 12px past the 20px ⋯: on touch the list opens clear of it, so the lift of the tap
          that opened it cannot land on Rename. */}
      <DropdownMenu.Content side="bottom" align="end" sideOffset={canHover ? 4 : 14} style={{ minWidth: 140 }}>
        {showRename && item('drive_file_rename_outline', t('chat.rename'), onRename)}
        {onTogglePin && item(isPinned ? 'keep_off' : 'keep', isPinned ? t('chat.unpin') : t('chat.pin'), onTogglePin)}
        {onExport && item('download', t('chat.export.menuItem'), onExport)}
        {onMoveToProject && item('drive_file_move', t('chat.projects.moveToProject'), onMoveToProject)}
        {onRemoveFromProject && item('folder_off', t('chat.projects.removeFromProject'), onRemoveFromProject)}
        {showArchive && item('archive', t('chat.archive'), onArchive)}
        <DropdownMenu.Separator />
        {item('delete', t('chat.deleteChat'), onDelete, true)}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
