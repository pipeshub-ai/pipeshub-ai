import React, { useRef, useMemo, useState, useEffect, useCallback } from 'react';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/mdi/close';
import upIcon from '@iconify-icons/mdi/chevron-up';
import leftIcon from '@iconify-icons/mdi/chevron-left';
import downIcon from '@iconify-icons/mdi/chevron-down';
import rightIcon from '@iconify-icons/mdi/chevron-right';
import filterMenuIcon from '@iconify-icons/mdi/filter-menu';
import filterRemoveIcon from '@iconify-icons/mdi/filter-remove';
import filterVariantIcon from '@iconify-icons/mdi/filter-variant';
import fileDocumentIcon from '@iconify-icons/mdi/file-document';
import sourceBranchIcon from '@iconify-icons/mdi/source-branch';
import connectionIcon from '@iconify-icons/mdi/connection';
import bookOpenVariantIcon from '@iconify-icons/mdi/book-open-variant';
import sortIcon from '@iconify-icons/mdi/sort';
import sortVariantIcon from '@iconify-icons/mdi/sort-variant';

// Additional MDI icons for filter options
import folderIcon from '@iconify-icons/mdi/folder';
import folderOpenIcon from '@iconify-icons/mdi/folder-open';
import fileIcon from '@iconify-icons/mdi/file';
import webIcon from '@iconify-icons/mdi/web';
import messageIcon from '@iconify-icons/mdi/message';
import emailIcon from '@iconify-icons/mdi/email';
import ticketIcon from '@iconify-icons/mdi/ticket';
import commentIcon from '@iconify-icons/mdi/comment';
import mailIcon from '@iconify-icons/mdi/email-outline';
import dotsHorizontalIcon from '@iconify-icons/mdi/dots-horizontal';
import clockOutlineIcon from '@iconify-icons/mdi/clock-outline';
import progressClockIcon from '@iconify-icons/mdi/progress-clock';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle';
import clockAlertIcon from '@iconify-icons/mdi/clock-alert';
import pauseCircleIcon from '@iconify-icons/mdi/pause-circle';
import fileRemoveIcon from '@iconify-icons/mdi/file-remove';
import toggleSwitchOffIcon from '@iconify-icons/mdi/toggle-switch-off';
import folderAlertIcon from '@iconify-icons/mdi/folder-alert';
import databaseIcon from '@iconify-icons/mdi/database';
import appsIcon from '@iconify-icons/mdi/apps';
import sortAscendingIcon from '@iconify-icons/mdi/sort-ascending';
import sortDescendingIcon from '@iconify-icons/mdi/sort-descending';
import sortAlphabeticalAscendingIcon from '@iconify-icons/mdi/sort-alphabetical-ascending';
import calendarClockIcon from '@iconify-icons/mdi/calendar-clock';
import fileClockIcon from '@iconify-icons/mdi/file-clock';
import fileSizeIcon from '@iconify-icons/mdi/file-chart';
import shapeIcon from '@iconify-icons/mdi/shape';

import { alpha, styled, useTheme, keyframes } from '@mui/material/styles';
import {
  Box,
  Chip,
  Fade,
  Badge,
  Paper,
  Drawer,
  Button,
  Tooltip,
  Checkbox,
  Collapse,
  FormGroup,
  Typography,
  IconButton,
  FormControlLabel,
  CircularProgress,
} from '@mui/material';

// Constants
const DRAWER_EXPANDED_WIDTH = 300;
const DRAWER_COLLAPSED_WIDTH = 60;

