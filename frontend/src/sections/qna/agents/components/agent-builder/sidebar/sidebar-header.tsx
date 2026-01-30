/**
 * SidebarHeader Component
 * 
 * Header section with search functionality for the flow builder sidebar.
 * Includes title, icon, and search input with clear functionality.
 * 
 * @component
 * @example
 * ```tsx
 * <SidebarHeader
 *   searchQuery={searchQuery}
 *   onSearchChange={(query) => setSearchQuery(query)}
 * />
 * ```
 */

import React, { memo } from 'react';
import {
  Box,
  Typography,
  TextField,
  IconButton,
  InputAdornment,
  useTheme,
  alpha,
} from '@mui/material';
import { Icon } from '@iconify/react';
import { UI_ICONS, CATEGORY_ICONS } from './sidebar.icons';
import { SidebarHeaderProps } from './sidebar.types';
import { SPACING, ICON_SIZES, PLACEHOLDERS, ARIA_LABELS } from './sidebar.constants';
import { getSearchFieldStyles, getIconContainerStyles } from './sidebar.styles';

/**
 * Sidebar header with search functionality
 * Optimized with React.memo for performance
 */
const SidebarHeaderComponent: React.FC<SidebarHeaderProps> = ({
  searchQuery,
  onSearchChange,
}) => {
  const theme = useTheme();

  const isDark = theme.palette.mode === 'dark';

  return (
    <Box
      sx={{
        p: 2.5,
        borderBottom: `1px solid ${theme.palette.divider}`,
        backgroundColor: theme.palette.background.paper,
        position: 'sticky',
        top: 0,
        zIndex: 10,
      }}
    >
      {/* Minimal Search Field */}
      <TextField
        fullWidth
        size="small"
        placeholder={PLACEHOLDERS.SEARCH}
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
        inputProps={{
          'aria-label': ARIA_LABELS.SEARCH_INPUT,
        }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Icon
                icon={UI_ICONS.search}
                fontSize={ICON_SIZES.MD}
                style={{ 
                  color: theme.palette.text.secondary,
                  transition: 'color 0.2s ease',
                }}
              />
            </InputAdornment>
          ),
          endAdornment: searchQuery && (
            <InputAdornment position="end">
              <IconButton
                size="small"
                onClick={() => onSearchChange('')}
                aria-label={ARIA_LABELS.CLEAR_SEARCH}
                sx={{
                  p: SPACING.XS,
                  color: theme.palette.text.secondary,
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    color: theme.palette.text.primary,
                    backgroundColor: alpha(theme.palette.action.hover, 0.5),
                  },
                }}
              >
                <Icon icon={UI_ICONS.clear} fontSize={ICON_SIZES.SM} />
              </IconButton>
            </InputAdornment>
          ),
        }}
        sx={{
          ...getSearchFieldStyles(theme),
          '& .MuiOutlinedInput-root': {
            backgroundColor: isDark ? alpha(theme.palette.background.paper, 0.5) : theme.palette.background.paper,
            borderRadius: 1,
            transition: 'all 0.2s ease',
            '& fieldset': {
              borderColor: theme.palette.divider,
              borderWidth: '1px',
            },
            '&:hover': {
              backgroundColor: isDark ? alpha(theme.palette.background.paper, 0.7) : theme.palette.background.paper,
              '& fieldset': {
                borderColor: theme.palette.divider,
              },
            },
            '&.Mui-focused': {
              backgroundColor: isDark ? theme.palette.background.paper : theme.palette.background.paper,
              '& fieldset': {
                borderColor: theme.palette.text.secondary,
                borderWidth: '1px',
              },
            },
          },
          '& .MuiInputBase-input': {
            fontSize: '0.8125rem',
            fontWeight: 400,
            padding: '8px 12px',
          },
        }}
      />
    </Box>
  );
};

/**
 * Memoized export to prevent unnecessary re-renders
 */
export const SidebarHeader = memo(SidebarHeaderComponent);

