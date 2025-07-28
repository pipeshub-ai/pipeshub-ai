import type { GridColDef } from '@mui/x-data-grid';

import { Icon } from '@iconify/react';
// Common UI icons
import addIcon from '@iconify-icons/mdi/plus';
import gavelIcon from '@iconify-icons/mdi/gavel';
import clearIcon from '@iconify-icons/mdi/close';
import codeIcon from '@iconify-icons/mdi/code-tags';
import searchIcon from '@iconify-icons/mdi/magnify';
import databaseIcon from '@iconify-icons/mdi/database';
import securityIcon from '@iconify-icons/mdi/security';
import headsetMicIcon from '@iconify-icons/mdi/headset';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import settingsIcon from '@iconify-icons/mdi/cog-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import engineeringIcon from '@iconify-icons/mdi/encryption';
import trendingUpIcon from '@iconify-icons/mdi/trending-up';
import moreVertIcon from '@iconify-icons/mdi/dots-vertical';
import campaignIcon from '@iconify-icons/mdi/bullhorn-outline';
import gridViewIcon from '@iconify-icons/mdi/view-grid-outline';
import accountBalanceIcon from '@iconify-icons/mdi/bank-outline';
import analyticsOutlineIcon from '@iconify-icons/mdi/chart-line';
// Icon imports - Add these at the top of your file
import folderOutlineIcon from '@iconify-icons/mdi/folder-outline';
import schoolOutlineIcon from '@iconify-icons/mdi/school-outline';
import autoStoriesIcon from '@iconify-icons/mdi/book-open-outline';
import listViewIcon from '@iconify-icons/mdi/format-list-bulleted';
import paletteOutlineIcon from '@iconify-icons/mdi/palette-outline';
import inventoryIcon from '@iconify-icons/mdi/package-variant-closed';
import peopleOutlineIcon from '@iconify-icons/mdi/account-group-outline';
import descriptionOutlineIcon from '@iconify-icons/mdi/file-document-outline';
import React, { memo, useRef, useMemo, useState, useEffect, useCallback } from 'react';
import { Theme } from '@mui/material/styles';
import { DataGrid } from '@mui/x-data-grid';
import {
  Box,
  Grid,
  Chip,
  Fade,
  Stack,
  Paper,
  alpha,
  Button,
  Divider,
  Popover,
  Skeleton,
  ListItem,
  MenuList,
  MenuItem,
  Container,
  TextField,
  Typography,
  IconButton,
  ToggleButton,
  ListItemIcon,
  ListItemText,
  CardActionArea,
  InputAdornment,
  LinearProgress,
  TablePagination,
  CircularProgress,
  ToggleButtonGroup,
  ClickAwayListener,
  ListItemSecondaryAction,
} from '@mui/material';

import { KnowledgeBaseAPI } from '../services/api';

import type { KnowledgeBase } from '../types/kb';
import type { RouteParams } from '../hooks/use-router';

interface DashboardProps {
  theme: any;
  navigateToKB: (kb: KnowledgeBase) => void;
  onEdit: (item: KnowledgeBase) => void;
  onDelete: (item: KnowledgeBase) => void;
  setCreateKBDialog: (open: boolean) => void;
  CompactCard: React.ComponentType<{ children: React.ReactNode }>;
  ActionButton: React.ComponentType<any>;
  isInitialized: boolean;
  navigate: (route: RouteParams) => void;
}

// Get appropriate icon based on KB name/type and department
const getKBIcon = (name: string) => {
  const lowerName = name.toLowerCase();

  // Department-specific icons
  if (
    lowerName.includes('hr') ||
    lowerName.includes('human') ||
    lowerName.includes('people') ||
    lowerName.includes('employee')
  ) {
    return peopleOutlineIcon;
  }
  if (
    lowerName.includes('engineering') ||
    lowerName.includes('tech') ||
    lowerName.includes('development') ||
    lowerName.includes('software')
  ) {
    return engineeringIcon;
  }
  if (
    lowerName.includes('sales') ||
    lowerName.includes('revenue') ||
    lowerName.includes('deals') ||
    lowerName.includes('crm')
  ) {
    return trendingUpIcon;
  }
  if (
    lowerName.includes('marketing') ||
    lowerName.includes('campaign') ||
    lowerName.includes('brand') ||
    lowerName.includes('promotion')
  ) {
    return campaignIcon;
  }
  if (
    lowerName.includes('finance') ||
    lowerName.includes('accounting') ||
    lowerName.includes('budget') ||
    lowerName.includes('expense')
  ) {
    return accountBalanceIcon;
  }
  if (
    lowerName.includes('legal') ||
    lowerName.includes('compliance') ||
    lowerName.includes('policy') ||
    lowerName.includes('contract')
  ) {
    return gavelIcon;
  }
  if (
    lowerName.includes('operations') ||
    lowerName.includes('ops') ||
    lowerName.includes('logistics') ||
    lowerName.includes('supply')
  ) {
    return settingsIcon;
  }
  if (
    lowerName.includes('customer') ||
    lowerName.includes('support') ||
    lowerName.includes('service') ||
    lowerName.includes('help')
  ) {
    return headsetMicIcon;
  }
  if (
    lowerName.includes('product') ||
    lowerName.includes('feature') ||
    lowerName.includes('roadmap')
  ) {
    return inventoryIcon;
  }
  if (
    lowerName.includes('design') ||
    lowerName.includes('ui') ||
    lowerName.includes('ux') ||
    lowerName.includes('creative')
  ) {
    return paletteOutlineIcon;
  }
  if (
    lowerName.includes('security') ||
    lowerName.includes('cyber') ||
    lowerName.includes('privacy') ||
    lowerName.includes('audit')
  ) {
    return securityIcon;
  }

  // Content-type specific icons
  if (
    lowerName.includes('doc') ||
    lowerName.includes('guide') ||
    lowerName.includes('manual') ||
    lowerName.includes('handbook')
  ) {
    return descriptionOutlineIcon;
  }
  if (
    lowerName.includes('api') ||
    lowerName.includes('code') ||
    lowerName.includes('dev') ||
    lowerName.includes('sdk')
  ) {
    return codeIcon;
  }
  if (
    lowerName.includes('data') ||
    lowerName.includes('analytics') ||
    lowerName.includes('report') ||
    lowerName.includes('metrics')
  ) {
    return analyticsOutlineIcon;
  }
  if (
    lowerName.includes('training') ||
    lowerName.includes('learn') ||
    lowerName.includes('course') ||
    lowerName.includes('onboard')
  ) {
    return schoolOutlineIcon;
  }
  if (lowerName.includes('wiki') || lowerName.includes('knowledge') || lowerName.includes('faq')) {
    return autoStoriesIcon;
  }

  return folderOutlineIcon; // Default: simple folder icon
};

