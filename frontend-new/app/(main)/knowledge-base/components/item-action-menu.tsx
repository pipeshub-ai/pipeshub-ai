'use client';

import React, { useState } from 'react';
import { DropdownMenu, IconButton, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';

// ========================================
// Types
// ========================================

export interface MenuAction {
  icon: string;
  label: string;
  /**
   * Click handler. May return a Promise — when it does, the menu item shows
   * a spinner and disables interaction until the promise settles (the menu
   * closes only after the action resolves).
   */
  onClick: () => void | Promise<void>;
  /** Render this item in the given color (e.g. 'red' for destructive actions) */
  color?: 'red';
  /** Override icon color — defaults based on `color` prop */
  iconColor?: string;
  /** Render a separator line before this item */
  separatorBefore?: boolean;
  /**
   * Externally-controlled loading state — use when the parent tracks
   * per-row loading in a store (the menu item is replaced by a spinner).
   */
  isLoading?: boolean;
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
  // Which action is mid-flight (index), if any. Keeps the menu open and
  // shows a spinner while the promise settles.
  const [pendingIndex, setPendingIndex] = useState<number | null>(null);

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
        {visibleActions.map((action, i) => {
          const iconColor =
            action.iconColor ||
            (action.color === 'red' ? 'var(--red-9)' : 'var(--slate-11)');
          const isPending = pendingIndex === i || action.isLoading === true;
          const anyPending = pendingIndex !== null;
          const isBlocked = isPending || (anyPending && pendingIndex !== i);

          return (
            <React.Fragment key={i}>
              {action.separatorBefore && <DropdownMenu.Separator />}
              <DropdownMenu.Item
                color={action.color}
                disabled={isBlocked || undefined}
                style={{
                  marginBottom: i < visibleActions.length - 1 ? '4px' : '0',
                  cursor: isPending ? 'wait' : isBlocked ? 'not-allowed' : 'pointer',
                  opacity: anyPending && pendingIndex !== i ? 0.55 : 1,
                }}
                onSelect={(e) => {
                  // Keep menu open while we run an async action — Radix
                  // closes on select by default; we re-open by setting
                  // pendingIndex (component-level), and only allow the
                  // parent `onOpenChange` to close it after settling.
                  if (isBlocked) {
                    e.preventDefault();
                    return;
                  }
                  const result = action.onClick();
                  if (result && typeof (result as Promise<void>).then === 'function') {
                    e.preventDefault();
                    setPendingIndex(i);
                    (result as Promise<void>).finally(() => {
                      setPendingIndex(null);
                      onOpenChange?.(false);
                    });
                  }
                }}
              >
                <Flex align="center" gap="2">
                  {isPending ? (
                    <Spinner size={14} color={iconColor} />
                  ) : (
                    <MaterialIcon name={action.icon} size={16} color={iconColor} />
                  )}
                  <Text size="2">{action.label}</Text>
                </Flex>
              </DropdownMenu.Item>
            </React.Fragment>
          );
        })}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
