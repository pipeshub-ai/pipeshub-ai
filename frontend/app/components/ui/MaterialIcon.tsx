'use client'

import React from "react";

interface MaterialIconProps {
  name: string;
  size?: number;
  color?: string;
  style?: React.CSSProperties;
  variant?: 'outlined' | 'filled';
  /**
   * What the icon means, for an icon that says something no text beside it says (a status, a warning). Without
   * one the icon is decorative: a control that holds only an icon names itself with `aria-label`.
   */
  label?: string;
}

export const MaterialIcon = ({
  name,
  size = 20,
  color,
  style,
  variant = 'outlined',
  label,
}: MaterialIconProps) => (
  <span
    className={variant === 'filled' ? 'material-icons' : 'material-icons-outlined'}
    // The font draws the glyph from its ligature text ("chevron_right"), which would otherwise be read aloud and
    // become part of the accessible name of every control holding the icon.
    {...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true })}
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
