'use client';

import React, { useState } from 'react';
import { IconButton, Flex, Text, Box, Popover } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

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
  onClick?: () => void;
  variant?: 'default' | 'danger';
  separatorBefore?: boolean;
  subMenu?: SubMenuConfig;
}

export interface EntityRowActionMenuProps {
  actions: (RowAction | false | null | undefined)[];
}

type View = 'actions' | 'rolePicker';

// ────────────────────────────────────────────────────────────────
// EntityRowActionMenu
//
// Single Popover with two views that swap:
//   'actions'    — compact action list (default)
//   'rolePicker' — role radio options (after "Change Role" click)
//
// Only one panel is ever visible at a time.
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
              gap: 'var(--space-2)',
            }}
          >
            {visibleActions.map((action, i) => {
              const isDanger = action.variant === 'danger';
              return (
                <Flex
                  key={i}
                  align="center"
                  gap="1"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (action.subMenu) {
                      setView('rolePicker');
                    } else {
                      action.onClick?.();
                      handleOpenChange(false);
                    }
                  }}
                  onMouseEnter={({ currentTarget }) => {
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
                    cursor: 'pointer',
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
                const subMenu = subMenuAction.subMenu;
                const isSelected = option.value === subMenu.value;
                const isLast = index === subMenu.options.length - 1;

                return (
                  <Flex
                    key={option.value}
                    align="center"
                    justify="between"
                    onClick={() => {
                      subMenu.onValueChange(option.value);
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
                      padding: '8px 16px',
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
