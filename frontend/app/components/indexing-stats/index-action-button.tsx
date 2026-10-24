'use client';

import React from 'react';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

const INDEX_ACTION_ICON_SIZE = 16;

export function IndexActionButton({
  label,
  ariaLabel,
  icon,
  color,
  iconColor,
  onClick,
  disabled,
  loading,
}: {
  label: string;
  /** Fuller name for screen readers; it should start with the visible label. */
  ariaLabel?: string;
  icon: string;
  color: 'orange' | 'gray';
  iconColor: string;
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
}) {
  const [isHovered, setIsHovered] = React.useState(false);
  const isBusy = disabled || loading;
  const backgroundColor =
    color === 'orange'
      ? isHovered && !isBusy
        ? 'var(--orange-a4)'
        : 'var(--orange-a3)'
      : isHovered && !isBusy
        ? 'var(--gray-a4)'
        : 'var(--gray-a3)';

  return (
    <button
      type="button"
      aria-label={ariaLabel}
      onClick={onClick}
      disabled={isBusy}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        height: 'var(--space-5)',
        padding: '0 var(--space-2)',
        borderRadius: 'max(var(--radius-1), var(--radius-full))',
        border: 'none',
        backgroundColor,
        color: iconColor,
        fontSize: 'var(--font-size-1)',
        fontWeight: 500,
        lineHeight: 'var(--line-height-1)',
        width: 'max-content',
        maxWidth: '100%',
        flexShrink: 0,
        whiteSpace: 'nowrap',
        cursor: loading ? 'wait' : disabled ? 'not-allowed' : 'pointer',
        opacity: disabled && !loading ? 0.6 : 1,
        transition: 'background-color 150ms ease',
      }}
    >
      {/* MaterialIcon renders its name as text, which would otherwise join the button's name. */}
      <span aria-hidden style={{ display: 'inline-flex' }}>
        <MaterialIcon name={icon} size={INDEX_ACTION_ICON_SIZE} color={iconColor} />
      </span>
      {label}
    </button>
  );
}