type ViewMode = 'grid' | 'list';

// Styled components
const CompactIconButton = React.memo(({ theme, ...props }: any) => (
  <IconButton
    sx={{
      width: 32,
      height: 32,
      borderRadius: 1,
      color: 'action.active',
      backgroundColor: 'transparent',
      border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
      '&:hover': {
        backgroundColor: 'action.hover',
        borderColor: 'action.active',
      },
    }}
    {...props}
  />
));

// Debounced search hook
const useDebounce = (value: string, delay: number) => {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(handler);
    };
  }, [value, delay]);

  return debouncedValue;
};

// Fixed Intersection Observer Hook for Infinite Scroll
const useIntersectionObserver = (callback: () => void, options: IntersectionObserverInit = {}) => {
  const targetRef = useRef<HTMLDivElement>(null);
  const callbackRef = useRef(callback);

  // Update callback ref when callback changes
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
        rootMargin: '100px',
        ...options,
      }
    );

    observer.observe(target);

    return () => observer.disconnect();
  }, [options]); // Remove callback from dependencies

  return targetRef;
};

// Memoized Menu Button Component with Fixed Positioning
const MenuButton = memo<{
  kb: KnowledgeBase;
  onEdit: (kb: KnowledgeBase) => void;
  onDelete: (kb: KnowledgeBase) => void;
  theme: any;
  className?: string;
  sx?: any;
}>(({ kb, onEdit, onDelete, theme, className, sx }) => {
  const [open, setOpen] = useState(false);
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);

  const handleToggle = useCallback((event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    event.preventDefault();
    setAnchorEl(event.currentTarget);
    setOpen((prevOpen) => !prevOpen);
  }, []);

  const handleClose = useCallback(
    (event?: Event | React.SyntheticEvent | {}, reason?: 'backdropClick' | 'escapeKeyDown') => {
      // Only check for contains if event is an actual Event or SyntheticEvent
      if (
        event &&
        'target' in event &&
        anchorEl &&
        anchorEl.contains(event.target as HTMLElement)
      ) {
        return;
      }
      setOpen(false);
      setAnchorEl(null);
    },
    [anchorEl]
  );

  const handleEdit = useCallback(
    (event: React.MouseEvent) => {
      event.stopPropagation();
      onEdit(kb);
      handleClose();
    },
    [onEdit, kb, handleClose]
  );

  const handleDelete = useCallback(
    (event: React.MouseEvent) => {
      event.stopPropagation();
      onDelete(kb);
      handleClose();
    },
    [onDelete, kb, handleClose]
  );

  return (
    <>
      <CompactIconButton
        size="small"
        onClick={handleToggle}
        className={className}
        sx={{
          ...sx,
          width: 28,
          height: 28,
          borderRadius: '4px',
          opacity: 0.7,
          transition: 'all 0.2s ease',
          '&:hover': {
            opacity: 1,
            backgroundColor: (themeVal: Theme) => alpha(themeVal.palette.grey[500], 0.1),
            borderColor: (themeVal: Theme) => alpha(themeVal.palette.grey[500], 0.3),
          },
        }}
        theme={theme}
        aria-controls={open ? 'composition-menu' : undefined}
        aria-expanded={open ? 'true' : undefined}
        aria-haspopup="true"
      >
        <Icon icon={moreVertIcon} fontSize={14} />
      </CompactIconButton>

      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={handleClose}
        anchorOrigin={{
          vertical: 'bottom',
          horizontal: 'right',
        }}
        transformOrigin={{
          vertical: 'top',
          horizontal: 'right',
        }}
        PaperProps={{
          sx: {
            borderRadius: 2,
            minWidth: 140,
            backgroundColor: 'background.paper',
            border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
            boxShadow: (themeVal) => themeVal.shadows[8],
            overflow: 'hidden',
            mt: 0.5,
          },
        }}
        slotProps={{
          paper: {
            elevation: 0,
          },
        }}
      >
        <ClickAwayListener onClickAway={handleClose}>
          <MenuList autoFocusItem={open} id="composition-menu" sx={{ p: 0.5 }}>
            <MenuItem
              onClick={handleEdit}
              sx={{
                fontSize: '0.8125rem',
                py: 1,
                px: 1.5,
                borderRadius: 1,
                mx: 0.5,
                '&:hover': {
                  backgroundColor: 'action.hover',
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 28 }}>
                <Icon icon={editIcon} fontSize={16} />
              </ListItemIcon>
              <ListItemText
                primary="Edit"
                primaryTypographyProps={{
                  fontSize: '0.8125rem',
                  fontWeight: 500,
                }}
              />
            </MenuItem>
            <MenuItem
              onClick={handleDelete}
              sx={{
                color: 'error.main',
                fontSize: '0.8125rem',
                py: 1,
                px: 1.5,
                borderRadius: 1,
                mx: 0.5,
                '&:hover': {
                  backgroundColor: (themeVal) => alpha(themeVal.palette.error.main, 0.08),
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 28 }}>
                <Icon icon={deleteIcon} fontSize={16} color={theme.palette.error.main} />
              </ListItemIcon>
              <ListItemText
                primary="Delete"
                primaryTypographyProps={{
                  fontSize: '0.8125rem',
                  fontWeight: 500,
                }}
              />
            </MenuItem>
          </MenuList>
        </ClickAwayListener>
      </Popover>
    </>
  );
});

MenuButton.displayName = 'MenuButton';

const GridItem = memo<{
  kb: KnowledgeBase;
  navigateToKB: (kb: KnowledgeBase) => void;
  onEdit: (kb: KnowledgeBase) => void;
  onDelete: (kb: KnowledgeBase) => void;
  theme: any;
  CompactCard: React.ComponentType<{ children: React.ReactNode }>;
}>(({ kb, navigateToKB, onEdit, onDelete, theme, CompactCard }) => {
  const [isHovered, setIsHovered] = useState(false);

  const handleClick = useCallback(() => {
    navigateToKB(kb);
  }, [navigateToKB, kb]);

  const handleMouseEnter = useCallback(() => {
    setIsHovered(true);
  }, []);

  const handleMouseLeave = useCallback(() => {
    setIsHovered(false);
  }, []);

  // Simple, readable date formatting
  const formatDate = useCallback((timestamp: number) => {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return '—';

    const now = new Date();
    const diffTime = Math.abs(now.getTime() - date.getTime());
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    if (diffDays < 30) return `${Math.ceil(diffDays / 7)}w ago`;
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }, []);

  const kbIcon = getKBIcon(kb.name);

  return (
    <Grid item xs={12} sm={6} md={4} lg={3}>
      <Paper
        elevation={0}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        sx={{
          position: 'relative',
          height: 140,
          borderRadius: 2,
          border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
          backgroundColor: 'background.paper',
          transition: 'all 0.2s ease',
          cursor: 'pointer',
          '&:hover': {
            borderColor: 'primary.main',
            boxShadow: (themeVal) =>
              `0 4px 12px ${alpha(themeVal.palette.common.black, themeVal.palette.mode === 'dark' ? 0.4 : 0.1)}`,
            transform: 'translateY(-2px)',
          },
        }}
      >
        <CardActionArea
          onClick={handleClick}
          sx={{
            height: '100%',
            p: 2.5,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            borderRadius: 2,
            '&:hover': {
              backgroundColor: 'transparent',
            },
          }}
        >
          {/* Header Section */}
          <Box sx={{ width: '100%', mb: 2 }}>
            <Stack direction="row" spacing={2} alignItems="flex-start">
              {/* Clean Icon */}
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  borderRadius: 2,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? alpha(themeVal.palette.grey[700], 0.3)
                      : alpha(themeVal.palette.grey[100], 1),
                  color: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? themeVal.palette.grey[400]
                      : themeVal.palette.grey[600],
                  flexShrink: 0,
                  transition: 'all 0.2s ease',
                  ...(isHovered && {
                    backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.1),
                    color: 'primary.main',
                  }),
                }}
              >
                <Icon icon={kbIcon} fontSize={20} />
              </Box>

              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography
                  variant="h6"
                  sx={{
                    fontSize: '1rem',
                    fontWeight: 600,
                    lineHeight: 1.3,
                    color: 'text.primary',
                    mb: 0.5,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {kb.name}
                </Typography>

                <Typography
                  variant="body2"
                  sx={{
                    fontSize: '0.8rem',
                    color: 'text.secondary',
                    fontWeight: 500,
                    textTransform: 'capitalize',
                  }}
                >
                  {kb.userRole}
                </Typography>
              </Box>
            </Stack>
          </Box>

          {/* Footer Section */}
          <Box
            sx={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <Typography
              variant="body2"
              sx={{
                fontSize: '0.75rem',
                color: 'text.secondary',
                fontWeight: 500,
              }}
            >
              Updated {formatDate(kb.updatedAtTimestamp)}
            </Typography>

            <Box
              sx={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                backgroundColor: 'success.main',
                opacity: 0.8,
              }}
            />
          </Box>
        </CardActionArea>

        {/* Clean Menu Button */}
        <Box
          sx={{
            position: 'absolute',
            top: 8,
            right: 8,
            opacity: isHovered ? 1 : 0.4,
            transition: 'opacity 0.2s ease',
          }}
        >
          <MenuButton
            kb={kb}
            onEdit={onEdit}
            onDelete={onDelete}
            theme={theme}
            sx={{
              width: 28,
              height: 28,
              borderRadius: 1.5,
              backgroundColor: 'background.paper',
              border: (themeVal: Theme) => `1px solid ${themeVal.palette.divider}`,
              color: 'text.secondary',
              '&:hover': {
                backgroundColor: 'background.default',
                borderColor: 'text.primary',
                color: 'text.primary',
              },
            }}
          />
        </Box>
      </Paper>
    </Grid>
  );
});

