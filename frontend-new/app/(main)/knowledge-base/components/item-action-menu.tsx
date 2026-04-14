'use client';

import React from 'react';
import { DropdownMenu, IconButton, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Types
// ========================================

export interface MenuAction {
  icon: string;
  label: string;
  onClick: () => void;
  /** Render this item in the given color (e.g. 'red' for destructive actions) */
  color?: 'red';
  /** Override icon color — defaults based on `color` prop */
  iconColor?: string;
  /** Render a separator line before this item */
  separatorBefore?: boolean;
}

export interface ItemActionMenuProps {
  /** List of actions to show in the dropdown. Falsy entries are filtered out. */
  actions: (MenuAction | false | null | undefined)[];
  /** Controlled open state */
  open?: boolean;
  /** Callback when open state changes */
  onOpenChange?: (open: boolean) => void;
}

// ========================================
// Component
// ========================================

/**
 * Shared meatball (three-dot) action menu used in both the sidebar
 * tree items and the main content list/grid views.
 *
 * The parent is responsible for positioning (e.g. absolute for sidebar,
 * inline for list rows). This component renders the trigger + dropdown.
 */
export function ItemActionMenu({ actions, open, onOpenChange }: ItemActionMenuProps) {
  const visibleActions = actions.filter(Boolean) as MenuAction[];
  if (visibleActions.length === 0) return null;

  return (
    <DropdownMenu.Root open={open} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger>
        <IconButton
          variant="ghost"
          size="1"
          color="gray"
          onClick={(e) => e.stopPropagation()}
          style={{ cursor: 'pointer' }}
        >
          <MaterialIcon name="more_horiz" size={16} color="var(--slate-11)" />
        </IconButton>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content
        size="1"
        style={{ minWidth: '120px' }}
        onClick={(e) => e.stopPropagation()}
      >
        {visibleActions.map((action, i) => (
          <React.Fragment key={i}>
            {action.separatorBefore && <DropdownMenu.Separator />}
            <DropdownMenu.Item
              color={action.color}
              style={{ marginBottom: i < visibleActions.length - 1 ? '4px' : '0' }}
              onClick={(e) => {
                e.stopPropagation();
                action.onClick();
              }}
            >
              <Flex align="center" gap="2">
                <MaterialIcon
                  name={action.icon}
                  size={16}
                  color={
                    action.iconColor ||
                    (action.color === 'red' ? 'var(--red-9)' : 'var(--slate-11)')
                  }
                />
                <Text size="2">{action.label}</Text>
              </Flex>
            </DropdownMenu.Item>
          </React.Fragment>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
