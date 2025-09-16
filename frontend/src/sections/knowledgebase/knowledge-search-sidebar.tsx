import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react';
import type { Icon as IconifyIcon } from '@iconify/react';

import { Icon } from '@iconify/react';
import appIcon from '@iconify-icons/mdi/apps';
import { useNavigate } from 'react-router-dom';
import closeIcon from '@iconify-icons/mdi/close';
import upIcon from '@iconify-icons/mdi/chevron-up';
import magnifyIcon from '@iconify-icons/mdi/magnify';
import leftIcon from '@iconify-icons/mdi/chevron-left';
import downIcon from '@iconify-icons/mdi/chevron-down';
import rightIcon from '@iconify-icons/mdi/chevron-right';
import viewModuleIcon from '@iconify-icons/mdi/view-module';
import filterMenuIcon from '@iconify-icons/mdi/filter-menu';
import filterRemoveIcon from '@iconify-icons/mdi/filter-remove';
import officeBuildingIcon from '@iconify-icons/mdi/office-building';
import formatListIcon from '@iconify-icons/mdi/format-list-bulleted';
import closeCircleIcon from '@iconify-icons/mdi/close-circle-outline';
import databaseIcon from '@iconify-icons/mdi/database';
import bookOpenIcon from '@iconify-icons/mdi/book-open-outline';

import { alpha, styled, useTheme } from '@mui/material/styles';
import {
  Box,
  Chip,
  Badge,
  Paper,
  Drawer,
  Button,
  Tooltip,
  Checkbox,
  Collapse,
  FormGroup,
  TextField,
  Typography,
  IconButton,
  InputAdornment,
  FormControlLabel,
  CircularProgress,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Skeleton,
} from '@mui/material';

import { KnowledgeBaseAPI } from './services/api';
import {useConnectors } from '../accountdetails/connectors/context';

import type { Modules } from './types/modules';
import type { Departments } from './types/departments';
import type { RecordCategories } from './types/record-categories';
import type { Filters, KnowledgeSearchSideBarProps } from './types/knowledge-base';
import type { KnowledgeBase } from './types/kb';

// Local KB definition (hardcoded)
const localKB = {
  id: 'local',
  name: 'KB',
  iconPath: '/assets/icons/connectors/kb.svg',
  color: '#34A853'
};

// Constants
const drawerWidth = 280;
const closedDrawerWidth = 60;

// Types for styled components
interface FilterHeaderProps {
  expanded?: boolean;
}

interface FilterSectionComponentProps {
  id: string;
  icon: React.ComponentProps<typeof IconifyIcon>['icon'];
  label: string;
  filterType: keyof Filters;
  items: any[];
  getItemId?: (item: any) => string;
  getItemLabel?: (item: any) => string;
  renderItemLabel?: ((item: any) => React.ReactNode) | null;
  expanded?: boolean;
  onToggle?: () => void;
  activeFilters?: string[];
  children?: React.ReactNode;
}

// Custom styled components with optimized transitions
const OpenedDrawer = styled(Drawer, { shouldForwardProp: (prop) => prop !== 'open' })(
  ({ theme, open }) => ({
    width: open ? drawerWidth : closedDrawerWidth,
    flexShrink: 0,
    whiteSpace: 'nowrap',
    boxSizing: 'border-box',
    '& .MuiDrawer-paper': {
      width: open ? drawerWidth : closedDrawerWidth,
      transition: theme.transitions.create('width', {
        easing: theme.transitions.easing.sharp,
        duration: '0.25s',
      }),
      overflowX: 'hidden',
      borderRight: 'none',
      backgroundColor: theme.palette.background.paper,
      boxShadow: '0 0 20px rgba(0, 0, 0, 0.05)',
      marginTop: theme.spacing(8),
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
}));

const FilterSection = styled(Paper)(({ theme }) => ({
  borderRadius: theme.shape.borderRadius,
  marginBottom: theme.spacing(1.5),
  overflow: 'hidden',
  border: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
  boxShadow: 'none',
  willChange: 'transform',
}));

const FilterHeader = styled('div', {
  shouldForwardProp: (prop) => prop !== 'expanded',
})<FilterHeaderProps>(({ theme, expanded }) => ({
  padding: theme.spacing(1.5, 2),
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  cursor: 'pointer',
  borderRadius: `${theme.shape.borderRadius}px ${theme.shape.borderRadius}px 0 0`,
  backgroundColor: expanded
    ? alpha(theme.palette.primary.main, 0.05)
    : alpha(theme.palette.background.default, 0.5),
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.08),
  },
}));

