/**
 * Standardized icon sizes for consistent UI across the application.
 * 
 * Usage guidelines:
 * - PRIMARY: Main action icons (message actions, toolbar actions, chat input)
 * - HEADER: Header and toolbar icons (file preview header, sidebars)
 * - SECONDARY: Supporting UI elements, badges
 * - MINIMAL: Tiny UI elements, rarely used
 * - FILE_ICON_SMALL: File icons in compact lists
 * - FILE_ICON_MEDIUM: File icons in standard views
 * - FILE_ICON_LARGE: File icons in preview headers
 */
export const ICON_SIZES = {
  /** Primary action icons - 20px */
  PRIMARY: 20,
  
  /** Header and toolbar icons - 20px */
  HEADER: 20,
  
  /** Secondary/supporting icons - 16px */
  SECONDARY: 16,
  
  /** Minimal UI icons - 14px */
  MINIMAL: 14,
  
  /** File icons - small - 16px */
  FILE_ICON_SMALL: 16,
  
  /** File icons - medium - 20px */
  FILE_ICON_MEDIUM: 20,
  
  /** File icons - large - 24px */
  FILE_ICON_LARGE: 24,
  
  /** Error/placeholder icons - 48px */
  PLACEHOLDER: 48,

  /** Small icons - 12px */
  SMALL: 12,
} as const;

export type IconSize = typeof ICON_SIZES[keyof typeof ICON_SIZES];
