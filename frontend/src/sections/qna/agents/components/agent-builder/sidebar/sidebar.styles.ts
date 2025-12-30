/**
 * Sidebar Style Utilities
 * 
 * Shared style generators and utilities for consistent styling across sidebar components.
 * Uses Material-UI theme system for responsive and themeable styles.
 * 
 * @module sidebar.styles
 */

import { Theme, alpha } from '@mui/material/styles';
import { SxProps } from '@mui/material';
import {
  SPACING,
  ICON_SIZES,
  FONT_SIZES,
  BORDER_RADIUS,
  OPACITY,
  PADDING_LEVELS,
  SCROLLBAR,
  TRANSITIONS,
} from './sidebar.constants';

/**
 * Generates draggable item styles
 * 
 * @param theme - MUI theme object
 * @param isSubItem - Whether this is a nested sub-item
 * @returns SxProps for draggable items
 */
export const getDraggableItemStyles = (theme: Theme, isSubItem = false): SxProps<Theme> => ({
  py: SPACING.LG * 0.5,
  px: SPACING.XL,
  pl: isSubItem ? PADDING_LEVELS.SUB_ITEM : PADDING_LEVELS.BASE,
  cursor: 'grab',
  borderRadius: BORDER_RADIUS.SM,
  mx: isSubItem ? SPACING.LG : SPACING.MD,
  my: SPACING.XS,
  border: `1px solid ${alpha(theme.palette.divider, OPACITY.DISABLED)}`,
  backgroundColor: 'transparent',
  transition: TRANSITIONS.ALL_EASE,
  '&:hover': {
    backgroundColor: alpha(theme.palette.action.hover, OPACITY.VERY_LIGHT / 2.5),
    borderColor: alpha(theme.palette.divider, OPACITY.VERY_LIGHT),
  },
  '&:active': {
    cursor: 'grabbing',
  },
});

/**
 * Generates category header styles
 * 
 * @param theme - MUI theme object
 * @param isDraggable - Whether the category itself is draggable
 * @returns SxProps for category headers
 */
export const getCategoryHeaderStyles = (
  theme: Theme,
  isDraggable = false
): SxProps<Theme> => ({
  py: SPACING.MD,
  px: SPACING.XL,
  pl: PADDING_LEVELS.BASE,
  cursor: isDraggable ? 'grab' : 'pointer',
  borderRadius: BORDER_RADIUS.MD,
  mx: SPACING.MD,
  mb: SPACING.SM,
  border: `1px solid ${alpha(theme.palette.divider, OPACITY.VERY_LIGHT * 0.8)}`,
  backgroundColor: 'transparent',
  transition: TRANSITIONS.ALL_EASE,
  '&:hover': {
    backgroundColor: alpha(theme.palette.action.hover, OPACITY.VERY_LIGHT / 2.5),
    borderColor: alpha(theme.palette.divider, OPACITY.LIGHT * 0.75),
    transform: isDraggable ? 'translateX(2px)' : 'none',
  },
  '&:active': {
    cursor: isDraggable ? 'grabbing' : 'pointer',
    transform: isDraggable ? 'scale(0.98)' : 'none',
  },
});

/**
 * Generates simple category toggle styles
 * 
 * @param theme - MUI theme object
 * @returns SxProps for simple category toggles
 */
export const getSimpleCategoryStyles = (theme: Theme): SxProps<Theme> => ({
  py: SPACING.MD,
  px: SPACING.XL,
  cursor: 'pointer',
  transition: TRANSITIONS.ALL_EASE,
  '&:hover': {
    backgroundColor: alpha(theme.palette.text.secondary, OPACITY.DISABLED),
  },
});

/**
 * Generates vertical connector line styles
 * 
 * @param theme - MUI theme object
 * @param color - Border color (defaults to divider)
 * @returns SxProps for vertical connector lines
 */
export const getVerticalConnectorStyles = (
  theme: Theme,
  color?: string
): SxProps<Theme> => ({
  position: 'relative',
  '&::before': {
    content: '""',
    position: 'absolute',
    left: '32px',
    top: 0,
    bottom: 0,
    width: '2px',
    backgroundColor: alpha(color || theme.palette.divider, OPACITY.LIGHT),
    borderRadius: '1px',
  },
});

/**
 * Generates scrollbar styles
 * 
 * @param theme - MUI theme object
 * @returns SxProps for custom scrollbars
 */
