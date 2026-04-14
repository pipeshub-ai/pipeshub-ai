'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Text, Flex, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ShareRole } from './types';
import { SHARE_ROLE_LABELS } from './types';

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
   * When provided, the dropdown will use fixed positioning aligned to
   * this element's edges (used for search-bar alignment).
   */
  anchorRef?: React.RefObject<HTMLElement | null>;
}

const SELECTABLE_ROLES: ShareRole[] = ['OWNER', 'WRITER', 'READER'];

export function RoleDropdownMenu({ role, onRoleChange, onRemove, isTeam = false, noRolesInfo, anchorRef }: RoleDropdownMenuProps) {
  // Treat as no-roles when isTeam or noRolesInfo is provided
  const isNoRoles = isTeam || !!noRolesInfo;
  const noRolesTitle = noRolesInfo?.title ?? 'Team';
  const noRolesDescription = noRolesInfo?.description ?? 'Teams do not have roles';
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [anchorRect, setAnchorRect] = useState<{ top: number; left: number; width: number } | null>(null);

  // Close on outside click
  const handleClickOutside = useCallback((e: MouseEvent) => {
    if (
      menuRef.current &&
      !menuRef.current.contains(e.target as Node) &&
      triggerRef.current &&
      !triggerRef.current.contains(e.target as Node)
    ) {
      setOpen(false);
    }
  }, []);

  // Close on Escape
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') setOpen(false);
  }, []);

  useEffect(() => {
    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open, handleClickOutside, handleKeyDown]);

  // Calculate anchor position when opening with anchorRef
  useEffect(() => {
    if (open && anchorRef?.current) {
      const rect = anchorRef.current.getBoundingClientRect();
      setAnchorRect({ top: rect.bottom + 4, left: rect.left, width: rect.width });
    } else if (!open) {
      setAnchorRect(null);
    }
  }, [open, anchorRef]);

  const useFixedPosition = !!anchorRef && !!anchorRect;

  const menuPositionStyle: React.CSSProperties = useFixedPosition
    ? {
        position: 'fixed',
        top: anchorRect.top,
        left: anchorRect.left,
        width: anchorRect.width,
      }
    : {
        position: 'absolute',
        top: 'calc(100% + 4px)',
        right: 0,
        width: 320,
      };

  return (
    <Box style={{ position: 'relative' }}>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          height: 24,
          padding: '0 8px',
          borderRadius: 'var(--radius-1)',
          backgroundColor: 'var(--slate-a4)',
          border: 'none',
          cursor: 'pointer',
          fontFamily: 'var(--default-font-family)',
          fontSize: 12,
          fontWeight: 400,
          lineHeight: '16px',
          letterSpacing: '0.04px',
          color: 'var(--slate-11)',
          flexShrink: 0,
          userSelect: 'none',
        }}
      >
        {isNoRoles ? noRolesTitle : SHARE_ROLE_LABELS[role].label}
        <MaterialIcon name="expand_more" size={16} color="var(--slate-11)" />
      </button>

      {/* Dropdown popover */}
      {open && (
        <Box
          ref={menuRef}
          style={{
            ...menuPositionStyle,
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            boxShadow:
              '0px 12px 32px -16px rgba(0, 9, 50, 0.12), 0px 12px 60px 0px rgba(0, 0, 0, 0.15)',
            padding: 0,
            overflow: 'hidden',
            zIndex: 100,
          }}
        >
          {isNoRoles ? (
            /* No-roles mode: show informational label only */
            <Flex
              direction="column"
              style={{ padding: '12px 16px', paddingBottom: onRemove ? 8 : 12 }}
            >
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {noRolesTitle}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                {noRolesDescription}
              </Text>
            </Flex>
          ) : (
            SELECTABLE_ROLES.map((r, index) => (
            <Flex
              key={r}
              align="center"
              justify="between"
              onClick={() => {
                onRoleChange?.(r);
                setOpen(false);
              }}
              style={{
                padding: '8px 16px',
                paddingTop: index === 0 ? 12 : 8,
                paddingBottom:
                  index === SELECTABLE_ROLES.length - 1 && !onRemove ? 12 : 8,
                cursor: 'pointer',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--slate-a3)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent';
              }}
            >
              {/* Label + description */}
              <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
                <Text
                  size="2"
                  weight={r === role ? 'medium' : 'regular'}
                  style={{ color: 'var(--slate-12)' }}
                >
                  {SHARE_ROLE_LABELS[r].label}
                </Text>
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {SHARE_ROLE_LABELS[r].description}
                </Text>
              </Flex>

              {/* Radio indicator */}
              <Box
                style={{
                  width: 16,
                  height: 16,
                  borderRadius: '50%',
                  border: `2px solid ${r === role ? 'var(--accent-9)' : 'var(--slate-7)'}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                {r === role && (
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
            ))
          )}

          {/* Remove access option */}
          {onRemove && (
            <>
              <Box
                style={{
                  height: 1,
                  backgroundColor: 'var(--olive-3)',
                }}
              />
              <Flex
                align="center"
                onClick={() => {
                  onRemove();
                  setOpen(false);
                }}
                style={{
                  padding: '12px 16px',
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--slate-a3)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent';
                }}
              >
                <Text size="2" style={{ color: 'var(--red-11)' }}>
                  Remove
                </Text>
              </Flex>
            </>
          )}
        </Box>
      )}
    </Box>
  );
}
