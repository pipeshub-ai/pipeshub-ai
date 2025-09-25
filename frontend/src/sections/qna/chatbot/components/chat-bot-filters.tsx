import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Icon } from '@iconify/react';
import arrowUpIcon from '@iconify-icons/mdi/arrow-up';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import sparklesIcon from '@iconify-icons/mdi/star-four-points';
import plusIcon from '@iconify-icons/mdi/plus';
import searchIcon from '@iconify-icons/mdi/magnify';
import closeIcon from '@iconify-icons/mdi/close';
import chevronUpIcon from '@iconify-icons/mdi/chevron-up';
import appsIcon from '@iconify-icons/mdi/connection';
import databaseIcon from '@iconify-icons/mdi/database-outline';
import filterIcon from '@iconify-icons/mdi/filter-variant';
import { Theme } from '@mui/material/styles';

import {
  Box,
  useTheme,
  alpha,
  Menu,
  MenuItem,
  Typography,
  Chip,
  Divider,
  TextField,
  InputAdornment,
  Collapse,
  Button,
  Badge,
  Stack,
} from '@mui/material';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

// Types
interface Resource {
  id: string;
  name: string;
  iconPath?: string;
}

interface App extends Resource {
  iconPath?: string;
}

interface KnowledgeBase extends Resource {}

export interface ChatBotFiltersProps {
  resourcesAnchor: HTMLElement | null;
  closeResourcesMenu: () => void;
  searchTerm: string;
  setSearchTerm: (value: string) => void;
  selectedApps: string[];
  selectedKbIds: string[];
  expandedSections: { apps: boolean; kb: boolean };
  toggleSection: (section: 'apps' | 'kb') => void;
  toggleApp: (id: string) => void;
  toggleKb: (id: string) => void;
  filteredApps: App[];
  filteredKBs: KnowledgeBase[];
  showMoreApps: boolean;
  showMoreKBs: boolean;
  setShowMoreApps: (value: boolean) => void;
  setShowMoreKBs: (value: boolean) => void;
  setSelectedApps: (value: string[]) => void;
  setSelectedKbIds: (value: string[]) => void;
}

// Professional Constants
const CONFIG = {
  MENU_WIDTH: 280,
  MENU_HEIGHT: { MIN: 360, MAX: 420 },
  DISPLAY_COUNT: 6,
  TRANSITION_DURATION: 150,
} as const;

const SECTIONS = {
  apps: {
    icon: appsIcon,
    label: 'Data Sources',
    type: 'SOURCE',
  },
  kb: {
    icon: databaseIcon,
    label: 'Knowledge',
    type: 'KB',
  },
} as const;

// Professional Styling Hook
const useStyles = (theme: Theme, isDark: boolean) =>
  useMemo(() => {
    const surfaceColor = isDark ? '#1a1a1a' : '#ffffff';
    const borderColor = isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)';
    const hoverColor = isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)';

    return {
      menu: {
        width: CONFIG.MENU_WIDTH,
        maxHeight: CONFIG.MENU_HEIGHT.MAX,
        minHeight: CONFIG.MENU_HEIGHT.MIN,
        borderRadius: '8px',
        border: `1px solid ${borderColor}`,
        backgroundColor: surfaceColor,
        boxShadow: isDark
          ? '0 8px 24px rgba(0, 0, 0, 0.4), 0 2px 8px rgba(0, 0, 0, 0.2)'
          : '0 8px 24px rgba(0, 0, 0, 0.12), 0 2px 8px rgba(0, 0, 0, 0.04)',
        overflow: 'hidden',
        backdropFilter: 'blur(8px)',
      },
      searchInput: {
        '& .MuiOutlinedInput-root': {
          height: 36,
          borderRadius: '6px',
          fontSize: '0.875rem',
          backgroundColor: isDark ? 'rgba(255, 255, 255, 0.02)' : 'rgba(0, 0, 0, 0.02)',
          '& fieldset': {
            borderColor,
          },
          '&:hover fieldset': {
            borderColor: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
          },
          '&.Mui-focused fieldset': {
            borderColor: theme.palette.text.secondary,
            borderWidth: '1px',
          },
          '& input': {
            padding: '8px 12px',
            fontSize: '0.875rem',
            '&::placeholder': {
              opacity: 0.5,
              fontWeight: 400,
            },
          },
        },
      },
      scrollArea: {
        ...createScrollableContainerStyle(theme),
        '&::-webkit-scrollbar': { width: 4 },
        '&::-webkit-scrollbar-track': { background: 'transparent' },
        '&::-webkit-scrollbar-thumb': {
          background: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
          borderRadius: '2px',
        },
      },
    };
  }, [theme, isDark]);