// MDI Icon mapping for filter options based on their ID
const getFilterOptionIcon = (filterId: string): any => {
  const iconMap: Record<string, any> = {
    // Node Types
    folder: folderIcon,
    record: fileIcon,
    recordGroup: folderOpenIcon,
    app: appsIcon,
    kb: bookOpenVariantIcon,
    
    // Record Types
    FILE: fileIcon,
    WEBPAGE: webIcon,
    MESSAGE: messageIcon,
    EMAIL: emailIcon,
    TICKET: ticketIcon,
    COMMENT: commentIcon,
    MAIL: mailIcon,
    OTHERS: dotsHorizontalIcon,
    
    // Origins
    KB: bookOpenVariantIcon,
    CONNECTOR: connectionIcon,
    
    // Indexing Status
    NOT_STARTED: clockOutlineIcon,
    IN_PROGRESS: progressClockIcon,
    COMPLETED: checkCircleIcon,
    FAILED: alertCircleIcon,
    QUEUED: clockAlertIcon,
    PAUSED: pauseCircleIcon,
    FILE_TYPE_NOT_SUPPORTED: fileRemoveIcon,
    AUTO_INDEX_OFF: toggleSwitchOffIcon,
    EMPTY: folderAlertIcon,
    
    // Sort By
    name: sortAlphabeticalAscendingIcon,
    createdAt: calendarClockIcon,
    updatedAt: fileClockIcon,
    size: fileSizeIcon,
    type: shapeIcon,
    
    // Sort Order
    asc: sortAscendingIcon,
    desc: sortDescendingIcon,
  };
  
  return iconMap[filterId] || fileIcon; // Default to file icon
};

// Types
export interface FilterOption {
  id: string;
  label: string;
  iconPath?: string;
  connectorType?: string;
}

export interface AvailableFilters {
  recordTypes?: FilterOption[];
  origins?: FilterOption[];
  connectors?: FilterOption[];
  kbs?: FilterOption[];
  indexingStatus?: FilterOption[];
  sortBy?: FilterOption[];
  sortOrder?: FilterOption[];
}

export interface AppliedFilters {
  recordTypes?: string[];
  origins?: string[];
  connectorIds?: string[];
  kbIds?: string[];
  indexingStatus?: string[];
  sortBy?: string;
  sortOrder?: string;
}

interface DynamicFilterSidebarProps {
  availableFilters: AvailableFilters;
  appliedFilters: AppliedFilters;
  onFilterChange: (filters: AppliedFilters) => void;
  open?: boolean;
  onToggle?: () => void;
}

// Styled Components
const pulse = keyframes`
  0%, 100% { opacity: 0.6; }
  50% { opacity: 0.8; }
`;

const StyledDrawer = styled(Drawer, { shouldForwardProp: (prop) => prop !== 'open' })(
  ({ theme, open }) => ({
    width: open ? DRAWER_EXPANDED_WIDTH : DRAWER_COLLAPSED_WIDTH,
    flexShrink: 0,
    marginTop: 50,
    whiteSpace: 'nowrap',
    boxSizing: 'border-box',
    transition: theme.transitions.create('width', {
      easing: theme.transitions.easing.sharp,
      duration: 250,
    }),
    '& .MuiDrawer-paper': {
      marginTop: 64,
      width: open ? DRAWER_EXPANDED_WIDTH : DRAWER_COLLAPSED_WIDTH,
      transition: theme.transitions.create('width', {
        easing: theme.transitions.easing.sharp,
        duration: 250,
      }),
      overflowX: 'hidden',
      borderRight: 'none',
      backgroundColor: theme.palette.background.paper,
      boxShadow: theme.shadows[3],
    },
  })
);

const DrawerHeader = styled('div')(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: theme.spacing(2, 2.5),
  ...theme.mixins.toolbar,
  borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
  backgroundColor: alpha(theme.palette.primary.main, 0.02),
  position: 'relative', // For the loading indicator
}));

const FilterSection = styled('div')(({ theme }) => ({
  borderRadius: theme.shape.borderRadius,
  marginBottom: theme.spacing(1.5),
  border: `1px solid ${alpha(theme.palette.divider, 0.06)}`,
  overflow: 'hidden',
  backgroundColor: alpha(theme.palette.background.paper, 0.6),
  transition: theme.transitions.create(['box-shadow', 'border-color'], {
    duration: 200,
  }),
  '&:hover': {
    borderColor: alpha(theme.palette.primary.main, 0.12),
    boxShadow: `0 2px 8px ${alpha(theme.palette.primary.main, 0.08)}`,
  },
}));

