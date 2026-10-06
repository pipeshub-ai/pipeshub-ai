'use client';

import { useTranslation } from 'react-i18next';
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Text, Flex, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ShareRole } from './types';
import { getShareRoleLabels } from './types';

interface RoleDropdownMenuProps {
  role: ShareRole;
  onRoleChange?: (newRole: ShareRole) => void;
  onRemove?: () => void;
  /**
   * When true, suppresses role options and shows "Team" / "Teams do not have roles".
   * Only for entities whose team grants carry no role; KB team rows pass roles instead.
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
  /**
   * Override the label + description for each role. Defaults to
   * {@link getShareRoleLabels}. Pass team-specific labels for team contexts.
   */
  labels?: Partial<Record<ShareRole, { label: string; description: string }>>;
  /** Roles offered in the menu. Defaults to the user roles. */
  roles?: readonly ShareRole[];
  /** Adds a "Make owner" item above Remove (ownership transfer). */
  onMakeOwner?: () => void;
}

const SELECTABLE_ROLES: readonly ShareRole[] = ['OWNER', 'WRITER', 'READER'];
const MENU_WIDTH = 292;

function onActivateKey(action: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      action();
    }
  };
}
const MIN_HORIZONTAL_MARGIN = 8;
const MIN_VERTICAL_MARGIN = 8;

/**
 * Where the menu mounts: inside the enclosing modal dialog when there is one. A modal dialog traps focus and
 * hides everything outside it from assistive tech, so a menu portaled to `body` could not be reached by keyboard.
 */
function portalHostOf(trigger: HTMLElement | null): HTMLElement | null {
  const dialog = trigger?.closest<HTMLElement>('[role="dialog"]');
  if (dialog && getComputedStyle(dialog).position !== 'static') return dialog;
  return null;
}

function menuItemsOf(menu: HTMLElement | null): HTMLElement[] {
  return Array.from(menu?.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"]') ?? []);
}

export function RoleDropdownMenu({ role, onRoleChange, onRemove, isTeam = false, noRolesInfo, anchorRef, onOpenChange, labels, roles = SELECTABLE_ROLES, onMakeOwner }: RoleDropdownMenuProps) {
  const { t } = useTranslation();
  const effectiveLabels = labels ?? getShareRoleLabels(t);
  const roleLabel =
    effectiveLabels[role]?.label ?? (typeof role === 'string' ? role : t('shareSidebar.roles.reader.label'));
  // Treat as no-roles when isTeam or noRolesInfo is provided
  const isNoRoles = isTeam || !!noRolesInfo;
  const noRolesTitle = noRolesInfo?.title ?? t('shareSidebar.team');
  const noRolesDescription = noRolesInfo?.description ?? t('shareSidebar.teamsNoRoles');
  const [open, setOpenState] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [anchorRect, setAnchorRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const [portalHost, setPortalHost] = useState<HTMLElement | null>(null);

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

  // Close on Escape and give focus back to the trigger.
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      setOpen(false);
      triggerRef.current?.focus();
    }
  }, [setOpen]);

  const handleMenuKeyDown = (e: React.KeyboardEvent) => {
    const items = menuItemsOf(menuRef.current);
    if (items.length === 0) return;
    const at = items.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    if (e.key === 'ArrowDown') next = at < 0 ? 0 : (at + 1) % items.length;
    else if (e.key === 'ArrowUp') next = at <= 0 ? items.length - 1 : at - 1;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = items.length - 1;
    else if (e.key === 'Tab') {
      setOpen(false);
      return;
    }
    if (next === null) return;
    e.preventDefault();
    items[next].focus();
  };

  const placed = anchorRect !== null;
  useEffect(() => {
    if (!open || !placed) return;
    const items = menuItemsOf(menuRef.current);
    (items.find((i) => i.getAttribute('aria-checked') === 'true') ?? items[0])?.focus();
  }, [open, placed]);

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
    const host = portalHostOf(triggerRef.current);
    setPortalHost(host);

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
      // Inside a dialog the menu is absolutely positioned against the dialog box.
      const hostRect = host?.getBoundingClientRect();
      const offsetTop = host && hostRect ? hostRect.top + host.clientTop : 0;
      const offsetLeft = host && hostRect ? hostRect.left + host.clientLeft : 0;
      setAnchorRect({
        top: top - offsetTop,
        left: left - offsetLeft,
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
        aria-haspopup="menu"
        aria-expanded={open}
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
        {isNoRoles ? noRolesTitle : (effectiveLabels[role]?.label ?? roleLabel)}
        <MaterialIcon name="expand_more" size={16} color="var(--slate-11)" />
      </button>

      {/* Dropdown popover — portaled out of the row (overflow clipping), into the enclosing dialog if any */}
      {open && anchorRect && createPortal(
        <Box
          ref={menuRef}
          role="menu"
          onKeyDown={handleMenuKeyDown}
          style={{
            position: portalHost ? 'absolute' : 'fixed',
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
            roles.map((r, index) => (
            <Flex
              key={r}
              role="menuitemradio"
              aria-checked={r === role}
              tabIndex={0}
              onKeyDown={onActivateKey(() => {
                onRoleChange?.(r);
                setOpen(false);
              })}
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
                  index === roles.length - 1 && !onRemove ? 8 : 6,
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
                  {effectiveLabels[r]?.label ?? r}
                </Text>
                <Text size="1" style={{ color: 'var(--slate-11)', fontSize: 12, lineHeight: '15px' }}>
                  {effectiveLabels[r]?.description ?? ''}
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

          {onMakeOwner && !isNoRoles && (
            <>
              <Box style={{ height: 1, backgroundColor: 'var(--olive-3)' }} />
              <Flex
                role="menuitem"
                tabIndex={0}
                align="center"
                onKeyDown={onActivateKey(() => {
                  onMakeOwner();
                  setOpen(false);
                })}
                onClick={() => {
                  onMakeOwner();
                  setOpen(false);
                }}
                style={{ padding: '10px 12px', cursor: 'pointer' }}
              >
                <Text size="1" style={{ color: 'var(--slate-12)', fontSize: 13, lineHeight: '16px' }}>
                  {t('chat.collab.share.makeOwner')}
                </Text>
              </Flex>
            </>
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
                role="menuitem"
                tabIndex={0}
                onKeyDown={onActivateKey(() => {
                  onRemove();
                  setOpen(false);
                })}
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
                  {t('action.remove')}
                </Text>
              </Flex>
            </>
          )}
        </Box>,
        portalHost ?? document.body
      )}
    </Box>
  );
}