// Minimal Section Header
const SectionHeader = React.memo(
  ({
    section,
    isExpanded,
    selectedCount,
    onToggle,
    theme,
    isDark,
  }: {
    section: 'apps' | 'kb';
    isExpanded: boolean;
    selectedCount: number;
    onToggle: () => void;
    theme: Theme;
    isDark: boolean;
  }) => {
    const config = SECTIONS[section];

    return (
      <Box
        onClick={onToggle}
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 2,
          py: 1.5,
          cursor: 'pointer',
          transition: 'all 0.15s ease',
          '&:hover': {
            backgroundColor: isDark ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.03)',
          },
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Icon
            icon={config.icon}
            width={16}
            height={16}
            style={{
              color: isDark ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
            }}
          />
          <Typography
            sx={{
              fontSize: '0.875rem',
              fontWeight: 500,
              color: 'text.primary',
            }}
          >
            {config.label}
          </Typography>
          {selectedCount > 0 && (
            <Box
              sx={{
                minWidth: 20,
                height: 20,
                borderRadius: '10px',
                backgroundColor: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.75rem',
                fontWeight: 600,
                color: 'text.secondary',
                px: 0.75,
              }}
            >
              {selectedCount}
            </Box>
          )}
        </Box>
        <Icon
          icon={isExpanded ? chevronUpIcon : chevronDownIcon}
          width={14}
          height={14}
          style={{
            color: isDark ? 'rgba(255, 255, 255, 0.4)' : 'rgba(0, 0, 0, 0.4)',
            transition: 'transform 0.15s ease',
          }}
        />
      </Box>
    );
  }
);