const FilterHeader = styled('div', {
  shouldForwardProp: (prop) => prop !== 'expanded',
})<{ expanded: boolean }>(({ theme, expanded }) => ({
  padding: theme.spacing(1.75, 2),
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  cursor: 'pointer',
  backgroundColor: expanded ? alpha(theme.palette.primary.main, 0.06) : 'transparent',
  transition: theme.transitions.create(['background-color'], {
    duration: 200,
  }),
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.08),
  },
}));

const FilterContent = styled(Collapse)(({ theme }) => ({
  '& > .MuiCollapse-wrapper > .MuiCollapse-wrapperInner': {
    padding: theme.spacing(1, 2, 2, 2),
  },
  maxHeight: 280,
  overflow: 'auto',
  '&::-webkit-scrollbar': {
    width: 6,
  },
  '&::-webkit-scrollbar-track': {
    background: alpha(theme.palette.divider, 0.05),
    borderRadius: 3,
  },
  '&::-webkit-scrollbar-thumb': {
    backgroundColor: alpha(theme.palette.text.secondary, 0.2),
    borderRadius: 3,
    '&:hover': {
      backgroundColor: alpha(theme.palette.text.secondary, 0.3),
    },
  },
}));

const FilterLabel = styled(Typography)(({ theme }) => ({
  fontWeight: 600,
  fontSize: '0.875rem',
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1.5),
  color: theme.palette.text.primary,
  letterSpacing: '0.01em',
}));

const FilterCheckbox = styled(Checkbox)(({ theme }) => ({
  padding: theme.spacing(0.5),
  '&.Mui-checked': {
    color: theme.palette.primary.main,
  },
}));

const FilterCount = styled(Badge)(({ theme }) => ({
  '& .MuiBadge-badge': {
    right: -12,
    top: 2,
    fontSize: '0.7rem',
    height: 18,
    minWidth: 18,
    fontWeight: 600,
    border: `2px solid ${theme.palette.background.paper}`,
    padding: '0 5px',
  },
}));

const FiltersContainer = styled(Box)(({ theme }) => ({
  height: 'calc(100vh - 128px)',
  overflow: 'auto',
  padding: theme.spacing(2, 1.5),
  '&::-webkit-scrollbar': {
    width: 6,
  },
  '&::-webkit-scrollbar-track': {
    background: 'transparent',
  },
  '&::-webkit-scrollbar-thumb': {
    backgroundColor: alpha(theme.palette.text.secondary, 0.15),
    borderRadius: 3,
    '&:hover': {
      backgroundColor: alpha(theme.palette.text.secondary, 0.25),
    },
  },
}));

const FilterChip = styled(Chip)(({ theme }) => ({
  margin: theme.spacing(0.5),
  height: 28,
  fontSize: '0.75rem',
  fontWeight: 500,
  borderRadius: 6,
  backgroundColor: theme.palette.mode === 'dark' ? alpha(theme.palette.common.white, 0.8) : alpha(theme.palette.primary.main, 0.08),
  color: theme.palette.mode === 'dark' ? alpha(theme.palette.common.black, 0.8) : theme.palette.primary.main,
  border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
  '& .MuiChip-label': {
    px: 1.5,
    py: 0.5,
  },
  transition: theme.transitions.create(['background-color', 'box-shadow'], {
    duration: 200,
  }),
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.12),
    boxShadow: `0 2px 4px ${alpha(theme.palette.primary.main, 0.15)}`,
  },
  '& .MuiChip-deleteIcon': {
    color: alpha(theme.palette.primary.main, 0.7),
    width: 16,
    height: 16,
    '&:hover': {
      color: theme.palette.primary.main,
    },
  },
}));

