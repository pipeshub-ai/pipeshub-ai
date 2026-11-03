'use client';

import React, { useState } from 'react';
import { DropdownMenu, IconButton, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

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
  /** Render the item in a disabled state (not interactive). */
  disabled?: boolean;
}

export interface ItemActionMenuProps {
  /** List of actions to show in the dropdown. Falsy entries are filtered out. */
  actions: (MenuAction | false | null | undefined)[];
  /** Controlled open state */
  open?: boolean;
  /** Callback when open state changes */
  onOpenChange?: (open: boolean) => void;
  /** Accessible name of the trigger button, e.g. "More options for report.csv". */
  triggerLabel?: string;
  /** Trigger size; ghost buttons size by padding, so '4' is the one that reaches a 40px touch target. */
  triggerSize?: '1' | '2' | '3' | '4';
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
function isEditableElement(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLTextAreaElement) return true;
  if (el instanceof HTMLInputElement) return !['button', 'checkbox', 'radio'].includes(el.type);
  return el instanceof HTMLElement && el.isContentEditable;
}

export function ItemActionMenu({ actions, open, onOpenChange, triggerLabel, triggerSize = '1' }: ItemActionMenuProps) {
  const visibleActions = actions.filter(Boolean) as MenuAction[];
  // Phones get 44px rows, as the workspace row menus do.
  const isMobile = useIsMobile();
  // Which action is mid-flight (index), if any. Keeps the menu open and
  // shows a spinner while the promise settles.
  const [pendingIndex, setPendingIndex] = useState<number | null>(null);

  if (visibleActions.length === 0) return null;

  return (
    <DropdownMenu.Root open={open} onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger>
        <IconButton
          className="touch-hit"
          variant="ghost"
          size={triggerSize}
          color="gray"
          aria-label={triggerLabel}
          onClick={(e) => e.stopPropagation()}
          style={{
            cursor: 'pointer',
            ...(triggerSize === '1' && { width: 24, height: 24, minWidth: 24, padding: 0 }),
            margin: 0,
            borderRadius: 'var(--radius-1)',
          }}
        >
          <MaterialIcon name="more_horiz" size={16} color="var(--neutral-11)" />
        </IconButton>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content
        size="1"
        // Same rows, hover and colours as the settings tables' row menu (entity-row-action-menu)
        className="row-action-menu"
        style={{
          minWidth: isMobile ? 200 : 178,
          // The viewport already pads 4px (globals.css); a second 4 here doubles it.
          padding: 0,
          // Frosted surface comes from the shared .rt-BaseMenuContent rule in globals.css
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-1)',
          backdropFilter: 'var(--frost-filter)',
          WebkitBackdropFilter: 'var(--frost-filter)',
        }}
        onClick={(e) => e.stopPropagation()}
        onCloseAutoFocus={(e) => {
          // An action such as Rename focuses its own field while the menu is still
          // closing; handing focus back to the trigger would take it away again.
          if (isEditableElement(document.activeElement)) e.preventDefault();
        }}
      >
        {visibleActions.map((action, i) => {
          const iconColor =
            action.iconColor ||
            (action.color === 'red' ? 'var(--red-10)' : 'var(--slate-11)');
          const isPending = pendingIndex === i || action.isLoading === true;
          const anyPending = pendingIndex !== null;
          const isBlocked = isPending || (anyPending && pendingIndex !== i) || action.disabled === true;

          return (
            <React.Fragment key={i}>
              {action.separatorBefore && <DropdownMenu.Separator />}
              <DropdownMenu.Item
                color={action.color}
                disabled={isBlocked || undefined}
                style={{
                  height: isMobile ? 44 : 24,
                  padding: '0 8px',
                  borderRadius: 'var(--radius-1)',
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
                  <Text
                    size={isMobile ? '2' : '1'}
                    style={{
                      fontWeight: 400,
                      letterSpacing: '0.04px',
                      lineHeight: '16px',
                      color: action.color === 'red' ? 'var(--red-10)' : 'var(--slate-11)',
                    }}
                  >
                    {action.label}
                  </Text>
                </Flex>
              </DropdownMenu.Item>
            </React.Fragment>
          );
        })}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
