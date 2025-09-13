import type { Icon as IconifyIcon } from '@iconify/react';

import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/mdi/close';
import upIcon from '@iconify-icons/mdi/chevron-up';
import leftIcon from '@iconify-icons/mdi/chevron-left';
import downIcon from '@iconify-icons/mdi/chevron-down';
import fileIcon from '@iconify-icons/mdi/file-outline';
import rightIcon from '@iconify-icons/mdi/chevron-right';
import emailIcon from '@iconify-icons/mdi/email-outline';
import { useLocation, useNavigate } from 'react-router-dom';
import filterMenuIcon from '@iconify-icons/mdi/filter-menu';
import filterRemoveIcon from '@iconify-icons/mdi/filter-remove';
import timerOffIcon from '@iconify-icons/mdi/timer-off-outline';
import filterVariantIcon from '@iconify-icons/mdi/filter-variant';
import circleOutlineIcon from '@iconify-icons/mdi/circle-outline';
import progressClockIcon from '@iconify-icons/mdi/progress-clock';
import fileAlertIcon from '@iconify-icons/mdi/file-alert-outline';
import React, { useRef, useMemo, useState, useEffect } from 'react';
import userCheckIcon from '@iconify-icons/mdi/account-check-outline';
import closeCircleIcon from '@iconify-icons/mdi/close-circle-outline';
import cloudUploadIcon from '@iconify-icons/mdi/cloud-upload-outline';
import cloudConnectorIcon from '@iconify-icons/mdi/cloud-sync-outline';
import alertCircleOutlineIcon from '@iconify-icons/mdi/alert-circle-outline';
import checkCircleOutlineIcon from '@iconify-icons/mdi/check-circle-outline';

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

import type { Modules } from './types/modules';
import type { Departments } from './types/departments';
import type { SearchTagsRecords } from './types/search-tags';
import type { RecordCategories } from './types/record-categories';
import type { Filters, FilterHeaderProps, KnowledgeBaseSideBarProps } from './types/knowledge-base';
import { useConnectors } from '../accountdetails/connectors/context';

// Constants
const DRAWER_EXPANDED_WIDTH = 280;
const DRAWER_COLLAPSED_WIDTH = 60;

// Define a subtle pulse animation
const pulse = keyframes`
  0% {
    opacity: 0.6;
  }
  50% {
    opacity: 0.8;
  }
  100% {
    opacity: 0.6;
  }
`;

// Custom styled components with optimized transitions
const OpenedDrawer = styled(Drawer, { shouldForwardProp: (prop) => prop !== 'open' })(
  ({ theme, open }) => ({
    width: open ? DRAWER_EXPANDED_WIDTH : DRAWER_COLLAPSED_WIDTH,
    flexShrink: 0,
    marginTop: 50,
    whiteSpace: 'nowrap',
    boxSizing: 'border-box',
    // Use will-change for better GPU optimization
    willChange: 'width',
    transition: theme.transitions.create('width', {
      easing: theme.transitions.easing.sharp,
      duration: '0.25s',
    }),
    '& .MuiDrawer-paper': {
      marginTop: 64,
      width: open ? DRAWER_EXPANDED_WIDTH : DRAWER_COLLAPSED_WIDTH,
      transition: theme.transitions.create(['width'], {
        easing: theme.transitions.easing.sharp,
        duration: '0.25s',
      }),
      overflowX: 'hidden',
      borderRight: 'none',
      backgroundColor: theme.palette.background.paper,
      boxShadow: theme.shadows[2],
    },
  })
);

const DrawerHeader = styled('div')(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: theme.spacing(2, 2.5),
  ...theme.mixins.toolbar,
  borderBottom: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
  position: 'relative', // For the loading indicator
}));

const FilterSection = styled('div')(({ theme }) => ({
  borderRadius: theme.shape.borderRadius,
  marginBottom: theme.spacing(1),
  overflow: 'hidden',
}));

// Updated to accept the expanded prop properly with a type
const FilterHeader = styled('div', {
  shouldForwardProp: (prop) => prop !== 'expanded',
})<FilterHeaderProps>(({ theme, expanded }) => ({
  padding: theme.spacing(1.5, 2),
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  cursor: 'pointer',
  borderRadius: theme.shape.borderRadius,
  backgroundColor: expanded ? alpha(theme.palette.primary.main, 0.04) : 'transparent',
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.08),
  },
}));

const FilterContent = styled(Collapse)(({ theme }) => ({
  padding: theme.spacing(0.5, 2, 1.5, 2),
  maxHeight: 260,
  overflow: 'auto',
  '&::-webkit-scrollbar': {
    width: '4px',
  },
  '&::-webkit-scrollbar-track': {
    background: 'transparent',
  },
  '&::-webkit-scrollbar-thumb': {
    backgroundColor: alpha(theme.palette.text.secondary, 0.2),
    borderRadius: '4px',
  },
  transition: theme.transitions.create(['height'], {
    duration: '0.2s',
    easing: theme.transitions.easing.sharp,
  }),
}));

