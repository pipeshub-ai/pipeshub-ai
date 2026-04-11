'use client';

import React, { useRef } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ShareRole, ShareSelection } from './types';
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
  /** Callback when role changes */
  onRoleChange: (role: ShareRole) => void;
  /** Callback to remove the last selection (backspace behaviour) */
  onRemoveLastSelection?: () => void;
  /** Callback when user presses Enter/comma with an email-like value */
  onEmailSubmit?: (email: string) => void;
}

export function ShareSearchInput({
  selections,
  searchQuery,
  selectedRole,
  supportsRoles,
  onSearchChange,
  onRemoveSelection,
  onRoleChange,
  onRemoveLastSelection,
  onEmailSubmit,
}: ShareSearchInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  return (
    <Flex
      ref={containerRef}
      align="center"
      onClick={() => inputRef.current?.focus()}
      style={{
        border: '1px solid var(--slate-6)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--tokens-colors-surface)',
        padding: '4px',
        cursor: 'text',
        minHeight: 40,
        gap: 0,
      }}
    >
      {/* Scrollable area for chips + input */}
      <Flex
        align="center"
        gap="2"
        className="no-scrollbar"
        style={{
          flex: 1,
          minWidth: 0,
          overflowX: 'auto',
          overflowY: 'hidden',
          flexWrap: 'nowrap',
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
            }}
          >
            <Text size="2" style={{ color: selection.isInvalid ? 'var(--red-11)' : 'var(--accent-11)', whiteSpace: 'nowrap' }}>
              {selection.email || selection.name}
            </Text>
            <Box
              style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}
              onClick={(e) => {
                e.stopPropagation();
                onRemoveSelection(selection.id);
              }}
            >
              <MaterialIcon name="close" size={14} color={selection.isInvalid ? 'var(--red-11)' : 'var(--accent-11)'} />
            </Box>
          </Flex>
        ))}

        {/* Search input */}
        <input
          ref={inputRef}
          type="text"
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

      {/* Role picker - fixed on the right (shown when items are selected and roles are supported) */}
      {selections.length > 0 && supportsRoles && (
        <Box style={{ flexShrink: 0, paddingRight: 4 }}>
          <RoleDropdownMenu
            role={selectedRole}
            onRoleChange={onRoleChange}
            anchorRef={containerRef}
          />
        </Box>
      )}
    </Flex>
  );
}
