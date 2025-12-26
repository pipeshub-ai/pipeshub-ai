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

  return (
    <Box
      sx={{
        p: SPACING.XL,
        borderBottom: `1px solid ${theme.palette.divider}`,
        backgroundColor: theme.palette.background.paper,
      }}
    >
      {/* Title Row */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: SPACING.LG, mb: SPACING.XL }}>
        <Box sx={getIconContainerStyles(ICON_SIZES.XXL)}>
          <Icon
            icon={CATEGORY_ICONS.bundle}
            width={ICON_SIZES.XL}
            height={ICON_SIZES.XL}
            style={{ color: theme.palette.text.primary }}
          />
        </Box>
        <Typography
          variant="h6"
          sx={{
            fontWeight: 600,
            color: theme.palette.text.primary,
            fontSize: '1rem',
            flex: 1,
          }}
        >
          Components
        </Typography>
      </Box>

      {/* Search Field */}
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
                style={{ color: theme.palette.text.secondary }}
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
                }}
              >
                <Icon icon={UI_ICONS.clear} fontSize={ICON_SIZES.SM} />
              </IconButton>
            </InputAdornment>
          ),
        }}
        sx={getSearchFieldStyles(theme)}
      />

      {/* Search Query Display */}
      {searchQuery && (
        <Typography
          variant="caption"
          sx={{
            mt: SPACING.MD,
            display: 'block',
            fontSize: '0.7rem',
            color: theme.palette.text.secondary,
            opacity: 0.8,
          }}
        >
          /{searchQuery}
        </Typography>
      )}
    </Box>
  );
};

/**
 * Memoized export to prevent unnecessary re-renders
 */
export const SidebarHeader = memo(SidebarHeaderComponent);