export const getScrollbarStyles = (theme: Theme): SxProps<Theme> => ({
  '&::-webkit-scrollbar': {
    width: SCROLLBAR.WIDTH,
  },
  '&::-webkit-scrollbar-track': {
    background: 'transparent',
  },
  '&::-webkit-scrollbar-thumb': {
    backgroundColor: alpha(theme.palette.text.secondary, OPACITY.LIGHT),
    borderRadius: SCROLLBAR.BORDER_RADIUS,
    '&:hover': {
      backgroundColor: alpha(theme.palette.text.secondary, OPACITY.MEDIUM * 0.75),
    },
  },
});

/**
 * Generates count badge styles
 * 
 * @param theme - MUI theme object
 * @returns SxProps for count badges
 */
export const getCountBadgeStyles = (theme: Theme): SxProps<Theme> => ({
  fontSize: FONT_SIZES.SM,
  color: alpha(theme.palette.text.secondary, OPACITY.SEMI),
  fontWeight: 500,
  backgroundColor: alpha(theme.palette.text.secondary, OPACITY.VERY_LIGHT),
  px: SPACING.LG * 0.5,
  py: SPACING.XS,
  borderRadius: BORDER_RADIUS.SM,
});

/**
 * Generates icon container styles
 * 
 * @param size - Icon size in pixels
 * @returns SxProps for icon containers
 */
export const getIconContainerStyles = (size: number): SxProps<Theme> => ({
  width: size,
  height: size,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
});

/**
 * Generates search field styles
 * 
 * @param theme - MUI theme object
 * @returns SxProps for search input fields
 */
export const getSearchFieldStyles = (theme: Theme): SxProps<Theme> => ({
  '& .MuiOutlinedInput-root': {
    borderRadius: BORDER_RADIUS.MD,
    backgroundColor: alpha(theme.palette.background.default, 0.5),
    border: `1px solid ${theme.palette.divider}`,
    height: 36,
    transition: TRANSITIONS.ALL_EASE,
    '&:hover': {
      backgroundColor: alpha(theme.palette.background.default, OPACITY.VERY_HIGH),
      borderColor: alpha(theme.palette.text.secondary, OPACITY.MEDIUM * 0.75),
    },
    '&.Mui-focused': {
      backgroundColor: theme.palette.background.default,
      borderColor: theme.palette.text.secondary,
    },
    '& fieldset': {
      border: 'none',
    },
  },
  '& .MuiInputBase-input': {
    color: theme.palette.text.primary,
    fontSize: FONT_SIZES.LG,
    padding: '8px 0',
    '&::placeholder': {
      color: theme.palette.text.secondary,
      opacity: OPACITY.HIGH,
    },
  },
});

/**
 * Generates icon styles for dynamic vs static icons
 * 
 * @param theme - MUI theme object
 * @param size - Icon size in pixels
 * @returns Style object for icons
 */
export const getIconStyles = (theme: Theme, size: number) => ({
  static: {
    color: alpha(theme.palette.text.secondary, OPACITY.HIGH),
    width: size,
    height: size,
  },
  dynamic: {
    objectFit: 'contain' as const,
    width: size,
    height: size,
  },
});

/**
 * Generates text styles for labels
 * 
 * @param theme - MUI theme object
 * @param isSubItem - Whether this is a sub-item
 * @returns SxProps for text labels
 */
export const getLabelStyles = (theme: Theme, isSubItem = false): SxProps<Theme> => ({
  fontSize: isSubItem ? FONT_SIZES.ML : FONT_SIZES.XL,
  color: theme.palette.text.primary,
  fontWeight: 400,
  flex: 1,
  lineHeight: 1.4,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
});

/**
 * Generates empty state message styles
 * 
 * @param theme - MUI theme object
 * @returns SxProps for empty state messages
 */
export const getEmptyStateStyles = (theme: Theme): SxProps<Theme> => ({
  pl: PADDING_LEVELS.BASE,
  py: SPACING.XL * 0.5,
  color: alpha(theme.palette.text.secondary, OPACITY.SEMI),
  fontSize: FONT_SIZES.REGULAR,
  fontStyle: 'italic',
});

/**
 * Generates flex container styles
 * 
 * @param gap - Gap between items (in theme spacing units)
 * @returns SxProps for flex containers
 */
export const getFlexContainerStyles = (gap = SPACING.LG): SxProps<Theme> => ({
  display: 'flex',
  alignItems: 'center',
  gap,
  width: '100%',
});

