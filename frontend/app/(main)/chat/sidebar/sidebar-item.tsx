'use client';

import { useState } from 'react';
import { Link } from '@/lib/navigation';
import { CHAT_ITEM_HEIGHT } from '@/app/components/sidebar';
import { SIDEBAR_FOCUS_CLASS, SidebarFocusStyle } from '@/app/components/sidebar/sidebar-focus-style';

interface SidebarItemProps {
  /** Optional left icon (MaterialIcon, Image, any ReactNode) */
  icon?: React.ReactNode;
  /** Label text or ReactNode displayed in the item */
  label: React.ReactNode;
  /** Optional second line under the label (e.g. who shared a chat) */
  subtitle?: React.ReactNode;
  /** Right after a text label, on its line (e.g. a pinned chat's pin); the label truncates first. */
  titleEnd?: React.ReactNode;
  /** Optional content rendered on the right side (e.g., keyboard shortcut badge) */
  rightSlot?: React.ReactNode;
  /** Click handler */
  onClick?: () => void;
  /** Navigation href — renders as <a> when provided */
  href?: string;
  /** Whether this item is currently selected/active */
  isActive?: boolean;
  /** Text color (default: 'var(--text-secondary)') */
  textColor?: string;
  /** Font weight (default: 400) */
  fontWeight?: number;
  /** Keep the hover tint while the pointer is elsewhere (e.g. on the row's open menu) */
  forceHighlight?: boolean;
  /** Callback when hover state changes */
  onHoverChange?: (isHovered: boolean) => void;
}

/**
 * Unified interactive sidebar item.
 *
 * All clickable sidebar elements (chat rows, More, Start a chat) share this
 * component for consistent sizing, spacing, and hover treatment.
 *
 * Selected (the open chat): the muted fill, a 1px default border, and the label
 * in the primary text colour at medium weight. Hover, press and an open menu: the
 * subtle fill, one step lighter, with no border, so a row under the pointer never
 * reads as the selected one. At rest the border is transparent, so the title never
 * moves between states.
 */