const FilterContent = styled(Collapse)(({ theme }) => ({
  padding: theme.spacing(1, 2, 1.5, 2),
  maxHeight: 400,
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
  height: 'calc(100vh - 130px)',
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
}));

const ClearFiltersButton = styled(Button)(({ theme }) => ({
  minWidth: 'auto',
  padding: theme.spacing(0.5, 1),
  fontSize: '0.75rem',
  textTransform: 'none',
  color: theme.palette.primary.main,
  fontWeight: 500,
  transition: theme.transitions.create(['background-color'], {
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

const CollapsedButtonContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  paddingTop: theme.spacing(2),
}));

const IconButtonStyled = styled(IconButton)(({ theme }) => ({
  transition: theme.transitions.create(['background-color'], {
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

const SearchInput = styled(TextField)(({ theme }) => ({
  marginBottom: theme.spacing(2),
  '& .MuiOutlinedInput-root': {
    backgroundColor: alpha(theme.palette.background.default, 0.8),
    borderRadius: theme.shape.borderRadius,
    '&:hover .MuiOutlinedInput-notchedOutline': {
      borderColor: alpha(theme.palette.primary.main, 0.3),
    },
    '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
      borderColor: theme.palette.primary.main,
    },
  },
  '& .MuiOutlinedInput-input': {
    padding: theme.spacing(1, 1, 1, 0.5),
    fontSize: '0.85rem',
  },
}));

// KB Search Component
const KBSearchInput = styled(TextField)(({ theme }) => ({
  marginBottom: theme.spacing(1),
  '& .MuiOutlinedInput-root': {
    backgroundColor: alpha(theme.palette.background.default, 0.8),
    borderRadius: theme.shape.borderRadius,
    height: 32,
    '&:hover .MuiOutlinedInput-notchedOutline': {
      borderColor: alpha(theme.palette.primary.main, 0.3),
    },
    '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
      borderColor: theme.palette.primary.main,
    },
  },
  '& .MuiOutlinedInput-input': {
    padding: theme.spacing(0.5, 1, 0.5, 0.5),
    fontSize: '0.8rem',
  },
}));

const KBListItem = styled(ListItem)(({ theme }) => ({
  padding: theme.spacing(0.5, 0),
  cursor: 'pointer',
  borderRadius: theme.shape.borderRadius,
  '&:hover': {
    backgroundColor: alpha(theme.palette.action.hover, 0.5),
  },
}));

const LoadMoreButton = styled(Button)(({ theme }) => ({
  width: '100%',
  height: 32,
  fontSize: '0.75rem',
  fontWeight: 500,
  textTransform: 'none',
  borderRadius: theme.shape.borderRadius,
  border: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
  color: theme.palette.text.secondary,
  backgroundColor: 'transparent',
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.05),
    borderColor: theme.palette.primary.main,
    color: theme.palette.primary.main,
  },
  '&:disabled': {
    opacity: 0.5,
    cursor: 'not-allowed',
  },
}));

const InfiniteScrollTrigger = styled(Box)(({ theme }) => ({
  height: 20,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  margin: theme.spacing(0.5, 0),
}));

// Helper function to format labels
const formatLabel = (label: string): string => {
  if (!label) return '';
  return label
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, (l: string) => l.toUpperCase());
};

// Get appropriate icon for KB
const getKBIcon = (name: string) => {
  const lowerName = name.toLowerCase();

  if (lowerName.includes('engineering') || lowerName.includes('tech')) {
    return viewModuleIcon;
  }
  if (lowerName.includes('hr') || lowerName.includes('people')) {
    return officeBuildingIcon;
  }
  if (lowerName.includes('api') || lowerName.includes('code')) {
    return formatListIcon;
  }

  return bookOpenIcon;
};

// Intersection Observer Hook for Infinite Scroll
const useIntersectionObserver = (callback: () => void) => {
  const targetRef = useRef<HTMLDivElement>(null);
  const callbackRef = useRef(callback);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    const target = targetRef.current;
    if (!target) return undefined;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          callbackRef.current();
        }
      },
      {
        threshold: 0.1,
        rootMargin: '50px',
      }
    );

    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  return targetRef;
};

