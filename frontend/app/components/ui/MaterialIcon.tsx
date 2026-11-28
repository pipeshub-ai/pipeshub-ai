'use client'

import React from "react";

interface MaterialIconProps {
  name: string;
  size?: number;
  color?: string;
  style?: React.CSSProperties;
  /** `filled` draws the same Material Symbols glyph with the fill axis on. */
  variant?: 'outlined' | 'filled';
  /** Weight axis (100 to 700). */
  weight?: number;
  /**
   * What the icon means, for an icon that says something no text beside it says (a status, a warning). With a
   * label the icon is announced as an image; without one it is decorative (DECORATIVE_BY_DEFAULT): a control
   * that holds only an icon names itself with `aria-label`.
   */
  label?: string;
  className?: string;
}

/**
 * Whether an icon without a `label` is hidden from assistive technology.
 *
 * Icons are drawn from font ligatures, so an unhidden icon's text ("folder",
 * "chevron_right") would be read out as part of its control's name. Hidden by
 * default: a control that holds only an icon names itself with `aria-label`.
 */
export const DECORATIVE_BY_DEFAULT = true;

/** Optical size follows the drawn size, within the font's 20 to 48 range. */
const opticalSize = (size: number) => Math.min(48, Math.max(20, size));

/**
 * One Material Symbols Outlined glyph, drawn at the size of its box
 * (font-size = width = height = size). The box keeps an explicit width and
 * height so the ligature text cannot widen it before the font has loaded.
 */
export const MaterialIcon = ({
  name,
  size = 20,
  color,
  style,
  variant = 'outlined',
  weight = 400,
  label,
  className,
}: MaterialIconProps) => {
  const a11y = label
    ? { role: 'img' as const, 'aria-label': label }
    : DECORATIVE_BY_DEFAULT
      ? { 'aria-hidden': true as const }
      : {};
  return (
    <span
      className={className ? `material-icons-outlined ${className}` : 'material-icons-outlined'}
      {...a11y}
      style={{
        fontSize: size,
        width: size,
        height: size,
        color: color || 'inherit',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        lineHeight: 1,
        fontVariationSettings: `'FILL' ${variant === 'filled' ? 1 : 0}, 'wght' ${weight}, 'GRAD' 0, 'opsz' ${opticalSize(size)}`,
        // Until the icon font loads the name renders as text; clipped, it can't take a neighbour's clicks.
        overflow: 'clip',
        overflowClipMargin: Math.round(size * 0.15),
        ...style
      }}
    >
      {name}
    </span>
  );
};
