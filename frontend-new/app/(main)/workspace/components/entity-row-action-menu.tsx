'use client';

import React, { useState } from 'react';
import { IconButton, Flex, Text, Box, Popover } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ── Sub-menu radio option ──

export interface SubMenuRadioOption {
  /** Option value */
  value: string;
  /** Display label */
  label: string;
  /** Optional description shown below the label */
  description?: string;
}

// ── Sub-menu configuration ──

export interface SubMenuConfig {
  type: 'radio';
  /** Currently-selected value (controls which radio is checked) */
  value: string;
  /** Called when the user picks a new value */
  onValueChange: (value: string) => void;
  /** Radio options */
  options: SubMenuRadioOption[];
}

export interface RowAction {
  /** Material icon name */
  icon: string;
  /** Action label */
  label: string;
  /** Callback when action is clicked (omit when using subMenu) */
  onClick?: () => void;
  /** Visual variant (danger renders in red) */
  variant?: 'default' | 'danger';
  /** Render a separator line before this item */
  separatorBefore?: boolean;
  /**
   * Optional sub-menu config. When present, clicking this action toggles
   * a role-picker panel (styled like the SelectDropdown in Invite User)
   * which replaces the action card — the action card is hidden while the sub-menu is open.
   */
  subMenu?: SubMenuConfig;
}

export interface EntityRowActionMenuProps {
  /** Array of actions — falsy entries are filtered out */
  actions: (RowAction | false | null | undefined)[];
}

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
              onClick={(e) => {
                e.stopPropagation();
                if (action.subMenu) {
                  onActionClick(action);
                } else {
                  action.onClick?.();
                  onClose();
                }
              }}
              onMouseEnter={(e) => {
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
                cursor: 'pointer',
                ...(isDanger
                  ? {
                      backgroundColor: 'var(--red-a3)',
                      color: 'var(--red-11)',
                    }
                  : {}),
              }}
            >
              <MaterialIcon
                name={action.icon}
                size={16}
                color={isDanger ? 'var(--red-11)' : 'var(--slate-11)'}
              />
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
  const [showRolePicker, setShowRolePicker] = useState(false);

  const visibleActions = actions.filter(Boolean) as RowAction[];
  if (visibleActions.length === 0) return null;

  const subMenuAction = visibleActions.find((a) => a.subMenu);

  const handleOpenChange = (isOpen: boolean) => {
    setOpen(isOpen);
    if (!isOpen) setShowRolePicker(false);
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
          // Transparent shell — each inner card has its own styling
          padding: 0,
          backgroundColor: 'transparent',
          border: 'none',
          boxShadow: 'none',
        }}
      >
        <Flex direction="column" gap="1">
          {/* ── Card 1: Action items (hidden when role picker is open) ── */}
          {!showRolePicker && (
            <ActionCard
              actions={visibleActions}
              onActionClick={() => setShowRolePicker((v) => !v)}
              onClose={() => handleOpenChange(false)}
            />
          )}

          {/* ── Card 2: Role picker (visible when "Change Role" is clicked) ── */}
          {showRolePicker && subMenuAction?.subMenu && (
            <RolePickerCard
              subMenu={subMenuAction.subMenu}
              onClose={() => handleOpenChange(false)}
            />
          )}
        </Flex>
      </Popover.Content>
    </Popover.Root>
  );
}