GridItem.displayName = 'GridItem';

const ListViewComponent = memo<{
  knowledgeBases: KnowledgeBase[];
  navigateToKB: (kb: KnowledgeBase) => void;
  onEdit: (kb: KnowledgeBase) => void;
  onDelete: (kb: KnowledgeBase) => void;
  theme: any;
  totalCount: number;
  page: number;
  rowsPerPage: number;
  onPageChange: (page: number) => void;
  onRowsPerPageChange: (rowsPerPage: number) => void;
  loading: boolean;
}>(
  ({
    knowledgeBases,
    navigateToKB,
    onEdit,
    onDelete,
    theme,
    totalCount,
    page,
    rowsPerPage,
    onPageChange,
    onRowsPerPageChange,
    loading,
  }) => {
    const columns: GridColDef[] = [
      {
        field: 'name',
        headerName: 'Name',
        flex: 1,
        minWidth: 250,
        renderCell: (params) => {
          const kb = params.row;
          const kbIcon = getKBIcon(kb.name);
          return (
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                height: '100%',
                width: '100%',
                pl: 2.5,
              }}
            >
              <Box
                sx={{
                  width: 36,
                  height: 36,
                  borderRadius: 1.5,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: (themeVal) => alpha(themeVal.palette.grey[500], 0.1),
                  color: 'text.secondary',
                  mr: 2,
                  flexShrink: 0,
                }}
              >
                <Icon icon={kbIcon} fontSize={18} />
              </Box>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {kb.name}
              </Typography>
            </Box>
          );
        },
      },
      {
        field: 'userRole',
        headerName: 'Role',
        width: 180,
        align: 'center',
        headerAlign: 'center',
        renderCell: (params) => (
          <Chip
            label={params.row.userRole}
            size="small"
            variant="outlined"
            sx={{
              fontSize: '0.7rem',
              height: 24,
              borderRadius: 1,
              ml: 2,
              borderColor: 'divider',
              color: 'text.secondary',
            }}
          />
        ),
      },
      {
        field: 'updatedAtTimestamp',
        headerName: 'Last Updated',
        width: 180,
        align: 'left',
        headerAlign: 'left',
        renderCell: (params) => {
          const date = new Date(params.row.updatedAtTimestamp);
          return (
            <Box sx={{ pl: 2.5, mt: 2 }}>
              <Typography
                variant="caption"
                display="block"
                color="text.primary"
                sx={{ fontWeight: 500 }}
              >
                {date.toLocaleDateString()}
              </Typography>
              <Typography variant="caption" display="block" color="text.secondary">
                {date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </Typography>
            </Box>
          );
        },
      },
      {
        field: 'createdAtTimestamp',
        headerName: 'Created',
        width: 180,
        align: 'left',
        headerAlign: 'left',
        renderCell: (params) => {
          const date = new Date(params.row.createdAtTimestamp);
          return (
            <Box sx={{ pl: 1.5, mt: 2 }}>
              <Typography
                variant="caption"
                display="block"
                color="text.primary"
                sx={{ fontWeight: 500 }}
              >
                {date.toLocaleDateString()}
              </Typography>
              <Typography variant="caption" display="block" color="text.secondary">
                {date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </Typography>
            </Box>
          );
        },
      },
      {
        field: 'actions',
        headerName: 'Actions',
        width: 100,
        sortable: false,
        align: 'center',
        headerAlign: 'center',
        renderCell: (params) => (
          <MenuButton kb={params.row} onEdit={onEdit} onDelete={onDelete} theme={theme} />
        ),
      },
    ];

    const handleRowClick = (params: any) => {
      navigateToKB(params.row);
    };

    if (loading && knowledgeBases.length === 0) {
      return (
        <Paper
          elevation={0}
          sx={{
            height: 600,
            display: 'flex',
            flexDirection: 'column',
            borderRadius: 2,
            border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
            overflow: 'hidden',
          }}
        >
          <Box sx={{ flexGrow: 1 }}>
            {Array.from(new Array(8)).map((_, index) => (
              <React.Fragment key={index}>
                <ListItem sx={{ py: 2, px: 3 }}>
                  <ListItemIcon>
                    <Skeleton variant="circular" width={40} height={40} />
                  </ListItemIcon>
                  <ListItemText
                    primary={<Skeleton variant="text" width="40%" height={24} />}
                    secondary={<Skeleton variant="text" width="60%" height={20} />}
                  />
                  <ListItemSecondaryAction>
                    <Skeleton variant="text" width={80} height={20} />
                  </ListItemSecondaryAction>
                </ListItem>
                {index < 7 && <Divider />}
              </React.Fragment>
            ))}
          </Box>
        </Paper>
      );
    }

    return (
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          height: 'calc(100vh - 250px)',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 2,
          border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
        }}
      >
        <Box sx={{ flexGrow: 1, height: 'calc(100% - 64px)' }}>
          <DataGrid
            rows={knowledgeBases}
            columns={columns}
            hideFooterPagination
            disableRowSelectionOnClick
            onRowClick={handleRowClick}
            getRowId={(row) => row.id}
            rowHeight={64}
            localeText={{
              noRowsLabel: 'No knowledge bases found',
            }}
            sx={{
              border: 'none',
              height: '100%',
              '& .MuiDataGrid-columnHeaders': {
                backgroundColor: (themeVal) => alpha(themeVal.palette.background.default, 0.5),
                minHeight: '56px !important',
                height: '56px !important',
                maxHeight: '56px !important',
                lineHeight: '56px !important',
              },
              '& .MuiDataGrid-columnHeader': {
                height: '56px !important',
                maxHeight: '56px !important',
                lineHeight: '56px !important',
                paddingLeft: 2,
              },
              '& .MuiDataGrid-columnHeaderTitle': {
                fontWeight: 600,
                fontSize: '0.875rem',
                color: 'text.primary',
              },
              '& .MuiDataGrid-cell': {
                border: 'none',
                paddingLeft: 1,
                maxHeight: '64px !important',
                minHeight: '64px !important',
                height: '64px !important',
                lineHeight: '64px !important',
              },
              '& .MuiDataGrid-cellContent': {
                maxHeight: '64px !important',
                height: '64px !important',
                lineHeight: '64px !important',
              },
              '& .MuiDataGrid-row': {
                maxHeight: '64px !important',
                minHeight: '64px !important',
                height: '64px !important',
                '&:hover': {
                  backgroundColor: 'action.hover',
                  cursor: 'pointer',
                },
              },
              '& .MuiDataGrid-cell:focus, .MuiDataGrid-cell:focus-within': {
                outline: 'none',
              },
              '& .MuiDataGrid-columnHeader:focus, .MuiDataGrid-columnHeader:focus-within': {
                outline: 'none',
              },
            }}
          />
        </Box>

        {/* Pagination footer */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            px: 3,
            py: 2,
            borderTop: (themeVal) => `1px solid ${themeVal.palette.divider}`,
            backgroundColor: (themeVal) => alpha(themeVal.palette.background.default, 0.5),
            height: '64px',
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {totalCount === 0
              ? 'No knowledge bases found'
              : `Showing ${page * rowsPerPage + 1}-${Math.min((page + 1) * rowsPerPage, totalCount)} of ${totalCount} knowledge bases`}
          </Typography>

          <TablePagination
            component="div"
            count={totalCount}
            page={page}
            onPageChange={(event, newPage) => onPageChange(newPage)}
            rowsPerPage={rowsPerPage}
            onRowsPerPageChange={(event) => onRowsPerPageChange(parseInt(event.target.value, 10))}
            rowsPerPageOptions={[10, 20, 50, 100]}
            labelRowsPerPage="Items per page:"
            labelDisplayedRows={({ from, to, count }) =>
              `${from}–${to} of ${count !== -1 ? count : `more than ${to}`}`
            }
            sx={{
              '& .MuiTablePagination-toolbar': {
                px: 0,
                py: 0,
                minHeight: 'auto',
              },
              '& .MuiTablePagination-selectLabel, & .MuiTablePagination-displayedRows': {
                fontSize: '0.875rem',
                color: 'text.secondary',
                fontWeight: 500,
                margin: 0,
              },
              '& .MuiTablePagination-select': {
                fontSize: '0.875rem',
                fontWeight: 500,
                borderRadius: 1,
              },
              '& .MuiTablePagination-actions': {
                '& .MuiIconButton-root': {
                  borderRadius: 1,
                  width: 32,
                  height: 32,
                  '&:hover': {
                    backgroundColor: 'action.hover',
                  },
                },
              },
            }}
          />
        </Box>
      </Paper>
    );
  }
);