const ActiveFiltersContainer = styled(Paper)(({ theme }) => ({
  padding: theme.spacing(2, 2.5),
  marginBottom: theme.spacing(2.5),
  display: 'flex',
  flexWrap: 'wrap',
  backgroundColor: alpha(theme.palette.background.default, 0.4),
  borderRadius: 8,
  border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
  boxShadow: `0 2px 8px ${alpha(theme.palette.common.black, 0.04)}`,
}));

const ClearFiltersButton = styled(Button)(({ theme }) => ({
  minWidth: 'auto',
  padding: theme.spacing(0.75, 1.5),
  fontSize: '0.75rem',
  textTransform: 'none',
  color: theme.palette.primary.main,
  fontWeight: 600,
  borderRadius: 6,
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.08),
  },
}));

const FormControlLabelStyled = styled(FormControlLabel)(({ theme }) => ({
  marginBottom: 6,
  marginLeft: -8,
  borderRadius: 6,
  padding: theme.spacing(0.5, 1),
  transition: theme.transitions.create(['background-color'], {
    duration: 150,
  }),
  '&:hover': {
    backgroundColor: alpha(theme.palette.action.hover, 0.04),
  },
  '& .MuiTypography-root': {
    fontSize: '0.85rem',
    fontWeight: 400,
  },
}));

const CollapsedButtonContainer = styled(Box)<{ visible: boolean }>(({ theme, visible }) => ({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  paddingTop: theme.spacing(2),
  opacity: visible ? 1 : 0,
  transition: theme.transitions.create('opacity', {
    duration: 200,
    delay: visible ? 50 : 0,
  }),
}));

const ExpandedContentContainer = styled(Box)<{ visible: boolean }>(({ theme, visible }) => ({
  opacity: visible ? 1 : 0,
  transition: theme.transitions.create('opacity', {
    duration: 200,
    delay: visible ? 50 : 0,
  }),
  width: '100%',
}));

const LoadingIndicator = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: 10,
  right: 10,
  zIndex: 100,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: '50%',
  padding: 2,
  backgroundColor: alpha(theme.palette.background.paper, 0.9),
  boxShadow: theme.shadows[2],
}));

const StatusIndicator = styled(Box)<{ active: boolean }>(({ theme, active }) => ({
  position: 'absolute',
  bottom: -1,
  left: 0,
  right: 0,
  height: 2,
  backgroundColor: theme.palette.primary.main,
  opacity: active ? 1 : 0,
  animation: active ? `${pulse} 1.5s infinite ease-in-out` : 'none',
  transition: theme.transitions.create(['opacity'], {
    duration: 200,
  }),
}));

// Map filter keys from API to internal state (constant mapping)
const filterKeyMap: Record<string, keyof AppliedFilters> = {
  sortBy: 'sortBy',
  sortOrder: 'sortOrder',
  recordTypes: 'recordTypes',
  origins: 'origins',
  connectors: 'connectorIds',
  kbs: 'kbIds',
  indexingStatus: 'indexingStatus',
};

