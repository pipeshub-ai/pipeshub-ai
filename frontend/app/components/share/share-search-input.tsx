'use client';

import React, { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { TEAM_SHARE_ROLES, toTeamShareRole, type ShareRole, type ShareRoleOption, type ShareSelection } from './types';
import { RoleDropdownMenu } from './role-dropdown-menu';

interface ShareSearchInputProps {
  /** Currently selected items (shown as chips) */
  selections: ShareSelection[];
  /** Current search query */
  searchQuery: string;
  /** Currently selected role for new shares */
  selectedRole: ShareRole;
  /** Whether to show the role picker on chips */
  supportsRoles: boolean;
  /** Callback when search query changes */
  onSearchChange: (query: string) => void;
  /** Callback when a selection is removed */
  onRemoveSelection: (id: string) => void;
  /** Accessible name of a chip's remove button. */
  removeSelectionLabel?: (name: string) => string;
  /** Callback when role changes */
  onRoleChange: (role: ShareRole) => void;
  /** Callback when a selected team's role changes */
  onTeamRoleChange?: (id: string, role: ShareRole) => void;
  /** Fires when any role dropdown opens or closes */
  onRoleDropdownOpenChange?: (open: boolean) => void;
  /** Callback to remove the last selection (backspace behaviour) */
  onRemoveLastSelection?: () => void;
  /** Callback when user presses Enter/comma with an email-like value */
  onEmailSubmit?: (email: string) => void;
  /** Levels offered on the pickers; defaults to the entity-agnostic lists. */
  roleOptions?: ShareRoleOption[];
  /** Accessible name for the input. */
  inputLabel?: string;
}

export function ShareSearchInput({
  selections,
  searchQuery,
  selectedRole,
  supportsRoles,
  onSearchChange,
  onRemoveSelection,
  removeSelectionLabel,
  onRoleChange,
  onTeamRoleChange,
  onRoleDropdownOpenChange,
  onRemoveLastSelection,
  onEmailSubmit,
  roleOptions,
  inputLabel,
}: ShareSearchInputProps) {
  const { t } = useTranslation();
  const optionRoles = roleOptions?.map((o) => o.role);
  const optionLabels = roleOptions
    ? Object.fromEntries(roleOptions.map((o) => [o.role, { label: o.label, description: o.description }]))
    : undefined;
  const inputRef = useRef<HTMLInputElement>(null);
  const roleAnchorRef = useRef<HTMLDivElement>(null);
  const hasUserSelection = selections.some((s) => s.type === 'user');
  // Chats pass roleOptions; their chips wrap so none is cut off. The KB and team drawers keep the single scrolling row.
  const wrapChips = Boolean(roleOptions);

  return (
    <Flex
      align="center"
      onClick={() => inputRef.current?.focus()}
      style={{
        ...(wrapChips ? { flexWrap: 'wrap' } : {}),
        border: '1px solid var(--slate-6)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--tokens-colors-surface)',
        padding: '4px',
        cursor: 'text',
        minHeight: 40,
        gap: 0,
      }}
    >
      {/* Chips + input: one scrolling row, or wrapped rows when wrapChips */}
      <Flex
        align="center"
        gap="2"
        className="no-scrollbar"
        data-testid="share-chips"
        data-wrap={wrapChips ? 'true' : undefined}
        style={{
          flex: wrapChips ? '1 1 18rem' : 1,
          minWidth: 0,
          overflowX: wrapChips ? 'visible' : 'auto',
          overflowY: wrapChips ? 'visible' : 'hidden',
          flexWrap: wrapChips ? 'wrap' : 'nowrap',
          paddingLeft: 8,
          paddingRight: 4,
        }}
      >
        {/* Selected chips */}
        {selections.map((selection) => (
          <Flex
            key={selection.id}
            align="center"
            gap="1"
            style={{
              backgroundColor: selection.isInvalid ? 'var(--red-a3)' : 'var(--accent-a3)',
              borderRadius: 'var(--radius-2)',
              padding: '4px 8px',
              flexShrink: 0,
              ...(wrapChips ? { maxWidth: '100%', minWidth: 0 } : {}),
            }}
          >
            <Text size="2" style={{ color: selection.isInvalid ? 'var(--red-11)' : 'var(--accent-11)', ...(wrapChips ? { overflowWrap: 'anywhere', minWidth: 0 } : { whiteSpace: 'nowrap' }) }}>
              {selection.email || selection.name}
            </Text>
            {selection.type === 'team' && supportsRoles && onTeamRoleChange && (
              <Box onClick={(e) => e.stopPropagation()}>
                <RoleDropdownMenu
                  role={toTeamShareRole(selection.role)}
                  roles={optionRoles ?? TEAM_SHARE_ROLES}
                  labels={optionLabels}
                  onRoleChange={(r) => onTeamRoleChange(selection.id, r)}
                  onOpenChange={onRoleDropdownOpenChange}
                />
              </Box>
            )}
            <button
              type="button"
              aria-label={removeSelectionLabel?.(selection.email || selection.name) ?? t('action.remove')}
              style={{
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                background: 'none',
                border: 'none',
                padding: 0,
              }}
              onClick={(e) => {
                e.stopPropagation();
                onRemoveSelection(selection.id);
              }}
            >
              <MaterialIcon name="close" size={14} color={selection.isInvalid ? 'var(--red-11)' : 'var(--accent-11)'} />
            </button>
          </Flex>
        ))}

        {/* Search input */}
        <input
          ref={inputRef}
          type="text"
          aria-label={inputLabel}
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Backspace' && searchQuery === '' && selections.length > 0) {
              onRemoveLastSelection?.();
            }
            if ((e.key === 'Enter' || e.key === ',') && searchQuery.includes('@') && searchQuery.trim().length > 0) {
              e.preventDefault();
              onEmailSubmit?.(searchQuery.trim());
              onSearchChange('');
            }
          }}
          placeholder={
            selections.length === 0
              ? 'Emails, teams or names (separated by commas)'
              : ''
          }
          style={{
            border: 'none',
            outline: 'none',
            flex: 1,
            minWidth: 120,
            fontSize: '14px',
            fontFamily: 'var(--default-font-family)',
            backgroundColor: 'transparent',
            color: 'var(--slate-12)',
            padding: 0,
          }}
        />
      </Flex>

      {/* Role picker for user selections; each team chip carries its own picker */}
      {hasUserSelection && supportsRoles && (
        <Box ref={roleAnchorRef} style={{ flexShrink: 0, paddingRight: 4 }}>
          <RoleDropdownMenu
            role={selectedRole}
            roles={optionRoles}
            labels={optionLabels}
            onRoleChange={onRoleChange}
            anchorRef={roleAnchorRef}
            onOpenChange={onRoleDropdownOpenChange}
          />
        </Box>
      )}
    </Flex>
  );
}