const FilterLabel = styled(Typography)(({ theme }) => ({
  fontWeight: 500,
  fontSize: '0.9rem',
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1.5),
  color: theme.palette.text.primary,
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
    border: `2px solid ${theme.palette.background.paper}`,
    padding: '0 4px',
  },
}));

const FiltersContainer = styled(Box)(({ theme }) => ({
  height: 'calc(100vh - 110px)',
  overflow: 'auto',
  padding: theme.spacing(2, 1.5),
  '&::-webkit-scrollbar': {
    width: '4px',
  },
  '&::-webkit-scrollbar-track': {
    background: 'transparent',
  },
  '&::-webkit-scrollbar-thumb': {
    backgroundColor: alpha(theme.palette.text.secondary, 0.15),
    borderRadius: '10px',
  },
  transition: theme.transitions.create('opacity', {
    duration: '0.2s',
    easing: theme.transitions.easing.sharp,
  }),
}));

const FilterChip = styled(Chip)(({ theme }) => ({
  margin: theme.spacing(0.5),
  height: 24,
  fontSize: '0.75rem',
  fontWeight: 500,
  borderRadius: '4px',
  backgroundColor: theme.palette.mode !== 'dark' ? alpha(theme.palette.primary.main, 0) : '',
  color:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.primary.lighter, 0.9)
      : theme.palette.primary.main,
  border:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.primary.main, 0.2)}`
      : `1px solid ${alpha(theme.palette.primary.main, 0.15)}`,
  '& .MuiChip-label': {
    px: 1.2,
    py: 0.5,
    letterSpacing: '0.01em',
  },
  transition: theme.transitions.create(['background-color', 'box-shadow'], {
    duration: '0.2s',
    easing: theme.transitions.easing.easeInOut,
  }),
  '&:hover': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.primary.main, 0.2)
        : alpha(theme.palette.primary.main, 0.12),
    boxShadow:
      theme.palette.mode === 'dark'
        ? `0 1px 3px ${alpha(theme.palette.common.black, 0.2)}`
        : 'none',
  },
  '& .MuiChip-deleteIcon': {
    color:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.primary.light, 1)
        : alpha(theme.palette.primary.main, 0.7),
    width: 16,
    height: 16,
    marginRight: theme.spacing(0.5),
    '&:hover': {
      color:
        theme.palette.mode === 'dark' ? theme.palette.primary.light : theme.palette.primary.main,
    },
  },
}));

const ActiveFiltersContainer = styled(Paper)(({ theme }) => ({
  padding: theme.spacing(1.5, 2),
  marginBottom: theme.spacing(2.5),
  display: 'flex',
  flexWrap: 'wrap',
  backgroundColor: alpha(theme.palette.background.default, 0.5),
  borderRadius: theme.shape.borderRadius,
  border: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
  position: 'relative', // For the loading overlay
}));

const ClearFiltersButton = styled(Button)(({ theme }) => ({
  minWidth: 'auto',
  padding: theme.spacing(0.5, 1),
  fontSize: '0.75rem',
  textTransform: 'none',
  color: theme.palette.primary.main,
  fontWeight: 500,
  transition: theme.transitions.create('background-color', {
    duration: '0.1s',
    easing: theme.transitions.easing.sharp,
  }),
}));

const FormControlLabelStyled = styled(FormControlLabel)(({ theme }) => ({
  marginBottom: 4,
  marginLeft: -8,
  '& .MuiTypography-root': {
    fontSize: '0.85rem',
    fontWeight: 400,
  },
  opacity: 1,
  '&:hover': {
    opacity: 0.9,
  },
}));

const CollapsedButtonContainer = styled(Box, {
  shouldForwardProp: (prop) => prop !== 'visible',
})<{ visible: boolean }>(({ theme, visible }) => ({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  paddingTop: theme.spacing(2),
  opacity: visible ? 1 : 0,
  transition: theme.transitions.create('opacity', {
    duration: '0.2s',
    easing: theme.transitions.easing.sharp,
    delay: visible ? '0.05s' : '0s',
  }),
}));

const ExpandedContentContainer = styled(Box, {
  shouldForwardProp: (prop) => prop !== 'visible',
})<{ visible: boolean }>(({ theme, visible }) => ({
  opacity: visible ? 1 : 0,
  transition: theme.transitions.create('opacity', {
    duration: '0.2s',
    easing: theme.transitions.easing.sharp,
    delay: visible ? '0.05s' : '0s',
  }),
  width: '100%',
  position: 'relative',
}));

const IconButtonStyled = styled(IconButton)(({ theme }) => ({
  transition: theme.transitions.create('background-color', {
    duration: '0.1s',
    easing: theme.transitions.easing.sharp,
  }),
  '&:hover': {
    transform: 'scale(1.05)',
  },
  '&:active': {
    transform: 'scale(0.95)',
  },
}));

// Clean, minimalist loading indicator that appears in the top-right corner
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
  backgroundColor: alpha(theme.palette.background.paper, 0.8),
  boxShadow: theme.shadows[1],
}));

// A clean status indicator that shows without disrupting the UI
const StatusIndicator = styled(Box, {
  shouldForwardProp: (prop) => prop !== 'active',
})<{ active: boolean }>(({ theme, active }) => ({
  position: 'absolute',
  bottom: -3,
  left: 0,
  right: 0,
  height: 3,
  backgroundColor: theme.palette.primary.main,
  opacity: active ? 1 : 0,
  animation: active ? `${pulse} 1.5s infinite ease-in-out` : 'none',
  transition: theme.transitions.create(['opacity'], {
    duration: '0.2s',
    easing: theme.transitions.easing.sharp,
  }),
}));

// Status icons mapping
const statusIcons: Record<string, React.ComponentProps<typeof IconifyIcon>['icon']> = {
  NOT_STARTED: circleOutlineIcon,
  IN_PROGRESS: progressClockIcon,
  FAILED: alertCircleOutlineIcon,
  COMPLETED: checkCircleOutlineIcon,
  FILE_TYPE_NOT_SUPPORTED: fileAlertIcon,
  AUTO_INDEX_OFF: timerOffIcon,
};

// Helper function to format labels
const formatLabel = (label: string): string => {
  if (!label) return '';
  if (label === 'AUTO_INDEX_OFF') return 'Maual Sync';
  return label
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, (l: string) => l.toUpperCase());
};

// Helper function to create empty filters object
const createEmptyFilters = (): Filters => ({
  indexingStatus: [],
  department: [],
  moduleId: [],
  searchTags: [],
  appSpecificRecordType: [],
  recordTypes: [],
  origin: [],
  status: [],
  connectors: [],
  app: [],
  permissions: [],
});

export default function KnowledgeBaseSideBar({
  filters,
  onFilterChange,
  openSidebar = true,
  onToggleSidebar,
}: KnowledgeBaseSideBarProps) {
  const theme = useTheme();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState<boolean>(true);
  const [departments, setDepartments] = useState<Departments[]>([]);
  const [recordCategories, setRecordCategories] = useState<RecordCategories[]>([]);
  const [modules, setModules] = useState<Modules[]>([]);
  const [tags, setTags] = useState<SearchTagsRecords[]>([]);

  // Use the connector hook for managing connector state
  const {
    activeConnectors,
    inactiveConnectors,
    loading: connectorsLoading,
    error: connectorsError,
  } = useConnectors();

  // Initialize expanded sections with all sections collapsed except status
  const [expandedSections, setExpandedSections] = useState<{ [key: string]: boolean }>({
    status: true,
    recordType: false,
    origin: false,
    connector: false,
    permissions: false,
    departments: false,
    modules: false,
    tags: false,
    categories: false,
  });

  // Add a ref to track if filter operation is in progress
  const isFilterChanging = useRef(false);
  // Add a loading state for filter changes
  const [isLoading, setIsLoading] = useState(false);
  // Keep local copy of filters to prevent UI flicker during updates
  const [localFilters, setLocalFilters] = useState<Filters>(filters || createEmptyFilters());

  // Store previous filters for comparison
  const prevFiltersRef = useRef<Filters | null>(null);

  // Smoothly manage content appearance
  const [showCollapsedContent, setShowCollapsedContent] = useState(!openSidebar);
  const [showExpandedContent, setShowExpandedContent] = useState(openSidebar);

  // Status color mapping using theme colors
  const statusColors: Record<string, string> = React.useMemo(
    () => ({
      NOT_STARTED: theme.palette.grey[500],
      IN_PROGRESS: theme.palette.info.main,
      FAILED: theme.palette.error.main,
      COMPLETED: theme.palette.success.main,
      FILE_TYPE_NOT_SUPPORTED: theme.palette.warning.main,
      AUTO_INDEX_OFF: theme.palette.grey[600],
    }),
    [
      theme.palette.grey,
      theme.palette.info.main,
      theme.palette.error.main,
      theme.palette.success.main,
      theme.palette.warning.main,
    ]
  );

  // Record type icon mapping
  const recordTypeIcons = React.useMemo<
    Record<string, React.ComponentProps<typeof IconifyIcon>['icon']>
  >(
    () => ({
      FILE: fileIcon,
      MAIL: emailIcon,
    }),
    []
  );

  // Origin icon mapping
  const originIcons = React.useMemo<
    Record<string, React.ComponentProps<typeof IconifyIcon>['icon']>
  >(
    () => ({
      UPLOAD: cloudUploadIcon,
      CONNECTOR: cloudConnectorIcon,
    }),
    []
  );

  // Permission icon mapping
  const permissionIcons = React.useMemo<
    Record<string, React.ComponentProps<typeof IconifyIcon>['icon']>
  >(
    () => ({
      READER: userCheckIcon,
      OWNER: userCheckIcon,
      WRITER: userCheckIcon,
      COMMENTER: userCheckIcon,
      FILEORGANIZER: userCheckIcon,
      ORGANIZER: userCheckIcon,
    }),
    []
  );

  const toggleInProgress = useRef(false);

  // Ensure filters have arrays for all properties
  useEffect(() => {
    // Initialize filters with empty arrays for all properties if they don't exist
    const normalizedFilters: Filters = { ...createEmptyFilters() };

    // Copy values from props if they exist and are arrays
    if (filters) {
      Object.keys(normalizedFilters).forEach((key) => {
        const filterKey = key as keyof Filters;
        if (filters[filterKey] && Array.isArray(filters[filterKey])) {
          normalizedFilters[filterKey] = [...filters[filterKey]!];
        }
      });
    }

    setLocalFilters(normalizedFilters);
    prevFiltersRef.current = JSON.parse(JSON.stringify(normalizedFilters));
  }, [filters]);

  // Use memo to cache active filter counts
  const activeCounts = useMemo(() => {
    const counts: Record<keyof Filters, number> = {
      indexingStatus: 0,
      department: 0,
      moduleId: 0,
      searchTags: 0,
      appSpecificRecordType: 0,
      recordTypes: 0,
      origin: 0,
      status: 0,
      connectors: 0,
      app: 0,
      permissions: 0,
      kb: 0,
    };

    // Calculate counts from local filters to prevent UI flicker
    Object.entries(localFilters).forEach(([key, values]) => {
      counts[key as keyof Filters] = Array.isArray(values) ? values.length : 0;
    });

    return counts;
  }, [localFilters]);

  // Use memo to cache total active filter count
  const totalActiveFilterCount = useMemo(
    () => Object.values(activeCounts).reduce((acc, count) => acc + count, 0),
    [activeCounts]
  );

  // Update local filters when props change, with added stability mechanism
  useEffect(() => {
    if (!filters) return;

    // Create a normalized version of the incoming filters
    const normalizedFilters: Filters = { ...createEmptyFilters() };

    // Copy all filter values, ensuring they are arrays
    Object.keys(normalizedFilters).forEach((key) => {
      const filterKey = key as keyof Filters;
      if (filters[filterKey] && Array.isArray(filters[filterKey])) {
        normalizedFilters[filterKey] = [...filters[filterKey]!];
      }
    });

    // Compare with prev filters to avoid unnecessary updates
    if (
      !prevFiltersRef.current ||
      JSON.stringify(prevFiltersRef.current) !== JSON.stringify(normalizedFilters)
    ) {
      // Only update if there's a real change
      setLocalFilters(normalizedFilters);
      prevFiltersRef.current = JSON.parse(JSON.stringify(normalizedFilters));
    }
  }, [filters]);

  // Sync internal state with prop
  useEffect(() => {
    setOpen(openSidebar);

    // Manage content visibility with a slight delay for smooth transitions
    if (openSidebar) {
      setShowCollapsedContent(false);
      setTimeout(() => setShowExpandedContent(true), 100);
    } else {
      setShowExpandedContent(false);
      setTimeout(() => setShowCollapsedContent(true), 100);
    }
  }, [openSidebar]);

  const handleDrawerToggle = () => {
    const newOpenState = !open;
    setOpen(newOpenState);

    if (onToggleSidebar) {
      onToggleSidebar();
    }
  };

  const toggleSection = React.useCallback((section: string) => {
    // Prevent multiple toggles from happening simultaneously
    if (toggleInProgress.current) return;

    // Set flag to indicate toggle is in progress
    toggleInProgress.current = true;

    // Use functional state update to get the most current state
    setExpandedSections((prevSections) => {
      // Create a fresh copy to avoid any state mutations
      const updatedSections = { ...prevSections };

      // Toggle only the specific section that was clicked
      updatedSections[section] = !prevSections[section];

      // Return the new object with only the requested section changed
      return updatedSections;
    });

    // Reset the toggle flag after a short delay
    setTimeout(() => {
      toggleInProgress.current = false;
    }, 50);
  }, []);

  // Use a ref to track mounted state to prevent state updates after unmount
  const isMounted = useRef(true);

  // Set up mount/unmount tracking
  useEffect(() => {
    isMounted.current = true;

    return () => {
      isMounted.current = false;
    };
  }, []);

  // Fix for the handleFilterChange function to isolate filter types completely
  const handleFilterChange = React.useCallback(
    (filterType: keyof Filters, value: string) => {
      // If a filter operation is already in progress, return to prevent flickering
      if (isFilterChanging.current) return;

      // Set the flag to indicate a filter operation is in progress
      isFilterChanging.current = true;
      // Show loading indicator
      setIsLoading(true);

      // Create a new copy of the current filters to avoid reference issues
      const updatedLocalFilters = JSON.parse(JSON.stringify(localFilters)) as Filters;

      // Ensure the filter property exists and is an array
      if (!Array.isArray(updatedLocalFilters[filterType])) {
        updatedLocalFilters[filterType] = [];
      }

      // Get the current array for this filter type
      const currentValues = updatedLocalFilters[filterType];

      // Check if value exists in the array
      const valueIndex = currentValues.indexOf(value);

      // Toggle the value
      if (valueIndex === -1) {
        // Value doesn't exist, so add it
        updatedLocalFilters[filterType] = [...currentValues, value];
      } else {
        // Value exists, so remove it
        updatedLocalFilters[filterType] = [
          ...currentValues.slice(0, valueIndex),
          ...currentValues.slice(valueIndex + 1),
        ];
      }

      // Update local state immediately for responsive UI
      setLocalFilters(updatedLocalFilters);

      // Use requestAnimationFrame to batch UI updates
      requestAnimationFrame(() => {
        // Update parent without causing a re-render of this component
        // Pass a deep copy to avoid reference issues
        onFilterChange(JSON.parse(JSON.stringify(updatedLocalFilters)));

        // Reset the flag and loading state after a short delay
        setTimeout(() => {
          isFilterChanging.current = false;
          setIsLoading(false);
        }, 300);
      });
    },
    [localFilters, onFilterChange]
  );

  // Get filter item names by IDs
  const getFilterName = (type: keyof Filters, id: string): string => {
    switch (type) {
      case 'department':
        return departments.find((d) => d._id === id)?.name || id;
      case 'moduleId':
        return modules.find((m) => m._id === id)?.name || id;
      case 'searchTags':
        return tags.find((t) => t._id === id)?.name || id;
      case 'appSpecificRecordType':
        return recordCategories.find((c) => c._id === id)?.name || id;
      case 'indexingStatus':
      case 'recordTypes':
      case 'origin':
      case 'connectors':
      case 'permissions':
        return formatLabel(id);
      default:
        return id;
    }
  };

  // Clear a specific filter
  const clearFilter = (type: keyof Filters, value: string) => {
    // If a filter operation is already in progress, return
    if (isFilterChanging.current) return;

    isFilterChanging.current = true;
    setIsLoading(true);

    // Create deep copies to avoid mutations
    const updatedLocalFilters = JSON.parse(JSON.stringify(localFilters));

    // Ensure the property exists and is an array
    if (!Array.isArray(updatedLocalFilters[type])) {
      updatedLocalFilters[type] = [];
    } else {
      // Filter out the value to remove
      updatedLocalFilters[type] = updatedLocalFilters[type].filter(
        (item: string) => item !== value
      );
    }

    // Update local state
    setLocalFilters(updatedLocalFilters);

    // Send to parent component
    requestAnimationFrame(() => {
      onFilterChange(JSON.parse(JSON.stringify(updatedLocalFilters)));

      setTimeout(() => {
        isFilterChanging.current = false;
        setIsLoading(false);
      }, 300);
    });
  };

  // Clear all filters
  // Clear all filters - wrapped in useCallback
  const clearAllFilters = React.useCallback(() => {
    // If a filter operation is already in progress, return
    if (isFilterChanging.current) return;

    isFilterChanging.current = true;
    setIsLoading(true);

    // Create an empty filters object
    const emptyFilters = createEmptyFilters();

    // Update local state immediately
    setLocalFilters(emptyFilters);

    requestAnimationFrame(() => {
      onFilterChange(JSON.parse(JSON.stringify(emptyFilters)));

      setTimeout(() => {
        isFilterChanging.current = false;
        setIsLoading(false);
      }, 300);
    });
  }, [onFilterChange]);

  // Improved handleCollapsedFilterClick with better section handling - wrapped in useCallback
  const handleCollapsedFilterClick = React.useCallback(
    (sectionId: string, filterType: keyof Filters) => {
      // If drawer is closed, open it first
      if (!open) {
        setOpen(true);
        if (onToggleSidebar) {
          onToggleSidebar();
        }

        // After drawer opens, expand only the section clicked
        // Use setTimeout to ensure state updates happen in sequence
        setTimeout(() => {
          setExpandedSections((prevSections) => ({
            ...prevSections,
            [sectionId]: true,
          }));
        }, 100);
      } else {
        // If drawer is already open, just toggle the section
        toggleSection(sectionId);
      }
    },
    [open, onToggleSidebar, toggleSection]
  );

  // Generate active filters view
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
          <Typography variant="body2" fontWeight={600} color="primary" sx={{ fontSize: '0.85rem' }}>
            Active Filters ({totalActiveFilterCount})
          </Typography>
          <ClearFiltersButton
            variant="text"
            size="small"
            onClick={clearAllFilters}
            disableRipple
            startIcon={<Icon icon={closeCircleIcon} fontSize="small" />}
          >
            Clear All
          </ClearFiltersButton>
        </Box>
        <Box sx={{ display: 'flex', flexWrap: 'wrap' }}>
          {Object.entries(localFilters).map(([type, values]) =>
            (values || []).map((value: string) => (
              <FilterChip
                key={`${type}-${value}`}
                label={getFilterName(type as keyof Filters, value)}
                size="small"
                onDelete={() => clearFilter(type as keyof Filters, value)}
                deleteIcon={<Icon icon={closeIcon} fontSize="small" />}
              />
            ))
          )}
        </Box>
      </ActiveFiltersContainer>
    );
  };

  // Filter section for Record Types
  const RecordTypeFilterSection = useMemo(
    () => (
      <FilterSection>
        <FilterHeader
          expanded={expandedSections.recordType || false}
          onClick={() => toggleSection('recordType')}
        >
          <FilterLabel>
            <Icon
              icon={fileIcon}
              fontSize="large"
              color={
                expandedSections.recordType
                  ? theme.palette.primary.main
                  : theme.palette.text.secondary
              }
            />
            Record Type
            {activeCounts.recordTypes > 0 && (
              <FilterCount badgeContent={activeCounts.recordTypes} color="primary" />
            )}
          </FilterLabel>
          <Icon
            icon={expandedSections.recordType ? upIcon : downIcon}
            fontSize="large"
            color={theme.palette.text.secondary}
          />
        </FilterHeader>
        <FilterContent in={expandedSections.recordType || false}>
          <FormGroup>
            {['FILE', 'MAIL'].map((type) => {
              const isChecked = (localFilters.recordTypes || []).includes(type);

              return (
                <FormControlLabelStyled
                  key={type}
                  control={
                    <FilterCheckbox
                      checked={isChecked}
                      onClick={() => handleFilterChange('recordTypes', type)}
                      size="small"
                      disableRipple
                    />
                  }
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Icon
                        icon={recordTypeIcons[type]}
                        color={theme.palette.text.secondary}
                        width={16}
                        height={16}
                      />
                      {formatLabel(type)}
                    </Box>
                  }
                />
              );
            })}
          </FormGroup>
        </FilterContent>
      </FilterSection>
    ),
    [
      expandedSections.recordType,
      activeCounts.recordTypes,
      localFilters.recordTypes,
      theme,
      handleFilterChange,
      recordTypeIcons,
      toggleSection,
    ]
  );

  // Filter section for Origin
  const OriginFilterSection = useMemo(
    () => (
      <FilterSection>
        <FilterHeader
          expanded={expandedSections.origin || false}
          onClick={() => toggleSection('origin')}
        >
          <FilterLabel>
            <Icon
              icon={cloudUploadIcon}
              fontSize="large"
              color={
                expandedSections.origin ? theme.palette.primary.main : theme.palette.text.secondary
              }
            />
            Origin
            {activeCounts.origin > 0 && (
              <FilterCount badgeContent={activeCounts.origin} color="primary" />
            )}
          </FilterLabel>
          <Icon
            icon={expandedSections.origin ? upIcon : downIcon}
            fontSize="large"
            color={theme.palette.text.secondary}
          />
        </FilterHeader>
        <FilterContent in={expandedSections.origin || false}>
          <FormGroup>
            {['UPLOAD', 'CONNECTOR'].map((origin) => {
              const isChecked = (localFilters.origin || []).includes(origin);

              return (
                <FormControlLabelStyled
                  key={origin}
                  control={
                    <FilterCheckbox
                      checked={isChecked}
                      onClick={() => handleFilterChange('origin', origin)}
                      size="small"
                      disableRipple
                    />
                  }
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Icon
                        icon={originIcons[origin]}
                        color={theme.palette.text.secondary}
                        width={16}
                        height={16}
                      />
                      {origin === 'UPLOAD' ? 'Local Upload' : 'Connector'}
                    </Box>
                  }
                />
              );
            })}
          </FormGroup>
        </FilterContent>
      </FilterSection>
    ),
    [
      expandedSections.origin,
      activeCounts.origin,
      localFilters.origin,
      theme,
      handleFilterChange,
      originIcons,
      toggleSection,
    ]
  );

  // Filter section for Connectors
  const ConnectorFilterSection = useMemo(
    () => (
      <FilterSection>
        <FilterHeader
          expanded={expandedSections.connector || false}
          onClick={() => toggleSection('connector')}
        >
          <FilterLabel>
            <Icon
              icon={cloudConnectorIcon}
              fontSize="large"
              color={
                expandedSections.connector
                  ? theme.palette.primary.main
                  : theme.palette.text.secondary
              }
            />
            Connectors
            {activeCounts.connectors > 0 && (
              <FilterCount badgeContent={activeCounts.connectors} color="primary" />
            )}
          </FilterLabel>
          <Icon
            icon={expandedSections.connector ? upIcon : downIcon}
            fontSize="large"
            color={theme.palette.text.secondary}
          />
        </FilterHeader>
        <FilterContent in={expandedSections.connector || false}>
          <FormGroup>
            {connectorsLoading ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
                <CircularProgress size={20} />
              </Box>
            ) : connectorsError ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
                <Typography variant="caption" color="error">
                  Failed to load connectors
                </Typography>
              </Box>
            ) : activeConnectors.length === 0 ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
                <Typography variant="caption" color="text.secondary">
                  No Active connectors available
                </Typography>
              </Box>
            ) : (
              activeConnectors?.map((connector) => {
                const isChecked = (localFilters.connectors || []).includes(connector.name);

                return (
                  <FormControlLabelStyled
                    key={connector.name}
                    control={
                      <FilterCheckbox
                        checked={isChecked}
                        onClick={() => handleFilterChange('connectors', connector.name)}
                        size="small"
                        disableRipple
                      />
                    }
                    label={
                      <Box sx={{ display: 'flex', alignItems: 'center', ml: 1, gap: 1 }}>
                        <img
                          src={connector.iconPath || '/assets/icons/connectors/default.svg'}
                          alt={connector.name}
                          width={16}
                          height={16}
                          style={{ objectFit: 'contain' }}
                          onError={(e) => {
                            e.currentTarget.src = '/assets/icons/connectors/default.svg';
                          }}
                        />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                          <Typography variant="body2">{connector.name}</Typography>
                        </Box>
                      </Box>
                    }
                  />
                );
              })
            )}
          </FormGroup>
        </FilterContent>
      </FilterSection>
    ),
    [
      expandedSections.connector,
      activeCounts.connectors,
      localFilters.connectors,
      theme,
      handleFilterChange,
      toggleSection,
      activeConnectors,
      connectorsLoading,
      connectorsError,
    ]
  );

  // Filter section for Permissions
  const PermissionsFilterSection = useMemo(
    () => (
      <FilterSection>
        <FilterHeader
          expanded={expandedSections.permissions || false}
          onClick={() => toggleSection('permissions')}
        >
          <FilterLabel>
            <Icon
              icon={userCheckIcon}
              fontSize="large"
              color={
                expandedSections.permissions
                  ? theme.palette.primary.main
                  : theme.palette.text.secondary
              }
            />
            Permissions
            {activeCounts.permissions > 0 && (
              <FilterCount badgeContent={activeCounts.permissions} color="primary" />
            )}
          </FilterLabel>
          <Icon
            icon={expandedSections.permissions ? upIcon : downIcon}
            fontSize="large"
            color={theme.palette.text.secondary}
          />
        </FilterHeader>
        <FilterContent in={expandedSections.permissions || false}>
          <FormGroup>
            {['READER', 'WRITER', 'OWNER', 'COMMENTER', 'ORGANIZER', 'FILEORGANIZER'].map(
              (permission) => {
                const isChecked = (localFilters.permissions || []).includes(permission);

                return (
                  <FormControlLabelStyled
                    key={permission}
                    control={
                      <FilterCheckbox
                        checked={isChecked}
                        onClick={() => handleFilterChange('permissions', permission)}
                        size="small"
                        disableRipple
                      />
                    }
                    label={
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Icon
                          icon={permissionIcons[permission]}
                          color={theme.palette.text.secondary}
                          width={16}
                          height={16}
                        />
                        {formatLabel(permission)}
                      </Box>
                    }
                  />
                );
              }
            )}
          </FormGroup>
        </FilterContent>
      </FilterSection>
    ),
    [
      expandedSections.permissions,
      activeCounts.permissions,
      localFilters.permissions,
      theme,
      handleFilterChange,
      permissionIcons,
      toggleSection,
    ]
  );

  // Collapsed sidebar content with memoization
  const CollapsedContent = useMemo(
    () => (
      <CollapsedButtonContainer visible={showCollapsedContent}>
        <Tooltip title="Status Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('status', 'indexingStatus')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.indexingStatus} color="primary">
              <Icon icon={filterVariantIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip>

        <Tooltip title="Record Type Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('recordType', 'recordTypes')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.recordTypes} color="primary">
              <Icon icon={fileIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip>

        <Tooltip title="Origin Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('origin', 'origin')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.origin} color="primary">
              <Icon icon={cloudUploadIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip>

        <Tooltip title="Connector Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('connector', 'connectors')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.connectors} color="primary">
              <Icon icon={cloudConnectorIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip>

        <Tooltip title="Permission Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('permissions', 'permissions')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.permissions} color="primary">
              <Icon icon={userCheckIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip>
        {/* 
        <Tooltip title="Department Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('departments', 'department')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.department} color="primary">
              <Icon icon={officeBuildingIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip>
        <Tooltip title="Module Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('modules', 'moduleId')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.moduleId} color="primary">
              <Icon icon={viewModuleIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip>
        <Tooltip title="Tag Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('tags', 'searchTags')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.searchTags} color="primary">
              <Icon icon={tagIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip>
        <Tooltip title="Category Filters" placement="right">
          <IconButtonStyled
            color="primary"
            sx={{ mb: 2 }}
            onClick={() => handleCollapsedFilterClick('categories', 'appSpecificRecordType')}
            disableRipple
          >
            <Badge badgeContent={activeCounts.appSpecificRecordType} color="primary">
              <Icon icon={formatListIcon} />
            </Badge>
          </IconButtonStyled>
        </Tooltip> */}

        {totalActiveFilterCount > 0 && (
          <Tooltip title="Clear All Filters" placement="right">
            <IconButtonStyled
              color="error"
              onClick={clearAllFilters}
              disableRipple
              sx={{
                mt: 2,
                bgcolor: alpha(theme.palette.error.main, 0.1),
                '&:hover': {
                  bgcolor: alpha(theme.palette.error.main, 0.2),
                },
              }}
            >
              <Icon icon={filterRemoveIcon} />
            </IconButtonStyled>
          </Tooltip>
        )}
      </CollapsedButtonContainer>
    ),
    [
      showCollapsedContent,
      activeCounts,
      totalActiveFilterCount,
      theme,
      clearAllFilters,
      handleCollapsedFilterClick,
    ]
  );

  // Filter section component - memoize for better performance
  const StatusFilterSection = useMemo(
    () => (
      <FilterSection>
        <FilterHeader
          expanded={expandedSections.status || false}
          onClick={() => toggleSection('status')}
        >
          <FilterLabel>
            <Icon
              icon={filterVariantIcon}
              fontSize="large"
              color={
                expandedSections.status ? theme.palette.primary.main : theme.palette.text.secondary
              }
            />
            Status
            {activeCounts.indexingStatus > 0 && (
              <FilterCount badgeContent={activeCounts.indexingStatus} color="primary" />
            )}
          </FilterLabel>
          <Icon
            icon={expandedSections.status ? upIcon : downIcon}
            fontSize="large"
            color={theme.palette.text.secondary}
          />
        </FilterHeader>
        <FilterContent in={expandedSections.status || false}>
          <FormGroup>
            {[
              'NOT_STARTED',
              'IN_PROGRESS',
              'FAILED',
              'COMPLETED',
              'FILE_TYPE_NOT_SUPPORTED',
              'AUTO_INDEX_OFF',
            ].map((status) => {
              const isChecked = (localFilters.indexingStatus || []).includes(status);

              return (
                <FormControlLabelStyled
                  key={status}
                  control={
                    <FilterCheckbox
                      checked={isChecked}
                      onClick={() => handleFilterChange('indexingStatus', status)}
                      size="small"
                      disableRipple
                    />
                  }
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Icon
                        icon={statusIcons[status]}
                        color={statusColors[status]}
                        width={16}
                        height={16}
                      />
                      {status === 'AUTO_INDEX_OFF' ? 'Manual Sync' : formatLabel(status)}
                    </Box>
                  }
                />
              );
            })}
          </FormGroup>
        </FilterContent>
      </FilterSection>
    ),
    [
      expandedSections.status,
      activeCounts.indexingStatus,
      localFilters.indexingStatus,
      theme,
      statusColors,
      handleFilterChange,
      toggleSection,
    ]
  );

  return (
    <OpenedDrawer
      variant="permanent"
      open={open}
      sx={{
        transition: theme.transitions.create('width', {
          duration: '0.25s',
          easing: theme.transitions.easing.sharp,
        }),
      }}
    >
      <DrawerHeader>
        {open ? (
          <>
            <Typography
              variant="subtitle1"
              fontWeight={600}
              sx={{ color: theme.palette.primary.main }}
            >
              <Box component="span" sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Icon icon={filterMenuIcon} />
                Filters
              </Box>
            </Typography>

            {/* Clean loading indicator in header */}
            <Fade in={isLoading} timeout={150}>
              <LoadingIndicator>
                <CircularProgress size={16} thickness={4} />
              </LoadingIndicator>
            </Fade>

            <Tooltip title="Collapse sidebar">
              <IconButtonStyled
                onClick={handleDrawerToggle}
                size="small"
                disableRipple
                sx={{ color: theme.palette.text.secondary }}
              >
                <Icon icon={leftIcon} width={20} height={20} />
              </IconButtonStyled>
            </Tooltip>

            {/* Status indicator bar at the bottom of header */}
            <StatusIndicator active={isLoading} />
          </>
        ) : (
          <>
            <Tooltip title="Expand sidebar" placement="right">
              <IconButtonStyled
                onClick={handleDrawerToggle}
                disableRipple
                sx={{ mx: 'auto', color: theme.palette.primary.main }}
              >
                <Icon icon={rightIcon} width={20} height={20} />
              </IconButtonStyled>
            </Tooltip>

            {/* Status indicator for collapsed state */}
            <StatusIndicator active={isLoading} />
          </>
        )}
      </DrawerHeader>

      {!open ? (
        CollapsedContent
      ) : (
        <ExpandedContentContainer visible={showExpandedContent}>
          <FiltersContainer>
            {renderActiveFilters()}
            {StatusFilterSection}

            {RecordTypeFilterSection}
            {OriginFilterSection}
            {ConnectorFilterSection}
            {PermissionsFilterSection}

            {/* Other filter sections can be added/implemented similarly */}
            {/* Department, Module, Tags, and Record Categories sections can be implemented 
                when you have the actual data available */}
          </FiltersContainer>
        </ExpandedContentContainer>
      )}
    </OpenedDrawer>
  );
}