export default function DynamicFilterSidebar({
  availableFilters,
  appliedFilters,
  onFilterChange,
  open = true,
  onToggle,
}: DynamicFilterSidebarProps) {
  const theme = useTheme();
  const [isOpen, setIsOpen] = useState(open);
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    indexingStatus: true,
  });
  const [showCollapsedContent, setShowCollapsedContent] = useState(!open);
  const [showExpandedContent, setShowExpandedContent] = useState(open);
  const [isLoading, setIsLoading] = useState(false);
  const isFilterChanging = useRef(false);

  useEffect(() => {
    setIsOpen(open);
    if (open) {
      setShowCollapsedContent(false);
      setTimeout(() => setShowExpandedContent(true), 100);
    } else {
      setShowExpandedContent(false);
      setTimeout(() => setShowCollapsedContent(true), 100);
    }
  }, [open]);

  const handleDrawerToggle = () => {
    const newState = !isOpen;
    setIsOpen(newState);
    onToggle?.();
  };

  const toggleSection = useCallback((section: string) => {
    setExpandedSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  }, []);

  const handleFilterChange = useCallback(
    (filterKey: string, value: string, isSingleSelect: boolean = false) => {
      if (isFilterChanging.current) return;
      
      isFilterChanging.current = true;
      setIsLoading(true);

      const mappedKey = filterKeyMap[filterKey];
      
      let updatedFilters;
      if (isSingleSelect) {
        // For single select (sortBy, sortOrder), toggle between the value and undefined
        // If clicking the same value, unselect it; otherwise select the new value
        const currentValue = appliedFilters[mappedKey] as string | undefined;
        updatedFilters = {
          ...appliedFilters,
          [mappedKey]: currentValue === value ? undefined : value,
        };
      } else {
        // For multi-select (checkboxes), toggle the value in array
        const currentValues = (appliedFilters[mappedKey] as string[]) || [];
        const newValues = currentValues.includes(value)
          ? currentValues.filter((v) => v !== value)
          : [...currentValues, value];

        updatedFilters = {
          ...appliedFilters,
          [mappedKey]: newValues,
        };
      }

      requestAnimationFrame(() => {
        onFilterChange(updatedFilters);
        setTimeout(() => {
          isFilterChanging.current = false;
          setIsLoading(false);
        }, 300);
      });
    },
    [appliedFilters, onFilterChange]
  );

  const clearFilter = useCallback(
    (filterKey: keyof AppliedFilters, value: string) => {
      if (isFilterChanging.current) return;
      
      isFilterChanging.current = true;
      setIsLoading(true);

      const currentValue = appliedFilters[filterKey];
      let updatedFilters;
      
      if (Array.isArray(currentValue)) {
        // For array filters, remove the value
        updatedFilters = {
          ...appliedFilters,
          [filterKey]: currentValue.filter((v: string) => v !== value),
        };
      } else {
        // For single-select filters (sortBy, sortOrder), clear to undefined
        updatedFilters = {
          ...appliedFilters,
          [filterKey]: undefined,
        };
      }

      requestAnimationFrame(() => {
        onFilterChange(updatedFilters);
        setTimeout(() => {
          isFilterChanging.current = false;
          setIsLoading(false);
        }, 300);
      });
    },
    [appliedFilters, onFilterChange]
  );

  const clearAllFilters = useCallback(() => {
    if (isFilterChanging.current) return;
    
    isFilterChanging.current = true;
    setIsLoading(true);

    const emptyFilters: AppliedFilters = {
      recordTypes: [],
      origins: [],
      connectorIds: [],
      kbIds: [],
      indexingStatus: [],
    };

    requestAnimationFrame(() => {
      onFilterChange(emptyFilters);
      setTimeout(() => {
        isFilterChanging.current = false;
        setIsLoading(false);
      }, 300);
    });
  }, [onFilterChange]);

  const getFilterLabel = useCallback(
    (filterKey: keyof AppliedFilters, value: string): string => {
      const reverseMap: Record<keyof AppliedFilters, keyof AvailableFilters> = {
        sortBy: 'sortBy',
        sortOrder: 'sortOrder',
        recordTypes: 'recordTypes',
        origins: 'origins',
        connectorIds: 'connectors',
        kbIds: 'kbs',
        indexingStatus: 'indexingStatus',
      };

      const availableKey = reverseMap[filterKey];
      const options = availableFilters[availableKey] || [];
      return options.find((opt) => opt.id === value)?.label || value;
    },
    [availableFilters]
  );

  const activeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    Object.entries(appliedFilters).forEach(([key, values]) => {
      if (!values) {
        counts[key] = 0;
      } else if (Array.isArray(values)) {
        counts[key] = values.length;
      } else {
        // Single-select filter (string value)
        counts[key] = 1;
      }
    });
    return counts;
  }, [appliedFilters]);

  const totalActiveFilterCount = useMemo(
    () => Object.values(activeCounts).reduce((acc, count) => acc + count, 0),
    [activeCounts]
  );

  // Helper function to get connector icon path
  const getConnectorIconPath = useCallback((connectorType?: string): string => {
    if (!connectorType) return '/assets/icons/connectors/default.svg';
    const normalizedType = connectorType.toLowerCase().trim();
    return `/assets/icons/connectors/${normalizedType}.svg`;
  }, []);

  const renderFilterSection = (
    key: string,
    title: string,
    icon: any,
    options: FilterOption[],
    isSingleSelect: boolean = false
  ) => {
    const mappedKey = filterKeyMap[key];
    const activeValues = appliedFilters[mappedKey];
    const activeValue = isSingleSelect ? (activeValues as string) : (activeValues as string[] || []);
    const isExpanded = expandedSections[key] || false;
    const hasActiveFilter = isSingleSelect ? !!activeValue : (activeValues as string[] || []).length > 0;

    return (
      <FilterSection key={key}>
        <FilterHeader expanded={isExpanded} onClick={() => toggleSection(key)}>
          <FilterLabel>
            <Icon
              icon={icon}
              fontSize={20}
              color={isExpanded ? theme.palette.primary.main : theme.palette.text.secondary}
            />
            {title}
            {hasActiveFilter && !isSingleSelect && (
              <FilterCount badgeContent={(activeValues as string[]).length} color="primary" />
            )}
          </FilterLabel>
          <Icon
            icon={isExpanded ? upIcon : downIcon}
            fontSize={20}
            color={theme.palette.text.secondary}
          />
        </FilterHeader>
        <FilterContent in={isExpanded}>
          <FormGroup>
            {options.map((option) => {
              const isChecked = isSingleSelect 
                ? activeValue === option.id 
                : (activeValues as string[] || []).includes(option.id);
              
              return (
                <FormControlLabelStyled
                  key={option.id}
                  control={
                    <FilterCheckbox
                      checked={isChecked}
                      onChange={() => handleFilterChange(key, option.id, isSingleSelect)}
                      size="small"
                      disableRipple
                    />
                  }
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 1 }}>
                      {option.connectorType ? (
                        // Connector icons: use img tag with SVG path
                        <Box
                          component="img"
                          src={getConnectorIconPath(option.connectorType)}
                          alt={option.label}
                          sx={{
                            width: 16,
                            height: 16,
                            objectFit: 'contain',
                            opacity: isChecked ? 1 : 0.7,
                          }}
                          onError={(e) => {
                            e.currentTarget.src = '/assets/icons/connectors/default.svg';
                          }}
                        />
                      ) : (
                        // Other filter options: use MDI icons
                        <Icon
                          icon={getFilterOptionIcon(option.id)}
                          fontSize={16}
                          style={{
                            opacity: isChecked ? 1 : 0.7,
                            color: isChecked ? theme.palette.primary.main : theme.palette.text.secondary,
                          }}
                        />
                      )}
                      <Typography variant="body2">{option.label}</Typography>
                    </Box>
                  }
                />
              );
            })}
          </FormGroup>
        </FilterContent>
      </FilterSection>
    );
  };

  const renderActiveFilters = () => {
    if (totalActiveFilterCount === 0) return null;

    return (
      <ActiveFiltersContainer elevation={0}>
        <Box
          sx={{
            width: '100%',
            display: 'flex',
            justifyContent: 'space-between',
            mb: 1.5,
            alignItems: 'center',
          }}
        >
          <Typography variant="body2" fontWeight={700} color="primary" sx={{ fontSize: '0.875rem' }}>
            Active Filters ({totalActiveFilterCount})
          </Typography>
          <ClearFiltersButton
            variant="text"
            size="small"
            onClick={clearAllFilters}
            disableRipple
            startIcon={<Icon icon={filterRemoveIcon} fontSize={14} />}
          >
            Clear All
          </ClearFiltersButton>
        </Box>
        <Box sx={{ display: 'flex', flexWrap: 'wrap' }}>
          {Object.entries(appliedFilters).map(([filterKey, values]) => {
            // Handle both single-select (string) and multi-select (array) filters
            if (!values) return null;
            
            const valueArray = Array.isArray(values) ? values : [values];
            return valueArray.map((value: string) => (
              <FilterChip
                key={`${filterKey}-${value}`}
                label={getFilterLabel(filterKey as keyof AppliedFilters, value)}
                size="small"
                onDelete={() => clearFilter(filterKey as keyof AppliedFilters, value)}
                deleteIcon={<Icon icon={closeIcon} fontSize={14} />}
              />
            ));
          })}
        </Box>
      </ActiveFiltersContainer>
    );
  };

  const filterSections = [
    { key: 'indexingStatus', title: 'Status', icon: filterVariantIcon, data: availableFilters.indexingStatus, isSingleSelect: false },
    { key: 'recordTypes', title: 'Record Type', icon: fileDocumentIcon, data: availableFilters.recordTypes, isSingleSelect: false },
    { key: 'origins', title: 'Origin', icon: sourceBranchIcon, data: availableFilters.origins, isSingleSelect: false },
    { key: 'connectors', title: 'Connectors', icon: connectionIcon, data: availableFilters.connectors, isSingleSelect: false },
    { key: 'kbs', title: 'Knowledge Bases', icon: bookOpenVariantIcon, data: availableFilters.kbs, isSingleSelect: false },
    { key: 'sortBy', title: 'Sort By', icon: sortIcon, data: availableFilters.sortBy, isSingleSelect: true },
    { key: 'sortOrder', title: 'Sort Order', icon: sortVariantIcon, data: availableFilters.sortOrder, isSingleSelect: true },
  ];

  return (
    <StyledDrawer variant="permanent" open={isOpen}>
      <DrawerHeader>
        {isOpen ? (
          <>
            <Typography variant="subtitle1" fontWeight={700} sx={{ color: theme.palette.primary.main }}>
              <Box component="span" sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <Icon icon={filterMenuIcon} fontSize={22} />
                Filters
              </Box>
            </Typography>

            <Fade in={isLoading} timeout={150}>
              <LoadingIndicator>
                <CircularProgress size={16} thickness={4} />
              </LoadingIndicator>
            </Fade>

            <Tooltip title="Collapse sidebar" placement="right">
              <IconButton
                onClick={handleDrawerToggle}
                size="small"
                sx={{ color: theme.palette.text.secondary }}
              >
                <Icon icon={leftIcon} width={20} height={20} />
              </IconButton>
            </Tooltip>

            <StatusIndicator active={isLoading} />
          </>
        ) : (
          <>
            <Tooltip title="Expand sidebar" placement="right">
              <IconButton
                onClick={handleDrawerToggle}
                sx={{ mx: 'auto', color: theme.palette.primary.main }}
              >
                <Icon icon={rightIcon} width={20} height={20} />
              </IconButton>
            </Tooltip>
            <StatusIndicator active={isLoading} />
          </>
        )}
      </DrawerHeader>

      {!isOpen ? (
        <CollapsedButtonContainer visible={showCollapsedContent}>
          <Tooltip title="Expand to see filters" placement="right">
            <IconButton color="primary" onClick={handleDrawerToggle}>
              <Badge badgeContent={totalActiveFilterCount} color="primary">
                <Icon icon={filterVariantIcon} fontSize={24} />
              </Badge>
            </IconButton>
          </Tooltip>
        </CollapsedButtonContainer>
      ) : (
        <ExpandedContentContainer visible={showExpandedContent}>
          <FiltersContainer>
            {renderActiveFilters()}
            {filterSections.map(
              (section) =>
                section.data &&
                section.data.length > 0 &&
                renderFilterSection(section.key, section.title, section.icon, section.data, section.isSingleSelect)
            )}
          </FiltersContainer>
        </ExpandedContentContainer>
      )}
    </StyledDrawer>
  );
}