ListViewComponent.displayName = 'ListViewComponent';

// Loading skeletons
const GridSkeleton = memo<{ count?: number }>(({ count = 20 }) => (
  <Grid container spacing={2.5}>
    {Array.from(new Array(count)).map((_, index) => (
      <Grid item xs={12} sm={6} md={4} lg={3} key={index}>
        <Skeleton
          variant="rounded"
          height={140}
          sx={{
            borderRadius: 2,
            border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
          }}
          animation="wave"
        />
      </Grid>
    ))}
  </Grid>
));

GridSkeleton.displayName = 'GridSkeleton';

// Empty State Component
const EmptyState = memo<{
  isSearchResult?: boolean;
  searchQuery?: string;
  onClearSearch?: () => void;
  onCreateKB?: () => void;
  loading?: boolean;
}>(({ isSearchResult = false, searchQuery = '', onClearSearch, onCreateKB, loading = false }) => (
  <Paper
    sx={{
      p: 6,
      textAlign: 'center',
      borderRadius: 2,
      border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
      backgroundColor: 'background.paper',
    }}
  >
    <Icon icon={searchIcon} fontSize={48} color="rgba(0,0,0,0.3)" />
    <Typography variant="h6" color="text.secondary" sx={{ mt: 2, mb: 1 }}>
      No knowledge bases found
    </Typography>
    <Typography variant="body2" color="text.secondary">
      {isSearchResult
        ? `No results found for "${searchQuery}"`
        : 'Create your first knowledge base to get started'}
    </Typography>
    {isSearchResult && onClearSearch && (
      <Button
        variant="outlined"
        onClick={onClearSearch}
        sx={{
          mt: 2,
          borderRadius: 2,
          textTransform: 'none',
          borderColor: 'divider',
          color: 'text.primary',
          '&:hover': {
            borderColor: 'primary.main',
            backgroundColor: 'action.hover',
          },
        }}
      >
        Clear search
      </Button>
    )}
    {!isSearchResult && !loading && onCreateKB && (
      <Button
        variant="outlined"
        onClick={onCreateKB}
        sx={{
          mt: 2,
          borderRadius: 2,
          textTransform: 'none',
          borderColor: 'primary.main',
          color: 'primary.main',
          '&:hover': {
            backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
          },
        }}
        startIcon={<Icon icon={addIcon} fontSize={16} />}
      >
        Create Knowledge Base
      </Button>
    )}
  </Paper>
));

