'use client';

import React, { useState } from 'react';
import { IconButton, Flex, Text, Box, Popover, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';

// ── Sub-menu radio option ──

export interface SubMenuRadioOption {
  value: string;
  label: string;
  description?: string;
}

// ── Sub-menu configuration ──

export interface SubMenuConfig {
  type: 'radio';
  value: string;
  onValueChange: (value: string) => void;
  options: SubMenuRadioOption[];
}

export interface RowAction {
  icon: string;
  label: string;
  /**
   * Callback when the action is clicked (omit when using `subMenu`).
   *
   * May return a Promise. If it does, the popover stays open (showing a
   * spinner in the clicked row) until the promise settles — giving the
   * user visual feedback that the async operation is in flight.
   */
  onClick?: () => void | Promise<void>;
  /** Visual variant (danger renders in red) */
  variant?: 'default' | 'danger';
  separatorBefore?: boolean;
  /**
   * External loading flag. When true, the row displays a spinner and the
   * item cannot be clicked. Use this when the parent already tracks
   * per-row loading state in a store (e.g. knowledge-base operations).
   */
  isLoading?: boolean;
  /**
   * Optional sub-menu config. When present, clicking this action toggles
   * a role-picker panel (styled like the SelectDropdown in Invite User)
   * which replaces the action card — the action card is hidden while the sub-menu is open.
   */
  subMenu?: SubMenuConfig;
  /** When true the item is greyed out and not clickable. */
  disabled?: boolean;
  /** Tooltip shown on hover (useful when disabled to explain why). */
  tooltip?: string;
}

export interface EntityRowActionMenuProps {
  actions: (RowAction | false | null | undefined)[];
}

type View = 'actions' | 'rolePicker';
// ────────────────────────────────────────────────────────────────
// RolePickerCard
//
// Radio-option list matching the SelectDropdown styling used in
// the "Invite User" panel's "Assign Role" dropdown.
// ────────────────────────────────────────────────────────────────

function RolePickerCard({
  subMenu,
  onClose,
}: {
  subMenu: SubMenuConfig;
  onClose: () => void;
}) {
  return (
    <Box
      style={{
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        boxShadow:
          '0px 12px 32px -16px rgba(0, 9, 50, 0.12), 0px 12px 60px 0px rgba(0, 0, 0, 0.15)',
        overflow: 'hidden',
      }}
    >
      {subMenu.options.map((option, index) => {
        const isSelected = option.value === subMenu.value;
        const isFirst = index === 0;
        const isLast = index === subMenu.options.length - 1;

        return (
          <Flex
            key={option.value}
            align="center"
            justify="between"
            onClick={() => {
              subMenu.onValueChange(option.value);
              onClose();
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.backgroundColor =
                'var(--slate-a3)';
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.backgroundColor =
                'transparent';
            }}
            style={{
              padding: '8px 16px',
              paddingTop: isFirst ? 12 : 8,
              paddingBottom: isLast ? 12 : 8,
              cursor: 'pointer',
            }}
          >
            <Flex direction="column" gap="1">
              <Text
                size="2"
                weight={isSelected ? 'medium' : 'regular'}
                style={{ color: 'var(--slate-12)' }}
              >
                {option.label}
              </Text>
              {option.description && (
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {option.description}
                </Text>
              )}
            </Flex>

            {/* Radio indicator — matches SelectDropdown styling identically */}
            <Box
              style={{
                width: 16,
                height: 16,
                borderRadius: '50%',
                border: `2px solid ${isSelected ? 'var(--accent-9)' : 'var(--slate-a7)'}`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              {isSelected && (
                <Box
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    backgroundColor: 'var(--accent-9)',
                  }}
                />
              )}
            </Box>
          </Flex>
        );
      })}
    </Box>
  );
}

// ────────────────────────────────────────────────────────────────
// ActionCard
//
// Compact action list (View Profile, Change Role, Deactivate, …).
// Each item is a clickable row with icon + label.
// ────────────────────────────────────────────────────────────────

function ActionCard({
  actions,
  onActionClick,
  onClose,
}: {
  actions: RowAction[];
  /** Called when a subMenu action is clicked (toggles role picker) */
  onActionClick: (action: RowAction) => void;
  /** Called to close the entire popover */
  onClose: () => void;
}) {
  // Index of the currently-running async action (if any). While set, the row
  // shows a spinner and the popover stays open until the promise settles.
  const [pendingIndex, setPendingIndex] = useState<number | null>(null);

  return (
    <Box
      style={{
        marginLeft: 'auto', // right-align when role picker widens the container
        minWidth: 120,
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 4,
      }}
    >
      {actions.map((action, i) => {
        const isDanger = action.variant === 'danger';
        const isPending = pendingIndex === i || action.isLoading === true;
        const anyPending = pendingIndex !== null;
        const iconColor = isDanger ? 'var(--red-11)' : 'var(--slate-11)';

        const handleClick = (e: React.MouseEvent) => {
          e.stopPropagation();
          if (isPending || (anyPending && pendingIndex !== i)) return;
          if (action.subMenu) {
            onActionClick(action);
            return;
          }
          const maybePromise = action.onClick?.();
          if (
            maybePromise &&
            typeof (maybePromise as Promise<void>).then === 'function'
          ) {
            setPendingIndex(i);
            (maybePromise as Promise<void>).finally(() => {
              setPendingIndex(null);
              onClose();
            });
          } else {
            onClose();
          }
        };

        return (
          <React.Fragment key={i}>
            {action.separatorBefore && (
              <Box
                style={{
                  height: 1,
                  backgroundColor: 'var(--olive-4)',
                  margin: '4px 0',
                }}
              />
            )}
            <Flex
              align="center"
              gap="2"
              aria-busy={isPending || undefined}
              aria-disabled={isPending || (anyPending && pendingIndex !== i) || undefined}
              onClick={handleClick}
              onMouseEnter={(e) => {
                if (isPending) return;
                (e.currentTarget as HTMLElement).style.backgroundColor =
                  isDanger ? 'var(--red-a4)' : 'var(--slate-a3)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.backgroundColor =
                  isDanger ? 'var(--red-a3)' : 'transparent';
              }}
              style={{
                height: 24,
                padding: '0 8px',
                borderRadius: 'var(--radius-1)',
                cursor: isPending
                  ? 'wait'
                  : anyPending && pendingIndex !== i
                    ? 'not-allowed'
                    : 'pointer',
                opacity: anyPending && pendingIndex !== i ? 0.55 : 1,
                ...(isDanger
                  ? {
                      backgroundColor: 'var(--red-a3)',
                      color: 'var(--red-11)',
                    }
                  : {}),
              }}
            >
              {isPending ? (
                <Spinner size={14} color={iconColor} />
              ) : (
                <MaterialIcon name={action.icon} size={16} color={iconColor} />
              )}
              <Text size="2">{action.label}</Text>
            </Flex>
          </React.Fragment>
        );
      })}
    </Box>
  );
}

// ────────────────────────────────────────────────────────────────
// EntityRowActionMenu
//
// Single Popover that switches between two visual "cards":
//   1. Action card   — compact list (View Profile, Change Role, …)
//   2. Role picker   — radio options (Admin / Member / Guest)
//
// Only one card is visible at a time: the action card is hidden when
// "Change Role" is clicked and the role picker takes its place.
// The Popover.Content itself is transparent — each card has its own
// background / border / shadow so they look like separate floating
// panels, right-aligned to the ⋯ trigger.
//
// Collision-aware: Radix Popover flips top ↔ bottom automatically.
// ────────────────────────────────────────────────────────────────

export function EntityRowActionMenu({ actions }: EntityRowActionMenuProps) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>('actions');

  const visibleActions = actions.filter(Boolean) as RowAction[];
  if (visibleActions.length === 0) return null;

  const subMenuAction = visibleActions.find((a) => a.subMenu);

  const handleOpenChange = (isOpen: boolean) => {
    setOpen(isOpen);
    if (!isOpen) setView('actions');
  };

  return (
    <Popover.Root open={open} onOpenChange={handleOpenChange}>
      <Popover.Trigger>
        <IconButton
          variant="ghost"
          size="1"
          color="gray"
          onClick={(e) => e.stopPropagation()}
          style={{ cursor: 'pointer' }}
        >
          <MaterialIcon name="more_horiz" size={16} color="var(--slate-11)" />
        </IconButton>
      </Popover.Trigger>

      <Popover.Content
        side="bottom"
        align="end"
        sideOffset={4}
        onClick={(e) => e.stopPropagation()}
        style={{
          padding: 0,
          backgroundColor: 'transparent',
          border: 'none',
          boxShadow: 'none',
        }}
      >
        {view === 'actions' ? (
          /* ── Action list ── */
          <Box
            style={{
              minWidth: 178,
              backgroundColor: 'var(--olive-2)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-1)',
              padding: 4,
              backdropFilter: 'blur(25px)',
              display: 'flex',
              flexDirection: 'column',
              gap: 2,
            }}
          >
            {visibleActions.map((action, i) => {
              const isDanger = action.variant === 'danger';
              const isDisabled = action.disabled === true;

              const row = (
                <Flex
                  align="center"
                  gap="1"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (isDisabled) return;
                    if (action.subMenu) {
                      setView('rolePicker');
                    } else {
                      action.onClick?.();
                      handleOpenChange(false);
                    }
                  }}
                  onMouseEnter={({ currentTarget }) => {
                    if (isDisabled) return;
                    (currentTarget as HTMLElement).style.backgroundColor =
                      isDanger ? 'var(--red-a3)' : 'var(--slate-a3)';
                  }}
                  onMouseLeave={({ currentTarget }) => {
                    (currentTarget as HTMLElement).style.backgroundColor =
                      'transparent';
                  }}
                  style={{
                    height: 24,
                    padding: '0 8px',
                    borderRadius: 'var(--radius-1)',
                    cursor: isDisabled ? 'not-allowed' : 'pointer',
                    opacity: isDisabled ? 0.45 : 1,
                  }}
                >
                  <MaterialIcon
                    name={action.icon}
                    size={16}
                    color={isDanger ? 'var(--red-10)' : 'var(--slate-11)'}
                  />
                  <Text
                    size="1"
                    style={{
                      color: isDanger ? 'var(--red-10)' : 'var(--slate-11)',
                    }}
                  >
                    {action.label}
                  </Text>
                </Flex>
              );

              return action.tooltip ? (
                <Tooltip key={i} content={action.tooltip} side="left">
                  {row}
                </Tooltip>
              ) : (
                <React.Fragment key={i}>{row}</React.Fragment>
              );
            })}
          </Box>
        ) : (
          /* ── Role picker ── */
          subMenuAction?.subMenu && (
            <Box
              style={{
                minWidth: 178,
                backgroundColor: 'var(--olive-2)',
                border: '1px solid var(--olive-3)',
                borderRadius: 'var(--radius-2)',
                boxShadow:
                  '0px 12px 32px -16px rgba(0, 9, 50, 0.12), 0px 12px 60px 0px rgba(0, 0, 0, 0.15)',
                overflow: 'hidden',
              }}
            >

              {/* Radio options */}
              {subMenuAction.subMenu.options.map((option, index) => {
                const isSelected = option.value === subMenuAction.subMenu!.value;
                const isLast =
                  index === subMenuAction.subMenu!.options.length - 1;

                return (
                  <Flex
                    key={option.value}
                    align="center"
                    justify="between"
                    onClick={() => {
                      subMenuAction.subMenu!.onValueChange(option.value);
                      handleOpenChange(false);
                    }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.backgroundColor =
                        'var(--slate-a3)';
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.backgroundColor =
                        'transparent';
                    }}
                    style={{
                      padding: 'var(--space-2) var(--space-4)',
                      paddingBottom: isLast ? 12 : 8,
                      cursor: 'pointer',
                    }}
                  >
                    <Flex direction="column" gap="1">
                      <Text
                        size="2"
                        weight={isSelected ? 'medium' : 'regular'}
                        style={{ color: 'var(--slate-12)' }}
                      >
                        {option.label}
                      </Text>
                      {option.description && (
                        <Text size="1" style={{ color: 'var(--slate-11)' }}>
                          {option.description}
                        </Text>
                      )}
                    </Flex>

                    <Box
                      style={{
                        width: 16,
                        height: 16,
                        borderRadius: '50%',
                        border: `2px solid ${isSelected ? 'var(--accent-9)' : 'var(--slate-a7)'}`,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                      }}
                    >
                      {isSelected && (
                        <Box
                          style={{
                            width: 8,
                            height: 8,
                            borderRadius: '50%',
                            backgroundColor: 'var(--accent-9)',
                          }}
                        />
                      )}
                    </Box>
                  </Flex>
                );
              })}
            </Box>
          )
        )}
      </Popover.Content>
    </Popover.Root>
  );
}