// App Sources Filter Component with Dynamic Connectors
const AppSourcesFilter: React.FC<{
  filters: Filters;
  onFilterChange: (filterType: keyof Filters, value: string) => void;
  expanded: boolean;
  onToggle: () => void;
}> = ({ filters, onFilterChange, expanded, onToggle }) => {
  const theme = useTheme();
  const { activeConnectors, loading: connectorsLoading, error: connectorsError } = useConnectors();

  // Combine local KB with active connectors
  const allApps = useMemo(() => {
    const connectorApps = activeConnectors.map(connector => ({
      id: connector.name.toLowerCase(),
      name: connector.name,
      iconPath: connector.iconPath || '/assets/icons/connectors/default.svg',
    }));
    
    return [localKB, ...connectorApps];
  }, [activeConnectors]);

  const activeAppCount = (filters.app || []).length;

  return (
    <FilterSection>
      <FilterHeader expanded={expanded} onClick={onToggle}>
        <FilterLabel>
          <Icon
            icon={appIcon}
            fontSize="small"
            style={{
              color: expanded ? theme.palette.primary.main : alpha(theme.palette.text.primary, 0.7),
            }}
          />
          App Sources
          {activeAppCount > 0 && <FilterCount badgeContent={activeAppCount} color="primary" />}
        </FilterLabel>
        <Icon
          icon={expanded ? upIcon : downIcon}
          fontSize="small"
          style={{ color: alpha(theme.palette.text.primary, 0.7) }}
        />
      </FilterHeader>

      <FilterContent in={expanded}>
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
          ) : allApps.length === 0 ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
              <Typography variant="caption" color="text.secondary">
                No app sources available
              </Typography>
            </Box>
          ) : (
            allApps.map((app) => {
              const isChecked = (filters.app || []).includes(app.id);

              return (
                <FormControlLabelStyled
                  key={app.id}
                  control={
                    <FilterCheckbox
                      checked={isChecked}
                      onClick={() => onFilterChange('app', app.id)}
                      size="small"
                      disableRipple
                    />
                  }
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center',ml: 1, gap: 1 }}>
                      <img
                        src={app.iconPath}
                        alt={app.name}
                        width={16}
                        height={16}
                        style={{ objectFit: 'contain' }}
                        onError={(e) => {
                          e.currentTarget.src = '/assets/icons/connectors/default.svg';
                        }}
                      />
                      <Typography variant="body2">{app.name}</Typography>
                    </Box>
                  }
                />
              );
            })
          )}
        </FormGroup>
      </FilterContent>
    </FilterSection>
  );
};