export function SidebarItem({
  icon,
  label,
  subtitle,
  titleEnd,
  rightSlot,
  onClick,
  href,
  isActive = false,
  textColor = 'var(--text-secondary)',
  fontWeight = 400,
  forceHighlight = false,
  onHoverChange,
}: SidebarItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  // Keyboard focus inside the row counts as hover, so its menu can be reached without a mouse.
  const [hasKeyboardFocus, setHasKeyboardFocus] = useState(false);
  const hovered = (isHovered || forceHighlight) && !isActive;
  const labelColor = isActive ? 'var(--text-primary)' : textColor;
  const labelWeight = isActive ? 500 : fontWeight;

  const handleMouseEnter = () => {
    setIsHovered(true);
    onHoverChange?.(true);
  };

  const handleMouseLeave = () => {
    setIsHovered(false);
    onHoverChange?.(hasKeyboardFocus);
  };

  const handleFocus = (e: React.FocusEvent) => {
    if (hasKeyboardFocus || !(e.target as HTMLElement).matches(':focus-visible')) return;
    setHasKeyboardFocus(true);
    onHoverChange?.(true);
  };

  const handleBlur = (e: React.FocusEvent) => {
    if (!hasKeyboardFocus || e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    // After focus has landed on the next control: a row that redraws (its menu hides, its faces return) while focus
    // is between two controls makes a focus trap (the navigation drawer) take focus back to itself.
    window.setTimeout(() => {
      setHasKeyboardFocus(false);
      onHoverChange?.(isHovered);
    }, 0);
  };

  const sharedStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
    width: '100%',
    height: subtitle ? 'auto' : CHAT_ITEM_HEIGHT,
    minHeight: CHAT_ITEM_HEIGHT,
    padding: subtitle ? '4px var(--space-3)' : '0 var(--space-3)',
    boxSizing: 'border-box',
    flexShrink: 0,
    borderRadius: 'var(--radius-1)',
    backgroundColor: isActive ? 'var(--fill-muted)' : hovered ? 'var(--surface-subtle)' : 'transparent',
    border: isActive ? '1px solid var(--border-default)' : '1px solid transparent',
    transition: 'background-color var(--motion-micro) var(--motion-ease)',
    cursor: (onClick || href) ? 'pointer' : 'default',
    userSelect: 'none',
    textDecoration: 'none',
    color: 'inherit',
  };

  const labelText = (
    <span
      style={{
        minWidth: 0,
        fontSize: 'var(--font-size-2)',
        fontWeight: labelWeight,
        lineHeight: 'var(--line-height-2)',
        color: labelColor,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  );
  const labelContent = typeof label === 'string' ? (
    <span
      style={{
        flex: 1,
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        textAlign: 'left',
      }}
    >
      {titleEnd ? (
        <span style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minWidth: 0 }}>
          {labelText}
          {titleEnd}
        </span>
      ) : (
        labelText
      )}
      {subtitle ? (
        <span
          style={{
            fontSize: 'var(--font-size-1)',
            fontWeight: 400,
            lineHeight: 'var(--line-height-1)',
            color: 'var(--text-secondary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {subtitle}
        </span>
      ) : null}
    </span>
  ) : (
    <div
      style={{
        flex: 1,
        fontSize: 'var(--font-size-2)',
        fontWeight: labelWeight,
        lineHeight: 'var(--line-height-2)',
        color: labelColor,
        overflow: 'hidden',
        textAlign: 'left',
      }}
    >
      {label}
    </div>
  );

  const iconSlot = icon ? (
    <span style={{ display: 'inline-flex', flexShrink: 0, lineHeight: 0 }}>
      {icon}
    </span>
  ) : null;

  const content = (
    <>
      {iconSlot}
      {labelContent}
      {rightSlot}
    </>
  );

  if (href) {
    const handleLinkClick = (e: React.MouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      onClick?.();
    };

    // Never nest interactive controls (e.g. menu triggers) inside <a> — invalid HTML
    // and clicks can still activate the link / full navigation in the browser.
    if (rightSlot) {
      // The whole row opens the chat, not only the title: a click on the gap before the trailing slot, on its padding
      // or on the faces is passed on to the link, with its modifier keys (so Ctrl/⌘-click still opens a new tab).
      // Controls in the slot (the ⋯ and its portalled menu) keep their own clicks.
      const handleRowClick = (e: React.MouseEvent<HTMLDivElement>) => {
        if (e.defaultPrevented) return;
        const target = e.target as Element;
        if (target.closest('a, button, [role="menuitem"], [role="menu"], input')) return;
        const link = e.currentTarget.querySelector<HTMLAnchorElement>(':scope > a');
        link?.dispatchEvent(
          new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            ctrlKey: e.ctrlKey,
            metaKey: e.metaKey,
            shiftKey: e.shiftKey,
            altKey: e.altKey,
          }),
        );
      };
      return (
        <div
          onClick={handleRowClick}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          onFocus={handleFocus}
          onBlur={handleBlur}
          style={{ ...sharedStyle, padding: 0 }}
        >
          <SidebarFocusStyle />
          <Link
            href={href}
            onClick={handleLinkClick}
            aria-current={isActive ? 'page' : undefined}
            className={SIDEBAR_FOCUS_CLASS}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-2)',
              flex: 1,
              minWidth: 0,
              paddingLeft: 'var(--space-3)',
              height: '100%',
              borderRadius: 'var(--radius-1)',
              textDecoration: 'none',
              color: 'inherit',
              cursor: 'pointer',
            }}
          >
            {iconSlot}
            {labelContent}
          </Link>
          <span
            style={{
              flexShrink: 0,
              display: 'inline-flex',
              alignItems: 'center',
              paddingRight: 'var(--space-3)',
              height: '100%'
            }}
            onClick={(e) => {
              // A click on a control in the slot (the ⋯) belongs to that control only; anywhere else in the slot
              // falls through to the row, which opens the chat.
              if (!(e.target as Element).closest('button, [role="menuitem"], [role="menu"]')) return;
              e.preventDefault();
              e.stopPropagation();
            }}
          >
            {rightSlot}
          </span>
        </div>
      );
    }

    return (
      <>
        <SidebarFocusStyle />
        <Link
          href={href}
          onClick={handleLinkClick}
          aria-current={isActive ? 'page' : undefined}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          className={SIDEBAR_FOCUS_CLASS}
          style={sharedStyle}
        >
          {content}
        </Link>
      </>
    );
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Keys pressed on a control inside the row (its menu) belong to that control.
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick?.();
    }
  };

  return (
    <>
      <SidebarFocusStyle />
      <div
        role="button"
        aria-current={isActive ? 'page' : undefined}
        tabIndex={onClick ? 0 : undefined}
        onClick={onClick}
        onKeyDown={handleKeyDown}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        onFocus={handleFocus}
        onBlur={handleBlur}
        className={SIDEBAR_FOCUS_CLASS}
        style={sharedStyle}
      >
        {content}
      </div>
    </>
  );
}