EmptyState.displayName = 'EmptyState';

// Enhanced Clean Dashboard Component with Modern Header
const DashboardComponent: React.FC<DashboardProps> = ({
  theme,
  navigateToKB,
  onEdit,
  onDelete,
  setCreateKBDialog,
  CompactCard,
  ActionButton,
  isInitialized,
  navigate,
}) => {
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Knowledge base state
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(20);

  // Debounced search value
  const debouncedSearchQuery = useDebounce(searchQuery, 300);

  // Refs to prevent multiple simultaneous calls
  const loadingRef = useRef(false);
  const currentSearchRef = useRef('');

  // Load knowledge bases function
const loadKnowledgeBases = useCallback(
  async (queryString = '', isLoadMore = false, pageNum = 0, pageSize = 20) => {
    if (!isInitialized || loadingRef.current) return;

    // Prevent loading more if already loading or no more data for grid view
    if (isLoadMore && (loadingMore || !hasMore)) return;

    loadingRef.current = true;
    currentSearchRef.current = queryString;

    // Set appropriate loading states
    if (isLoadMore) {
      setLoadingMore(true);
    } else {
      setLoading(true);
      if (queryString !== currentSearchRef.current) {
        setKnowledgeBases([]);
        setHasMore(true);
        setPage(0);
      }
    }

    try {
      const params = {
        page: isLoadMore ? Math.floor(knowledgeBases.length / pageSize) + 1 : pageNum + 1,
        limit: pageSize,
        search: queryString,
      };

      const data = await KnowledgeBaseAPI.getKnowledgeBases(params);

      if (data.knowledgeBases && data.pagination) {
        const newKBs = data.knowledgeBases;
        const totalItems = data.pagination.totalCount;

        if (isLoadMore) {
          setKnowledgeBases((prev) => [...prev, ...newKBs]);
          setHasMore(knowledgeBases.length + newKBs.length < totalItems);
        } else {
          setKnowledgeBases(newKBs);
          setHasMore(newKBs.length < totalItems);
        }

        setTotalCount(totalItems);
      } else if (Array.isArray(data)) {
        if (isLoadMore) {
          setKnowledgeBases((prev) => [...prev, ...data]);
        } else {
          setKnowledgeBases(data);
        }
        setTotalCount(data.length);
        setHasMore(false);
      }
    } catch (err: any) {
      console.error('Failed to fetch knowledge bases:', err);
      if (!isLoadMore) {
        setKnowledgeBases([]);
        setTotalCount(0);
      }
    } finally {
      setLoading(false);
      setLoadingMore(false);
      loadingRef.current = false;
    }
  },
  [isInitialized,knowledgeBases.length, loadingMore,hasMore]
);

  // Effect for debounced search
  useEffect(() => {
    if (isInitialized) {
      loadKnowledgeBases(debouncedSearchQuery, false, 0, rowsPerPage);
    }
    // eslint-disable-next-line
  }, [debouncedSearchQuery, isInitialized, rowsPerPage]);

  // // Initial load
  // useEffect(() => {
  //   if (isInitialized) {
  //     loadKnowledgeBases('', false, 0, rowsPerPage);
  //   }
  // }, [isInitialized, loadKnowledgeBases, rowsPerPage]);

  // Load more for infinite scroll
  const handleLoadMore = useCallback(() => {
    if (!loadingMore && hasMore && !loading) {
      loadKnowledgeBases(debouncedSearchQuery, true, 0, rowsPerPage);
    }
  }, [loadingMore, hasMore, loading, debouncedSearchQuery, rowsPerPage, loadKnowledgeBases]);

  // Handlers
  const handleViewChange = useCallback(
    (event: React.MouseEvent<HTMLElement>, newView: ViewMode | null): void => {
      if (newView !== null) {
        setViewMode(newView);
      }
    },
    []
  );

  const handleSearchChange = useCallback((event: React.ChangeEvent<HTMLInputElement>): void => {
    setSearchQuery(event.target.value);
  }, []);

  const handleClearSearch = useCallback((): void => {
    setSearchQuery('');
    setPage(0);
  }, []);

  const handlePageChange = useCallback(
    (newPage: number) => {
      setPage(newPage);
      loadKnowledgeBases(debouncedSearchQuery, false, newPage, rowsPerPage);
    },
    [debouncedSearchQuery, rowsPerPage, loadKnowledgeBases]
  );

  const handleRowsPerPageChange = useCallback(
    (newRowsPerPage: number) => {
      setRowsPerPage(newRowsPerPage);
      setPage(0);
      loadKnowledgeBases(debouncedSearchQuery, false, 0, newRowsPerPage);
    },
    [debouncedSearchQuery, loadKnowledgeBases]
  );

  const handleCreateKB = useCallback(() => {
    setCreateKBDialog(true);
  }, [setCreateKBDialog]);

  // Intersection observer for infinite scroll
  const loadMoreRef = useIntersectionObserver(handleLoadMore);

  // Memoized filtered knowledge bases
  const filteredKnowledgeBases = useMemo(() => knowledgeBases, [knowledgeBases]);

  // Memoized components (GridView and ListView remain the same)
  const GridView = useMemo(() => {
    if (loading && knowledgeBases.length === 0) {
      return <GridSkeleton />;
    }

    if (filteredKnowledgeBases.length === 0 && !loading) {
      return (
        <EmptyState
          isSearchResult={!!debouncedSearchQuery}
          searchQuery={debouncedSearchQuery}
          onClearSearch={handleClearSearch}
          onCreateKB={handleCreateKB}
          loading={loading}
        />
      );
    }

    return (
      <Box>
        <Grid container spacing={2.5}>
          {filteredKnowledgeBases.map((kb) => (
            <GridItem
              key={kb.id}
              kb={kb}
              navigateToKB={navigateToKB}
              onEdit={onEdit}
              onDelete={onDelete}
              theme={theme}
              CompactCard={CompactCard}
            />
          ))}
        </Grid>

        {/* Infinite Scroll Trigger and Loading More Indicator */}
        {hasMore && !debouncedSearchQuery && (
          <Box
            ref={loadMoreRef}
            sx={{
              mt: 4,
              mb: 3,
              textAlign: 'center',
              borderTop: (themeVal) => `1px solid ${alpha(themeVal.palette.divider, 0.5)}`,
              pt: 3,
            }}
          >
            {loadingMore ? (
              <Box sx={{ py: 2 }}>
                <CircularProgress size={20} thickness={4} />
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{
                    mt: 1,
                    fontSize: '0.8125rem',
                  }}
                >
                  Loading more knowledge bases...
                </Typography>
              </Box>
            ) : (
              <Box sx={{ py: 1 }}>
                <Button
                  variant="outlined"
                  onClick={handleLoadMore}
                  sx={{
                    height: 36,
                    px: 3,
                    borderRadius: 1.5,
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    textTransform: 'none',
                    borderColor: 'divider',
                    color: 'text.secondary',
                    backgroundColor: 'background.paper',
                    '&:hover': {
                      borderColor: 'primary.main',
                      backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                      color: 'primary.main',
                    },
                  }}
                >
                  Load More
                </Button>
              </Box>
            )}
          </Box>
        )}

        {/* Loading skeletons for infinite scroll */}
        {loadingMore && (
          <Box sx={{ mt: 2, mb: 3 }}>
            <GridSkeleton count={8} />
          </Box>
        )}
      </Box>
    );
  }, [
    loading,
    knowledgeBases.length,
    filteredKnowledgeBases,
    debouncedSearchQuery,
    hasMore,
    loadingMore,
    navigateToKB,
    onEdit,
    onDelete,
    theme,
    CompactCard,
    handleLoadMore,
    handleClearSearch,
    handleCreateKB,
    loadMoreRef,
  ]);

  const ListView = useMemo(
    () => (
      <ListViewComponent
        knowledgeBases={filteredKnowledgeBases}
        navigateToKB={navigateToKB}
        onEdit={onEdit}
        onDelete={onDelete}
        theme={theme}
        totalCount={totalCount}
        page={page}
        rowsPerPage={rowsPerPage}
        onPageChange={handlePageChange}
        onRowsPerPageChange={handleRowsPerPageChange}
        loading={loading}
      />
    ),
    [
      filteredKnowledgeBases,
      navigateToKB,
      onEdit,
      onDelete,
      theme,
      totalCount,
      page,
      rowsPerPage,
      handlePageChange,
      handleRowsPerPageChange,
      loading,
    ]
  );

  // Calculate display counts
  const { displayCount, actualTotalCount } = useMemo(
    () => ({
      displayCount: filteredKnowledgeBases.length,
      actualTotalCount: totalCount,
    }),
    [filteredKnowledgeBases.length, totalCount]
  );

  if (!isInitialized) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
        }}
      >
        <CircularProgress size={40} thickness={4} />
      </Box>
    );
  }

  return (
    <Box sx={{ height: '90vh', display: 'flex', flexDirection: 'column' }}>
      {/* Enhanced Clean Header */}
      <Box
        sx={{
          borderBottom: (themeVal) => `1px solid ${themeVal.palette.divider}`,
          backgroundColor: 'background.paper',
          px: { xs: 2, sm: 3 },
          py: 2,
        }}
      >
        {/* Loading progress bar */}
        {(loading || loadingMore) && (
          <LinearProgress
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 2,
              backgroundColor: 'transparent',
            }}
          />
        )}

        {/* Single row layout */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={3}
          sx={{
            width: '100%',
            minHeight: 56,
          }}
        >
          {/* Title Section */}
          <Box sx={{ flexShrink: 0 }}>
            <Typography
              variant="h5"
              sx={{
                fontSize: '1.5rem',
                fontWeight: 700,
                color: 'text.primary',
                letterSpacing: '-0.02em',
                lineHeight: 1.2,
              }}
            >
              Knowledge Bases
            </Typography>
            {actualTotalCount > 0 && (
              <Typography
                variant="caption"
                sx={{
                  fontSize: '0.75rem',
                  color: 'text.secondary',
                  fontWeight: 500,
                }}
              >
                {actualTotalCount} total
              </Typography>
            )}
          </Box>

          {/* Spacer */}
          <Box sx={{ flexGrow: 1 }} />

          {/* Search Bar */}
          <Box sx={{ flexShrink: 0, minWidth: 300 }}>
            <TextField
              placeholder="Search knowledge bases..."
              value={searchQuery}
              onChange={handleSearchChange}
              size="small"
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Icon icon={searchIcon} fontSize={18} color="action.active" />
                  </InputAdornment>
                ),
                endAdornment: searchQuery && (
                  <InputAdornment position="end">
                    <Box
                      component="button"
                      onClick={handleClearSearch}
                      sx={{
                        width: 20,
                        height: 20,
                        borderRadius: 0.5,
                        border: 'none',
                        backgroundColor: 'transparent',
                        color: 'action.active',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        cursor: 'pointer',
                        '&:hover': {
                          backgroundColor: 'action.hover',
                          color: 'text.primary',
                        },
                      }}
                    >
                      <Icon icon={clearIcon} fontSize={14} />
                    </Box>
                  </InputAdornment>
                ),
              }}
              sx={{
                width: '100%',
                '& .MuiOutlinedInput-root': {
                  height: 36,
                  borderRadius: 1.5,
                  backgroundColor: 'background.default',
                  border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                  fontSize: '0.875rem',
                  '&:hover': {
                    borderColor: 'action.active',
                  },
                  '&.Mui-focused': {
                    borderColor: 'primary.main',
                    backgroundColor: 'background.paper',
                  },
                  '& .MuiOutlinedInput-notchedOutline': {
                    border: 'none',
                  },
                },
                '& .MuiInputBase-input::placeholder': {
                  color: 'text.secondary',
                  opacity: 0.7,
                  fontSize: '0.875rem',
                },
              }}
            />
            {searchQuery && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  mt: 0.5,
                  display: 'block',
                  fontSize: '0.7rem',
                }}
              >
                {displayCount} of {actualTotalCount} results
              </Typography>
            )}
          </Box>

          {/* View Toggle */}
          <ToggleButtonGroup
            value={viewMode}
            exclusive
            onChange={handleViewChange}
            size="small"
            sx={{
              flexShrink: 0,
              '& .MuiToggleButton-root': {
                border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                borderRadius: 1,
                px: 1.5,
                py: 0.5,
                minWidth: 36,
                height: 32,
                color: 'text.secondary',
                '&.Mui-selected': {
                  backgroundColor: 'action.selected',
                  color: 'text.primary',
                  borderColor: 'divider',
                },
                '&:hover': {
                  backgroundColor: 'action.hover',
                },
              },
            }}
          >
            <ToggleButton value="grid">
              <Icon icon={gridViewIcon} fontSize={16} />
            </ToggleButton>
            <ToggleButton value="list">
              <Icon icon={listViewIcon} fontSize={16} />
            </ToggleButton>
          </ToggleButtonGroup>

          {/* Action Buttons */}
          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            <Button
              variant="outlined"
              startIcon={<Icon icon={databaseIcon} fontSize={14} />}
              onClick={() => navigate({ view: 'all-records' })}
              sx={{
                height: 32,
                px: 1.5,
                borderRadius: 1,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                borderColor: 'divider',
                color: 'text.secondary',
                '&:hover': {
                  borderColor: 'action.active',
                  backgroundColor: 'action.hover',
                  color: 'text.primary',
                },
              }}
            >
              <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>All Records</Box>
            </Button>

            <Button
              variant="outlined"
              startIcon={<Icon icon={addIcon} fontSize={14} />}
              onClick={handleCreateKB}
              sx={{
                height: 32,
                px: 1.5,
                borderRadius: 1,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                borderColor: 'primary.main',
                color: 'primary.main',
                '&:hover': {
                  backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                  borderColor: 'primary.dark',
                },
              }}
            >
              <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>Create KB</Box>
            </Button>
          </Stack>
        </Stack>
      </Box>

      {/* Content Area */}
      <Box
        sx={{
          flex: 1,
          overflow: 'auto',
          display: 'flex',
          flexDirection: 'column',
          '&::-webkit-scrollbar': {
            width: '6px',
          },
          '&::-webkit-scrollbar-track': {
            background: 'transparent',
          },
          '&::-webkit-scrollbar-thumb': {
            background: (themeVal) => alpha(themeVal.palette.grey[500], 0.3),
            borderRadius: '3px',
            '&:hover': {
              background: (themeVal) => alpha(themeVal.palette.grey[500], 0.5),
            },
          },
        }}
      >
        <Container
          maxWidth="xl"
          sx={{
            py: { xs: 2, sm: 3 },
            px: { xs: 2, sm: 3 },
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <Fade in timeout={300}>
            <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              {viewMode === 'grid' ? GridView : ListView}
            </Box>
          </Fade>
        </Container>

        {/* Fixed Status Footer */}
        {viewMode === 'grid' && !debouncedSearchQuery && filteredKnowledgeBases.length > 0 && (
          <Box
            sx={{
              borderTop: (themeVal) => `1px solid ${themeVal.palette.divider}`,
              backgroundColor: 'background.paper',
              py: 1.5,
              px: 3,
              textAlign: 'center',
              mt: 'auto',
              flexShrink: 0,
            }}
          >
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
              Showing {displayCount} of {actualTotalCount} knowledge bases
              {hasMore && (
                <Box
                  component="span"
                  sx={{
                    ml: 1,
                    px: 1,
                    py: 0.25,
                    borderRadius: 0.5,
                    backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.1),
                    color: 'primary.main',
                    fontSize: '0.7rem',
                    fontWeight: 500,
                  }}
                >
                  More available
                </Box>
              )}
            </Typography>
          </Box>
        )}
      </Box>
    </Box>
  );
};

export default memo(DashboardComponent);
