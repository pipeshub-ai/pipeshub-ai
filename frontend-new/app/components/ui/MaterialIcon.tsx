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
      fontSize: size,
      color: color || 'inherit',
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      lineHeight: 1,
      ...style
    }}
  >
    {name}
  </span>
);
