'use client';

import { Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ELEMENT_HEIGHT, ELEMENT_BORDER, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';

interface StaticNavSectionElementProps {
  icon: string;
  label: string;
  onClick?: () => void;
  isActive?: boolean;
  accent?: boolean;
}

/**
 * A single navigation menu button in the static upper section.
 */
export function StaticNavSectionElement({
  icon,
  label,
  onClick,
  isActive = false,
  accent = false,
}: StaticNavSectionElementProps) {
  return (
    <Button
      variant={isActive ? 'soft' : 'ghost'}
      size="2"
      onClick={onClick}
      color={accent ? undefined : 'gray'}
      style={{
        width: '100%',
        height: ELEMENT_HEIGHT,
        justifyContent: 'flex-start',
        borderRadius: 'var(--radius-1)',
        ...(isActive && { border: ELEMENT_BORDER }),
      }}
    >
      <MaterialIcon name={icon} size={ICON_SIZE_DEFAULT} />
      <span
        style={{
          flex: 1,
          textAlign: 'left',
          fontWeight: 400,
          fontSize: 14,
          lineHeight: 'var(--line-height-2)',
          color: 'var(--slate-11)',
        }}
      >
        {label}
      </span>
    </Button>
  );
}
