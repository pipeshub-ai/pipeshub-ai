'use client'

import React from "react";

interface MaterialIconProps {
  name: string;
  size?: number;
  color?: string;
  style?: React.CSSProperties;
}

export const MaterialIcon = ({
  name,
  size = 20,
  color,
  style
}: MaterialIconProps) => (
  <span
    className="material-icons-outlined"
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