// Clean Resource Item
const ResourceItem = React.memo(
  ({
    resource,
    type,
    isSelected,
    onToggle,
    isSearchResult = false,
    theme,
    isDark,
  }: {
    resource: Resource;
    type: 'app' | 'kb';
    isSelected: boolean;
    onToggle: () => void;
    isSearchResult?: boolean;
    theme: Theme;
    isDark: boolean;
  }) => {
    const config = SECTIONS[type === 'app' ? 'apps' : 'kb'];

    return (
      <MenuItem
        onClick={onToggle}
        sx={{
          px: 2,
          py: 1,
          mx: 1,
          mb: 0.5,
          borderRadius: '4px',
          minHeight: 36,
          transition: 'all 0.15s ease',
          backgroundColor: isSelected
            ? isDark
              ? 'rgba(255, 255, 255, 0.06)'
              : 'rgba(0, 0, 0, 0.06)'
            : 'transparent',
          border: isSelected
            ? `1px solid ${isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)'}`
            : '1px solid transparent',
          '&:hover': {
            backgroundColor: isSelected
              ? isDark
                ? 'rgba(255, 255, 255, 0.08)'
                : 'rgba(0, 0, 0, 0.08)'
              : isDark
                ? 'rgba(255, 255, 255, 0.03)'
                : 'rgba(0, 0, 0, 0.03)',
          },
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', gap: 1.5 }}>
          {/* Icon */}
          <Box
            sx={{
              width: 20,
              height: 20,
              borderRadius: '4px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)',
              flexShrink: 0,
            }}
          >
            {type === 'app' ? (
              <img
                src={resource.iconPath || '/assets/icons/connectors/default.svg'}
                alt={resource.name}
                width={12}
                height={12}
                style={{ opacity: 0.8 }}
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).src =
                    '/assets/icons/connectors/default.svg';
                }}
              />
            ) : (
              <Icon
                icon={databaseIcon}
                width={12}
                height={12}
                style={{
                  color: isDark ? 'rgba(255, 255, 255, 0.6)' : 'rgba(0, 0, 0, 0.6)',
                }}
              />
            )}
          </Box>

          {/* Name */}
          <Typography
            sx={{
              flex: 1,
              fontSize: '0.875rem',
              fontWeight: 400,
              color: 'text.primary',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {resource.name}
          </Typography>

          {/* Type indicator for search */}
          {isSearchResult && (
            <Typography
              sx={{
                fontSize: '0.6875rem',
                fontWeight: 500,
                color: 'text.disabled',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              {config.type}
            </Typography>
          )}

          {/* Selection indicator */}
          {isSelected && (
            <Box
              sx={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                backgroundColor: isDark ? 'rgba(255, 255, 255, 0.6)' : 'rgba(0, 0, 0, 0.6)',
                flexShrink: 0,
              }}
            />
          )}
        </Box>
      </MenuItem>
    );
  }
);

// Show More Button
const ShowMoreButton = React.memo(
  ({ count, onClick, isDark }: { count: number; onClick: () => void; isDark: boolean }) => (
    <MenuItem
      onClick={onClick}
      sx={{
        px: 2,
        py: 1,
        mx: 1,
        mt: 0.5,
        borderRadius: '4px',
        justifyContent: 'center',
        minHeight: 32,
        fontSize: '0.8125rem',
        fontWeight: 400,
        color: 'text.secondary',
        border: `1px dashed ${isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)'}`,
        '&:hover': {
          backgroundColor: isDark ? 'rgba(255, 255, 255, 0.02)' : 'rgba(0, 0, 0, 0.02)',
          borderColor: isDark ? 'rgba(255, 255, 255, 0.15)' : 'rgba(0, 0, 0, 0.15)',
        },
      }}
    >
      <Icon icon={plusIcon} width={12} height={12} style={{ marginRight: '6px' }} />
      {count} more
    </MenuItem>
  )
);

// Main Component
const ChatBotFilters = ({
  resourcesAnchor,
  closeResourcesMenu,
  searchTerm,
  setSearchTerm,
  selectedApps,
  selectedKbIds,
  expandedSections,
  toggleSection,
  toggleApp,
  toggleKb,
  filteredApps,
  filteredKBs,
  showMoreApps,
  showMoreKBs,
  setShowMoreApps,
  setShowMoreKBs,
  setSelectedApps,
  setSelectedKbIds,
}: ChatBotFiltersProps) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const styles = useStyles(theme, isDark);

  const totalSelected = selectedApps.length + selectedKbIds.length;
  const hasResults = filteredApps.length > 0 || filteredKBs.length > 0;

  const renderSection = (section: 'apps' | 'kb') => {
    const isApps = section === 'apps';
    const resources = isApps ? filteredApps : filteredKBs;
    const selectedItems = isApps ? selectedApps : selectedKbIds;
    const showMore = isApps ? showMoreApps : showMoreKBs;
    const setShowMore = isApps ? setShowMoreApps : setShowMoreKBs;
    const toggleResource = isApps ? toggleApp : toggleKb;

    const displayCount = showMore ? resources.length : CONFIG.DISPLAY_COUNT;
    const hasMore = resources.length > CONFIG.DISPLAY_COUNT;

    return (
      <Box key={section}>
        <SectionHeader
          section={section}
          isExpanded={expandedSections[section]}
          selectedCount={selectedItems.length}
          onToggle={() => toggleSection(section)}
          theme={theme}
          isDark={isDark}
        />

        <Collapse in={expandedSections[section]} timeout={CONFIG.TRANSITION_DURATION}>
          <Box sx={{ pb: 1 }}>
            {resources.slice(0, displayCount).map((resource) => (
              <ResourceItem
                key={resource.id}
                resource={resource}
                type={isApps ? 'app' : 'kb'}
                isSelected={selectedItems.includes(resource.id)}
                onToggle={() => toggleResource(resource.id)}
                theme={theme}
                isDark={isDark}
              />
            ))}

            {!showMore && hasMore && (
              <ShowMoreButton
                count={resources.length - CONFIG.DISPLAY_COUNT}
                onClick={() => setShowMore(true)}
                isDark={isDark}
              />
            )}
          </Box>
        </Collapse>
      </Box>
    );
  };

  const renderSearchResults = () => (
    <Box sx={{ py: 1 }}>
      {filteredApps.map((app) => (
        <ResourceItem
          key={`search-app-${app.id}`}
          resource={app}
          type="app"
          isSelected={selectedApps.includes(app.id)}
          onToggle={() => toggleApp(app.id)}
          isSearchResult
          theme={theme}
          isDark={isDark}
        />
      ))}

      {filteredKBs.map((kb) => (
        <ResourceItem
          key={`search-kb-${kb.id}`}
          resource={kb}
          type="kb"
          isSelected={selectedKbIds.includes(kb.id)}
          onToggle={() => toggleKb(kb.id)}
          isSearchResult
          theme={theme}
          isDark={isDark}
        />
      ))}
    </Box>
  );

  return (
    <Menu
      anchorEl={resourcesAnchor}
      open={Boolean(resourcesAnchor)}
      onClose={closeResourcesMenu}
      anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
      transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      PaperProps={{ sx: styles.menu }}
      transitionDuration={CONFIG.TRANSITION_DURATION}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <Box sx={{ p: 2, pb: 1.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5 }}>
            <Icon
              icon={filterIcon}
              width={14}
              height={14}
              style={{ color: isDark ? 'rgba(255, 255, 255, 0.6)' : 'rgba(0, 0, 0, 0.6)' }}
            />
            <Typography
              sx={{
                fontSize: '0.875rem',
                fontWeight: 500,
                color: 'text.primary',
              }}
            >
              Filter Sources
            </Typography>
          </Box>

          <TextField
            fullWidth
            placeholder="Search sources..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            size="small"
            sx={styles.searchInput}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Icon
                    icon={searchIcon}
                    width={14}
                    height={14}
                    style={{ color: isDark ? 'rgba(255, 255, 255, 0.4)' : 'rgba(0, 0, 0, 0.4)' }}
                  />
                </InputAdornment>
              ),
            }}
          />
        </Box>

        <Divider sx={{ opacity: 0.6 }} />

        {/* Content */}
        <Box
          sx={{
            flex: 1,
            overflow: 'auto',
            maxHeight: 250,
            ...styles.scrollArea,
          }}
        >
          {searchTerm ? (
            !hasResults ? (
              <Box sx={{ py: 4, textAlign: 'center' }}>
                <Typography
                  variant="body2"
                  sx={{
                    fontSize: '0.8125rem',
                    color: 'text.secondary',
                    fontWeight: 400,
                  }}
                >
                  No results for &quot;{searchTerm}&quot;
                </Typography>
              </Box>
            ) : (
              renderSearchResults()
            )
          ) : (
            <Box sx={{ py: 0.5 }}>
              {renderSection('apps')}
              {renderSection('kb')}
            </Box>
          )}
        </Box>

        {/* Footer */}
        {totalSelected > 0 && (
          <>
            <Divider sx={{ opacity: 0.6 }} />
            <Box
              sx={{ p: 2, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
            >
              <Typography
                sx={{
                  fontSize: '0.8125rem',
                  color: 'text.secondary',
                  fontWeight: 400,
                }}
              >
                {totalSelected} selected
              </Typography>

              <Button
                onClick={() => {
                  setSelectedApps([]);
                  setSelectedKbIds([]);
                }}
                size="small"
                sx={{
                  fontSize: '0.8125rem',
                  fontWeight: 400,
                  textTransform: 'none',
                  color: 'text.secondary',
                  minWidth: 'auto',
                  p: 0.5,
                  '&:hover': {
                    backgroundColor: isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)',
                  },
                }}
              >
                Clear
              </Button>
            </Box>
          </>
        )}
      </Box>
    </Menu>
  );
};

export default React.memo(ChatBotFilters);
