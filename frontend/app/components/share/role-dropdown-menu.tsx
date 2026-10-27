'use client';

import { useTranslation } from 'react-i18next';
import React from 'react';
import { DropdownMenu, Text, Flex, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ShareRole } from './types';
import { getShareRoleLabels } from './types';

interface RoleDropdownMenuProps {
  role: ShareRole;
  onRoleChange?: (newRole: ShareRole) => void;
  onRemove?: () => void;
  /**
   * When true, suppresses role options and shows "Team" / "Teams do not have roles".
   */
  isTeam?: boolean;
  /**
   * When provided, suppresses role options and shows a custom title/description.
   * e.g. { title: 'Chat', description: 'Chats do not have roles' }
   */
  noRolesInfo?: { title: string; description: string };
  /**
   * Fires when the menu opens or closes. Parents can use this to gate
   * outside-click handling.
   */
  onOpenChange?: (open: boolean) => void;
  /**
   * Override the label + description for each role. Defaults to
   * {@link getShareRoleLabels}. Pass team-specific labels for team contexts.
   */
  labels?: Record<ShareRole, { label: string; description: string }>;
  /** Roles offered in the menu (default: owner, editor, viewer). */
  roles?: ShareRole[];
}

const SELECTABLE_ROLES: ShareRole[] = ['OWNER', 'WRITER', 'READER'];
const MENU_WIDTH = 292;

/**
 * Role picker. Built on the Radix Themes DropdownMenu so it nests correctly in the
 * Share dialog: it is its own focus scope and dismiss layer, so focus moves into it
 * on open, Arrow keys and Enter work, and Escape closes only the menu.
 */
export function RoleDropdownMenu({ role, onRoleChange, onRemove, isTeam = false, noRolesInfo, onOpenChange, labels, roles = SELECTABLE_ROLES }: RoleDropdownMenuProps) {
  const { t } = useTranslation();
  const effectiveLabels = labels ?? getShareRoleLabels(t);
  const isNoRoles = isTeam || !!noRolesInfo;
  const noRolesTitle = noRolesInfo?.title ?? t('shareSidebar.team');
  const noRolesDescription = noRolesInfo?.description ?? t('shareSidebar.teamsNoRoles');
  const triggerLabel = isNoRoles
    ? noRolesTitle
    : effectiveLabels[role]?.label ?? (typeof role === 'string' ? role : t('shareSidebar.roles.reader.label'));

  return (
    <DropdownMenu.Root onOpenChange={onOpenChange}>
      <DropdownMenu.Trigger>
        <button
          type="button"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            height: 28,
            padding: '0 8px',
            borderRadius: 'var(--radius-1)',
            backgroundColor: 'var(--slate-a4)',
            border: 'none',
            cursor: 'pointer',
            fontFamily: 'var(--default-font-family)',
            fontSize: 11,
            fontWeight: 400,
            lineHeight: '14px',
            letterSpacing: '0.02px',
            color: 'var(--slate-11)',
            flexShrink: 0,
            userSelect: 'none',
          }}
        >
          {triggerLabel}
          {/* The icon's ligature text must not become part of the button's name. */}
          <span aria-hidden="true" style={{ display: 'inline-flex' }}>
            <MaterialIcon name="expand_more" size={16} color="var(--slate-11)" />
          </span>
        </button>
      </DropdownMenu.Trigger>

      <DropdownMenu.Content
        align="end"
        size="1"
        // Soft highlight: olive-12 text (13.1:1) and olive-11 descriptions (4.8:1) on jade-a4, where the
        // solid accent gave white on jade-9 at 3.15:1. Radix labels the menu by its trigger, whose text
        // includes the icon ligature, so name it explicitly.
        variant="soft"
        aria-labelledby={undefined}
        aria-label={triggerLabel}
        style={{
          width: MENU_WIDTH,
          maxWidth: 'calc(100vw - 16px)',
          padding: 0,
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          zIndex: 9999,
        }}
      >
        {isNoRoles ? (
          <Flex direction="column" style={{ padding: '10px 12px' }}>
            <Text size="1" weight="medium" style={{ color: 'var(--slate-12)', fontSize: 13, lineHeight: '16px' }}>
              {noRolesTitle}
            </Text>
            <Text size="1" style={{ color: 'var(--slate-11)', fontSize: 12, lineHeight: '16px' }}>
              {noRolesDescription}
            </Text>
          </Flex>
        ) : (
          <DropdownMenu.RadioGroup value={role} onValueChange={(v) => onRoleChange?.(v as ShareRole)}>
            {roles.map((r) => (
              <DropdownMenu.RadioItem
                key={r}
                value={r}
                style={{ height: 'auto', minHeight: 44, padding: '6px 12px 6px 28px' }}
              >
                <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                  {/* No fixed colors: the highlighted item switches text to its contrasting color. */}
                  <Text
                    size="1"
                    weight={r === role ? 'medium' : 'regular'}
                    style={{ color: 'var(--olive-12)', fontSize: 13, lineHeight: '16px' }}
                  >
                    {effectiveLabels[r]?.label ?? r}
                  </Text>
                  <Text size="1" style={{ color: 'var(--olive-11)', fontSize: 12, lineHeight: '15px', whiteSpace: 'normal' }}>
                    {effectiveLabels[r]?.description ?? ''}
                  </Text>
                </Flex>
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        )}

        {onRemove && (
          <>
            <Box style={{ height: 1, backgroundColor: 'var(--olive-3)' }} />
            <DropdownMenu.Item
              color="red"
              onSelect={onRemove}
              // red-12 on red-a4 (9.3:1); the default red-11 is 3.9:1 there.
              style={{ height: 'auto', minHeight: 44, padding: '10px 12px', color: 'var(--red-12)' }}
            >
              {t('action.remove')}
            </DropdownMenu.Item>
          </>
        )}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
