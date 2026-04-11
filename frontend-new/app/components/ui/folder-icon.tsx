'use client';

import React from 'react';

export type FolderIconVariant = 'default' | 'open' | 'special';

interface FolderIconProps {
  variant?: FolderIconVariant;
  size?: number;
  color?: string;
  style?: React.CSSProperties;
  className?: string;
}

export function FolderIcon({
  variant = 'default',
  size = 16,
  color = 'var(--slate-11)',
  style,
  className,
}: FolderIconProps) {
  // Variant-specific styles
  const getVariantStyles = (): React.CSSProperties => {
    switch (variant) {
      case 'open':
        return {
          transform: 'rotate(-5deg)',
          opacity: 0.9,
        };
      case 'special':
        return {
          filter: 'drop-shadow(0 0 2px currentColor)',
        };
      default:
        return {};
    }
  };

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{
        color: color,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        ...getVariantStyles(),
        ...style,
      }}
      className={className}
    >
      <path
        d="M20.25 6.75H12.3103L9.75 4.18969C9.61115 4.04988 9.44594 3.93902 9.26392 3.86354C9.08191 3.78807 8.88673 3.74947 8.68969 3.75H3.75C3.35218 3.75 2.97064 3.90803 2.68934 4.18934C2.40804 4.47064 2.25 4.85217 2.25 5.25V18.8081C2.25074 19.1905 2.40302 19.5569 2.67347 19.8272C2.94391 20.0975 3.31047 20.2495 3.69281 20.25H20.3334C20.709 20.2495 21.069 20.1001 21.3345 19.8345C21.6001 19.569 21.7495 19.209 21.75 18.8334V8.25C21.75 7.85217 21.592 7.47064 21.3107 7.18934C21.0294 6.90804 20.6478 6.75 20.25 6.75ZM3.75 5.25H8.68969L10.1897 6.75H3.75V5.25Z"
        fill="currentColor"
      />
    </svg>
  );
}