// Knowledge Base Filter Component
const KnowledgeBaseFilter: React.FC<{
  filters: Filters;
  onFilterChange: (filterType: keyof Filters, value: string) => void;
  expanded: boolean;
  onToggle: () => void;
  knowledgeBasesMap: Map<string, KnowledgeBase>;
  setKnowledgeBasesMap: (map: Map<string, KnowledgeBase>) => void;
}> = ({ filters, onFilterChange, expanded, onToggle, knowledgeBasesMap, setKnowledgeBasesMap }) => {
  const theme = useTheme();
  const [kbSearch, setKbSearch] = useState('');
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [paginationMode, setPaginationMode] = useState<'infinite' | 'pagination'>('infinite');

  const loadingRef = useRef(false);
  const itemsPerPage = 10;

  const loadKnowledgeBases = useCallback(
    async (searchQuery = '', pageNum = 1, isLoadMore = false) => {
      if (loadingRef.current) return;

      loadingRef.current = true;
      if (isLoadMore) {
        setLoadingMore(true);
      } else {
        setLoading(true);
        if (!isLoadMore) {
          setKnowledgeBases([]);
        }
      }

      try {
        const params = {
          page: pageNum,
          limit: itemsPerPage,
          search: searchQuery,
        };

        const data = await KnowledgeBaseAPI.getKnowledgeBases(params);

        if (data.knowledgeBases && data.pagination) {
          const newKBs = data.knowledgeBases;

          // Update knowledge bases map for name lookup
          const newMap = new Map(knowledgeBasesMap);
          newKBs.forEach((kb: KnowledgeBase) => {
            newMap.set(kb.id, kb);
          });
          setKnowledgeBasesMap(newMap);

          if (isLoadMore) {
            setKnowledgeBases((prev) => [...prev, ...newKBs]);
          } else {
            setKnowledgeBases(newKBs);
          }

          setTotalPages(data.pagination.totalPages);
          setTotalCount(data.pagination.totalCount);
          setHasMore(pageNum < data.pagination.totalPages);
        } else if (Array.isArray(data)) {
          // Update map
          const newMap = new Map(knowledgeBasesMap);
          data.forEach((kb: KnowledgeBase) => {
            newMap.set(kb.id, kb);
          });
          setKnowledgeBasesMap(newMap);

          if (isLoadMore) {
            setKnowledgeBases((prev) => [...prev, ...data]);
          } else {
            setKnowledgeBases(data);
          }
          setTotalPages(1);
          setTotalCount(data.length);
          setHasMore(false);
        }
      } catch (error) {
        console.error('Failed to fetch knowledge bases:', error);
        if (!isLoadMore) {
          setKnowledgeBases([]);
          setTotalPages(1);
          setTotalCount(0);
          setHasMore(false);
        }
      } finally {
        setLoading(false);
        setLoadingMore(false);
        loadingRef.current = false;
      }
    },
    [knowledgeBasesMap, setKnowledgeBasesMap]
  );

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => {
      setPage(1);
      setHasMore(true);
      loadKnowledgeBases(kbSearch, 1, false);
    }, 300);

    return () => clearTimeout(timer);
    // eslint-disable-next-line
  }, [kbSearch]);

  // Load KBs on page change (pagination mode)
  useEffect(() => {
    if (paginationMode === 'pagination' && page > 1) {
      loadKnowledgeBases(kbSearch, page, false);
    }
  }, [page, kbSearch, loadKnowledgeBases, paginationMode]);

  // Initial load when expanded
  useEffect(() => {
    if (expanded && knowledgeBases.length === 0) {
      loadKnowledgeBases();
    }
  }, [expanded, knowledgeBases.length, loadKnowledgeBases]);

  // Infinite scroll handler
  const handleLoadMore = useCallback(() => {
    if (!loadingMore && hasMore && !loading && paginationMode === 'infinite') {
      const nextPage = Math.floor(knowledgeBases.length / itemsPerPage) + 1;
      loadKnowledgeBases(kbSearch, nextPage, true);
    }
  }, [
    loadingMore,
    hasMore,
    loading,
    paginationMode,
    knowledgeBases.length,
    kbSearch,
    loadKnowledgeBases,
  ]);

  const loadMoreRef = useIntersectionObserver(handleLoadMore);

  const handleKbSearchChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setKbSearch(event.target.value);
  };

  const handlePageChange = (event: React.ChangeEvent<unknown>, value: number) => {
    setPage(value);
  };

  const handleKBToggle = (kbId: string) => {
    onFilterChange('kb', kbId);
  };

  const handlePaginationModeChange = (
    event: React.MouseEvent<HTMLElement>,
    newMode: 'infinite' | 'pagination' | null
  ) => {
    if (newMode !== null) {
      setPaginationMode(newMode);
      if (newMode === 'pagination') {
        // Reset to page 1 and load fresh data
        setPage(1);
        loadKnowledgeBases(kbSearch, 1, false);
      }
    }
  };

  const handleLoadMoreClick = () => {
    if (!loadingMore && hasMore) {
      handleLoadMore();
    }
  };

  const activeKBCount = (filters.kb || []).length;

  return (
    <FilterSection>
      <FilterHeader expanded={expanded} onClick={onToggle}>
        <FilterLabel>
          <Icon
            icon={databaseIcon}
            fontSize="small"
            style={{
              color: expanded ? theme.palette.primary.main : alpha(theme.palette.text.primary, 0.7),
            }}
          />
          Knowledge Bases
          {activeKBCount > 0 && <FilterCount badgeContent={activeKBCount} color="primary" />}
        </FilterLabel>
        <Icon
          icon={expanded ? upIcon : downIcon}
          fontSize="small"
          style={{ color: alpha(theme.palette.text.primary, 0.7) }}
        />
      </FilterHeader>

      <FilterContent in={expanded}>
        <KBSearchInput
          fullWidth
          size="small"
          value={kbSearch}
          onChange={handleKbSearchChange}
          placeholder="Search knowledge bases..."
          variant="outlined"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Icon icon={magnifyIcon} fontSize="small" />
              </InputAdornment>
            ),
            endAdornment: kbSearch ? (
              <InputAdornment position="end">
                <IconButton size="small" onClick={() => setKbSearch('')} disableRipple>
                  <Icon icon={closeIcon} fontSize="small" />
                </IconButton>
              </InputAdornment>
            ) : null,
          }}
        />

        {/* KB List */}
        <Box sx={{ minHeight: 200, maxHeight: 280 }}>
          {loading ? (
            <Box>
              {Array.from(new Array(5)).map((_, index) => (
                <Box key={index} sx={{ display: 'flex', alignItems: 'center', py: 1 }}>
                  <Skeleton variant="circular" width={20} height={20} sx={{ mr: 1 }} />
                  <Skeleton variant="text" width="80%" height={20} />
                </Box>
              ))}
            </Box>
          ) : (
            <List dense sx={{ py: 0 }}>
              {knowledgeBases.map((kb) => {
                const isChecked = (filters.kb || []).includes(kb.id);
                const kbIcon = getKBIcon(kb.name);

                return (
                  <KBListItem key={kb.id} onClick={() => handleKBToggle(kb.id)}>
                    <ListItemIcon sx={{ minWidth: 32 }}>
                      <FilterCheckbox
                        checked={isChecked}
                        size="small"
                        disableRipple
                        sx={{ p: 0 }}
                      />
                    </ListItemIcon>
                    <ListItemText
                      primary={kb.name}
                      secondary={kb.userRole}
                      primaryTypographyProps={{
                        fontSize: '0.8rem',
                        fontWeight: 500,
                        noWrap: true,
                      }}
                      secondaryTypographyProps={{
                        fontSize: '0.7rem',
                        color: 'text.secondary',
                        noWrap: true,
                      }}
                    />
                  </KBListItem>
                );
              })}

              {/* Infinite Scroll Trigger */}
              {paginationMode === 'infinite' && hasMore && !loading && (
                <InfiniteScrollTrigger ref={loadMoreRef}>
                  {loadingMore && <CircularProgress size={16} thickness={4} />}
                </InfiniteScrollTrigger>
              )}
            </List>
          )}

          {!loading && knowledgeBases.length === 0 && (
            <Box sx={{ textAlign: 'center', py: 2 }}>
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
                {kbSearch ? 'No knowledge bases found' : 'No knowledge bases available'}
              </Typography>
            </Box>
          )}
        </Box>
      </FilterContent>
      {/* Load More Button (Infinite Scroll Mode) */}
      {paginationMode === 'infinite' && expanded && hasMore && knowledgeBases.length > 0 && (
        <Box sx={{ mt: 1 }}>
          <LoadMoreButton
            onClick={handleLoadMoreClick}
            disabled={loadingMore}
            startIcon={loadingMore ? <CircularProgress size={12} /> : null}
          >
            {loadingMore
              ? 'Loading...'
              : `Load More (${totalCount - knowledgeBases.length} remaining)`}
          </LoadMoreButton>
        </Box>
      )}
    </FilterSection>
  );
};

