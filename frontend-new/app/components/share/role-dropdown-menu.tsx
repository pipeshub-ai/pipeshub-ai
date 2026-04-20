'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
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
  /**
   * Fires when the dropdown open state changes. Parents can use this to
   * gate outside-click handling on portaled elements.
   */
  onOpenChange?: (open: boolean) => void;
}

const SELECTABLE_ROLES: ShareRole[] = ['OWNER', 'WRITER', 'READER'];
const MENU_WIDTH = 292;
const MIN_HORIZONTAL_MARGIN = 8;
const MIN_VERTICAL_MARGIN = 8;

export function RoleDropdownMenu({ role, onRoleChange, onRemove, isTeam = false, noRolesInfo, anchorRef, onOpenChange }: RoleDropdownMenuProps) {
  // Treat as no-roles when isTeam or noRolesInfo is provided
  const isNoRoles = isTeam || !!noRolesInfo;
  const noRolesTitle = noRolesInfo?.title ?? 'Team';
  const noRolesDescription = noRolesInfo?.description ?? 'Teams do not have roles';
  const [open, setOpenState] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [anchorRect, setAnchorRect] = useState<{ top: number; left: number; width: number } | null>(null);

  const setOpen = useCallback((next: boolean | ((prev: boolean) => boolean)) => {
    setOpenState((prev) =>
      typeof next === 'function' ? (next as (p: boolean) => boolean)(prev) : next
    );
  }, []);

  useEffect(() => {
    onOpenChange?.(open);
  }, [open, onOpenChange]);

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
  }, [setOpen]);

  // Close on Escape
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') setOpen(false);
  }, [setOpen]);

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

  // Calculate fixed position from trigger (or anchorRef) using viewport coordinates.
  // Re-measures on scroll/resize so the dropdown stays anchored to its trigger.
  useEffect(() => {
    if (!open) {
      setAnchorRect(null);
      return;
    }
    const el = anchorRef?.current ?? triggerRef.current;
    if (!el) return;

    const update = () => {
      const rect = el.getBoundingClientRect();
      const dropdownWidth = MENU_WIDTH;
      const left = Math.min(
        window.innerWidth - dropdownWidth - MIN_HORIZONTAL_MARGIN,
        Math.max(MIN_HORIZONTAL_MARGIN, rect.right - dropdownWidth)
      );
      const estimatedMenuHeight = onRemove ? 220 : 190;
      const measuredMenuHeight = menuRef.current?.offsetHeight || estimatedMenuHeight;
      const preferredTop = rect.bottom + 4;
      const maxTop = window.innerHeight - measuredMenuHeight - MIN_VERTICAL_MARGIN;
      let top = preferredTop;

      // If opening downward would overflow viewport, open upward and keep attached
      // to the trigger. Fallback to clamped viewport bounds when necessary.
      if (preferredTop > maxTop) {
        const upwardTop = rect.top - measuredMenuHeight - 4;
        top =
          upwardTop >= MIN_VERTICAL_MARGIN
            ? upwardTop
            : Math.max(MIN_VERTICAL_MARGIN, maxTop);
      }
      setAnchorRect({
        top,
        left,
        width: dropdownWidth,
      });
    };

    update();
    // Re-measure after the menu mounts so we use real height, not only estimate.
    const rafId = requestAnimationFrame(update);
    const ro = new ResizeObserver(update);
    ro.observe(el);
    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    return () => {
      cancelAnimationFrame(rafId);
      ro.disconnect();
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
    };
  }, [open, anchorRef, onRemove]);

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
        {isNoRoles ? noRolesTitle : SHARE_ROLE_LABELS[role].label}
        <MaterialIcon name="expand_more" size={16} color="var(--slate-11)" />
      </button>

      {/* Dropdown popover — portaled to body to escape Dialog overflow clipping */}
      {open && anchorRect && createPortal(
        <Box
          ref={menuRef}
          style={{
            position: 'fixed',
            top: anchorRect.top,
            left: anchorRect.left,
            width: anchorRect.width,
            maxHeight: 'min(320px, calc(100vh - 16px))',
            pointerEvents: 'auto',
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            boxShadow:
              '0px 12px 32px -16px rgba(0, 9, 50, 0.12), 0px 12px 60px 0px rgba(0, 0, 0, 0.15)',
            padding: 0,
            overflowY: 'auto',
            overflowX: 'hidden',
            zIndex: 9999,
          }}
        >
          {isNoRoles ? (
            /* No-roles mode: show informational label only */
            <Flex
              direction="column"
              style={{ padding: '10px 12px', paddingBottom: onRemove ? 8 : 10 }}
            >
              <Text size="1" weight="medium" style={{ color: 'var(--slate-12)', fontSize: 13, lineHeight: '16px' }}>
                {noRolesTitle}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-11)', fontSize: 12, lineHeight: '16px' }}>
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
                padding: '6px 12px',
                paddingTop: index === 0 ? 8 : 6,
                paddingBottom:
                  index === SELECTABLE_ROLES.length - 1 && !onRemove ? 8 : 6,
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
                  size="1"
                  weight={r === role ? 'medium' : 'regular'}
                  style={{ color: 'var(--slate-12)', fontSize: 13, lineHeight: '16px' }}
                >
                  {SHARE_ROLE_LABELS[r].label}
                </Text>
                <Text size="1" style={{ color: 'var(--slate-11)', fontSize: 12, lineHeight: '15px' }}>
                  {SHARE_ROLE_LABELS[r].description}
                </Text>
              </Flex>

              {/* Radio indicator */}
              <Box
                style={{
                  width: 14,
                  height: 14,
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
                      width: 6,
                      height: 6,
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
                  padding: '10px 12px',
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--slate-a3)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent';
                }}
              >
                <Text size="1" style={{ color: 'var(--red-11)', fontSize: 13, lineHeight: '16px' }}>
                  Remove
                </Text>
              </Flex>
            </>
          )}
        </Box>,
        document.body
      )}
    </Box>
  );
}
