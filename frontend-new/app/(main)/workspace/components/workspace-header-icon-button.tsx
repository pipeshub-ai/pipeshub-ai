'use client';

import { useState } from 'react';
import type { CSSProperties } from 'react';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface WorkspaceHeaderIconButtonProps {
  icon: string;
  onClick: () => void;
  'aria-label'?: string;
  style?: CSSProperties;
}

/** 32├ù32 bordered icon control ΓÇö matches workspace headers (e.g. Bots). */
export function WorkspaceHeaderIconButton({
  icon,
  onClick,
  'aria-label': ariaLabel,
  style,
}: WorkspaceHeaderIconButtonProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        padding: 0,
        border: '1px solid var(--gray-a4)',
        outline: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 32,
        height: 32,
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--gray-a3)' : 'transparent',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
        flexShrink: 0,
        ...style,
      }}
    >
      <MaterialIcon name={icon} size={16} color="var(--gray-11)" />
    </button>
  );
}