export default function KnowledgeSearchSideBar({
  filters,
  onFilterChange,
  sx = {},
  openSidebar,
  onToggleSidebar,
}: KnowledgeSearchSideBarProps) {
  const theme = useTheme();
  const navigate = useNavigate();
  const [open, setOpen] = useState<boolean>(true);
  const [departments, setDepartments] = useState<Departments[]>([]);
  const [recordCategories, setRecordCategories] = useState<RecordCategories[]>([]);
  const [modules, setModules] = useState<Modules[]>([]);
  const [searchTerm, setSearchTerm] = useState<string>('');
  const isFilterChanging = useRef(false);
  const [expandedSections, setExpandedSections] = useState<{ [key: string]: boolean }>({
    apps: true,
    kb: false,
    departments: false,
    modules: false,
    categories: false,
  });
  const [loading, setLoading] = useState<boolean>(true);

  // Knowledge base map for name lookup in chips
  const [knowledgeBasesMap, setKnowledgeBasesMap] = useState<Map<string, KnowledgeBase>>(new Map());

  useEffect(() => {
    setOpen(openSidebar);
  }, [openSidebar]);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        // Mock data since API calls are commented out
        setDepartments([
          {
            _id: 'engineering',
            name: 'Engineering',
            tag: 'eng',
            origin: 'system',
            description: '',
            orgId: '',
            isDeleted: false,
            __v: 0,
            createdAt: '',
            updatedAt: '',
          },
          {
            _id: 'product',
            name: 'Product Management',
            tag: 'pm',
            origin: 'system',
            description: '',
            orgId: '',
            isDeleted: false,
            __v: 0,
            createdAt: '',
            updatedAt: '',
          },
        ]);
        setRecordCategories([
          {
            _id: 'technical',
            name: 'Technical Documentation',
            tag: 'tech',
            origin: 'system',
            description: '',
            orgId: '',
            isDeleted: false,
            __v: 0,
            createdAt: '',
            updatedAt: '',
          },
        ]);
        setModules([
          {
            _id: 'module1',
            name: 'User Management',
            description: '',
            orgId: '',
            isDeleted: false,
            createdAt: '',
            updatedAt: '',
            __v: 0,
          },
        ]);
      } catch (error) {
        console.error('Error fetching filter data:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const handleDrawerToggle = () => {
    setOpen(!open);
  };

  const toggleSection = (section: string) => {
    setExpandedSections({
      ...expandedSections,
      [section]: !expandedSections[section],
    });
  };

  const handleFilterChange = (filterType: keyof Filters, value: string) => {
    if (isFilterChanging.current) return;

    isFilterChanging.current = true;

    requestAnimationFrame(() => {
      const currentFilterValues = filters[filterType] || [];
      const updatedFilters = {
        ...filters,
        [filterType]: currentFilterValues.includes(value)
          ? currentFilterValues.filter((item: string) => item !== value)
          : [...currentFilterValues, value],
      };

      onFilterChange(updatedFilters);

      setTimeout(() => {
        isFilterChanging.current = false;
      }, 50);
    });
  };

  const handleCollapsedFilterClick = (sectionId: string, filterType: keyof Filters) => {
    if (!open) {
      setOpen(true);
      setExpandedSections({
        ...expandedSections,
        [sectionId]: true,
      });
    }
  };

  const getActiveFilterCount = (filterType: keyof Filters): number =>
    (filters[filterType] || []).length;

  const getTotalActiveFilterCount = (): number =>
    Object.values(filters).reduce((acc, curr) => acc + (curr || []).length, 0);

  const getFilterName = (type: keyof Filters, id: string): string => {
    switch (type) {
      case 'department':
        return departments.find((d) => d._id === id)?.name || id;
      case 'moduleId':
        return modules.find((m) => m._id === id)?.name || id;
      case 'appSpecificRecordType':
        return recordCategories.find((c) => c._id === id)?.name || id;
      case 'app':
        // Handle local KB and dynamic connectors
        if (id === 'local') {
          return localKB.name;
        }
        // For connector names, convert from lowercase back to original case
        return id.charAt(0).toUpperCase() + id.slice(1).toLowerCase();
      case 'kb': {
        // Get KB name from the map, fallback to truncated ID
        const kb = knowledgeBasesMap.get(id);
        return kb ? kb.name : `KB: ${id.substring(0, 8)}...`;
      }
      default:
        return id;
    }
  };

  const clearFilter = (type: keyof Filters, value: string) => {
    if (isFilterChanging.current) return;

    isFilterChanging.current = true;

    requestAnimationFrame(() => {
      const updatedFilters = {
        ...filters,
        [type]: (filters[type] || []).filter((item) => item !== value),
      };
      onFilterChange(updatedFilters);

      setTimeout(() => {
        isFilterChanging.current = false;
      }, 50);
    });
  };

  const clearAllFilters = () => {
    if (isFilterChanging.current) return;

    isFilterChanging.current = true;

    requestAnimationFrame(() => {
      onFilterChange({
        department: [],
        moduleId: [],
        appSpecificRecordType: [],
        app: [],
        kb: [],
      });

      setTimeout(() => {
        isFilterChanging.current = false;
      }, 50);
    });
  };

  const hasActiveFilters = getTotalActiveFilterCount() > 0;

  const filterItems = <T extends { name: string; id?: string }>(items: T[]): T[] => {
    if (!searchTerm) return items;
    return items.filter(
      (item) =>
        item.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (item.id && item.id.toLowerCase().includes(searchTerm.toLowerCase()))
    );
  };

  const renderActiveFilters = () => {
    if (!hasActiveFilters) return null;

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
            Active Filters ({getTotalActiveFilterCount()})
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
          {Object.entries(filters).map(([type, values]) =>
            (values || []).map((value: any) => (
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

  // Collapsed sidebar content
  const renderCollapsedContent = () => (
    <CollapsedButtonContainer>
      <Tooltip title="App Sources" placement="right">
        <IconButtonStyled
          color="primary"
          sx={{ mb: 2 }}
          onClick={() => handleCollapsedFilterClick('apps', 'app')}
          disableRipple
        >
          <Badge badgeContent={getActiveFilterCount('app')} color="primary">
            <Icon icon={appIcon} />
          </Badge>
        </IconButtonStyled>
      </Tooltip>

      <Tooltip title="Knowledge Bases" placement="right">
        <IconButtonStyled
          color="primary"
          sx={{ mb: 2 }}
          onClick={() => handleCollapsedFilterClick('kb', 'kb')}
          disableRipple
        >
          <Badge badgeContent={getActiveFilterCount('kb')} color="primary">
            <Icon icon={databaseIcon} />
          </Badge>
        </IconButtonStyled>
      </Tooltip>

      <Tooltip title="Department Filters" placement="right">
        <IconButtonStyled
          color="primary"
          sx={{ mb: 2 }}
          onClick={() => handleCollapsedFilterClick('departments', 'department')}
          disableRipple
        >
          <Badge badgeContent={getActiveFilterCount('department')} color="primary">
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
          <Badge badgeContent={getActiveFilterCount('moduleId')} color="primary">
            <Icon icon={viewModuleIcon} />
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
          <Badge badgeContent={getActiveFilterCount('appSpecificRecordType')} color="primary">
            <Icon icon={formatListIcon} />
          </Badge>
        </IconButtonStyled>
      </Tooltip>

      {hasActiveFilters && (
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
  );

  // Filter section component with enhanced styling
  const FilterSectionComponent = ({
    id,
    icon,
    label,
    filterType,
    items,
    getItemId = (item: any) => item._id || item.id,
    getItemLabel = (item: any) => item.name,
    renderItemLabel = null,
    expanded,
    onToggle,
    activeFilters = [],
    children,
  }: FilterSectionComponentProps) => {
    const isExpanded = expanded !== undefined ? expanded : expandedSections[id];
    const handleToggle = onToggle || (() => toggleSection(id));

    return (
      <FilterSection>
        <FilterHeader expanded={isExpanded} onClick={handleToggle}>
          <FilterLabel>
            <Icon
              icon={icon}
              fontSize="small"
              style={{
                color: isExpanded
                  ? theme.palette.primary.main
                  : alpha(theme.palette.text.primary, 0.7),
              }}
            />
            {label}
            {getActiveFilterCount(filterType) > 0 && (
              <FilterCount badgeContent={getActiveFilterCount(filterType)} color="primary" />
            )}
          </FilterLabel>
          <Icon
            icon={isExpanded ? upIcon : downIcon}
            fontSize="small"
            style={{ color: alpha(theme.palette.text.primary, 0.7) }}
          />
        </FilterHeader>
        <FilterContent in={isExpanded}>
          {children || (
            <>
              {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : (
                <FormGroup>
                  {filterItems(items).map((item) => {
                    const itemId = typeof item === 'string' ? item : getItemId(item);
                    const isChecked = filters[filterType]?.includes(itemId) || false;

                    return (
                      <FormControlLabelStyled
                        key={itemId}
                        control={
                          <FilterCheckbox
                            checked={isChecked}
                            onClick={() => handleFilterChange(filterType, itemId)}
                            size="small"
                            disableRipple
                            sx={{
                              color: isChecked ? theme.palette.primary.main : undefined,
                            }}
                          />
                        }
                        label={
                          renderItemLabel !== null
                            ? renderItemLabel(item)
                            : typeof item === 'string'
                              ? formatLabel(item)
                              : getItemLabel(item)
                        }
                      />
                    );
                  })}

                  {filterItems(items).length === 0 && (
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ py: 1, textAlign: 'center' }}
                    >
                      No matching items
                    </Typography>
                  )}
                </FormGroup>
              )}
            </>
          )}
        </FilterContent>
      </FilterSection>
    );
  };

  return (
    <OpenedDrawer variant="permanent" open={open} sx={sx}>
      <DrawerHeader>
        {open ? (
          <>
            <Typography
              variant="subtitle1"
              fontWeight={600}
              sx={{
                color: theme.palette.primary.main,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
              }}
            >
              <Icon icon={filterMenuIcon} />
              Filters
            </Typography>
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
          </>
        ) : (
          <Tooltip title="Expand sidebar" placement="right">
            <IconButtonStyled
              onClick={handleDrawerToggle}
              disableRipple
              sx={{ mx: 'auto', color: theme.palette.primary.main }}
            >
              <Icon icon={rightIcon} width={20} height={20} />
            </IconButtonStyled>
          </Tooltip>
        )}
      </DrawerHeader>

      {!open ? (
        renderCollapsedContent()
      ) : (
        <FiltersContainer>
          <SearchInput
            fullWidth
            size="small"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search filters..."
            variant="outlined"
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Icon icon={magnifyIcon} fontSize="small" />
                </InputAdornment>
              ),
              endAdornment: searchTerm ? (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={() => setSearchTerm('')} disableRipple>
                    <Icon icon={closeIcon} fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ) : null,
            }}
          />

          {renderActiveFilters()}

          <AppSourcesFilter
            filters={filters}
            onFilterChange={handleFilterChange}
            expanded={expandedSections.apps}
            onToggle={() => toggleSection('apps')}
          />

          {/* Knowledge Base Filter */}
          <KnowledgeBaseFilter
            filters={filters}
            onFilterChange={handleFilterChange}
            expanded={expandedSections.kb}
            onToggle={() => toggleSection('kb')}
            knowledgeBasesMap={knowledgeBasesMap}
            setKnowledgeBasesMap={setKnowledgeBasesMap}
          />

          {/* Uncomment these sections when API data is available */}
          {/*
          <FilterSectionComponent
            id="departments"
            icon={officeBuildingIcon}
            label="Departments"
            filterType="department"
            items={departments}
          />

          <FilterSectionComponent
            id="modules"
            icon={viewModuleIcon}
            label="Modules"
            filterType="moduleId"
            items={modules}
          />

          <FilterSectionComponent
            id="categories"
            icon={formatListIcon}
            label="Record Categories"
            filterType="appSpecificRecordType"
            items={recordCategories}
          />
          */}
        </FiltersContainer>
      )}
    </OpenedDrawer>
  );
}
