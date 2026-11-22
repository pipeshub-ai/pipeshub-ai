'use client'

import React from "react";

interface MaterialIconProps {
  name: string;
  size?: number;
  color?: string;
  style?: React.CSSProperties;
  variant?: 'outlined' | 'filled';
}

export const MaterialIcon = ({
  name,
  size = 20,
  color,
  style,
  variant = 'outlined',
}: MaterialIconProps) => (
  <span
    className={variant === 'filled' ? 'material-icons' : 'material-icons-outlined'}
    style={{
      fontSize: variant === 'filled' ? size : Math.round(size * 1.22),
      width: size,
      height: size,
      color: color || 'inherit',
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      lineHeight: 1,
      fontVariationSettings: variant === 'filled' ? undefined : "'FILL' 0, 'wght' 300, 'GRAD' 0, 'opsz' 40",
      // Until the icon font loads the name renders as text; clipped, it can't take a neighbour's clicks.
      overflow: 'clip',
      overflowClipMargin: Math.round(size * 0.15),
      ...style
    }}
  >
    {name}
  </span>
);
