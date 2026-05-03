'use client';

import React from 'react';

interface ChatStarIconProps {
  size?: number;
  color?: string;
  style?: React.CSSProperties;
}

export const ChatStarIcon = ({ size = 16, color = 'currentColor', style }: ChatStarIconProps) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    style={{ display: 'inline-flex', flexShrink: 0, color, ...style }}
  >
    <path
      d="M12.6667 1H3.33333C2.6 1 2 1.6 2 2.33333V11.6667C2 12.4 2.6 13 3.33333 13H6L8 15L10 13H12.6667C13.4 13 14 12.4 14 11.6667V2.33333C14 1.6 13.4 1 12.6667 1ZM12.6667 11.6667H9.44667L8 13.1133L6.55333 11.6667H3.33333V2.33333H12.6667V11.6667ZM8 11L9.25333 8.25333L12 7L9.25333 5.74667L8 3L6.74667 5.74667L4 7L6.74667 8.25333L8 11Z"
      fill="currentColor"
    />
  </svg>
);
