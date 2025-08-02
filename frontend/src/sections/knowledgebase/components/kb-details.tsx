import {
  Fade,
  Box,
  Stack,
  ToggleButtonGroup,
  ToggleButton,
  Typography,
  alpha,
  Button,
  styled,
  IconButton,
  Paper,
  Theme,
} from '@mui/material';
import { Icon } from '@iconify/react';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import viewListIcon from '@iconify-icons/mdi/view-list';
import viewGridIcon from '@iconify-icons/mdi/view-grid';
import folderPlusIcon from '@iconify-icons/mdi/folder-plus';
import fileUploadIcon from '@iconify-icons/mdi/file-upload';
import accountMultipleIcon from '@iconify-icons/mdi/account-multiple';
import refreshIcon from '@iconify-icons/mdi/refresh';
import folderIcon from '@iconify-icons/mdi/folder-outline';
import { renderSmartBreadcrumbs } from './breadcrumbs';
import { GridView } from './folder-grid-view';
import { ListView } from './folder-list-view';

const ModernToolbar = styled(Paper)(({ theme }) => ({
  padding: theme.spacing(1, 1.5),
  borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
  backgroundColor: alpha(theme.palette.background.paper, 0.9),
  backdropFilter: 'blur(20px)',
  display: 'flex',
  flexDirection: 'column',
  gap: theme.spacing(1.5),
  borderRadius: 0,
  boxShadow: 'none',
  minHeight: 'auto',

  [theme.breakpoints.up('md')]: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: theme.spacing(2),
    padding: theme.spacing(1.5, 3),
  },

  [theme.breakpoints.up('lg')]: {
    minHeight: 60,
  },
}));

const ActionButton = styled(Button)(({ theme }) => ({
  height: 32,
  padding: `${theme.spacing(0.75)} ${theme.spacing(1.75)}`, // replaces px/py
  borderRadius: '4px',
  textTransform: 'none',
  fontSize: '0.8125rem',
  fontWeight: 500,
  minWidth: 100,
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.23)' : 'rgba(0,0,0,0.23)',
  color: theme.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
  backgroundColor: 'transparent',
  '&:hover': {
    borderColor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.4)',
    backgroundColor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.03)',
  },
}));

const CompactIconButton = styled(IconButton)(({ theme }) => ({
  width: 36,
  height: 36,
  borderRadius: 10,
  border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
  backgroundColor: alpha(theme.palette.background.paper, 0.8),
  backdropFilter: 'blur(10px)',
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.08),
    borderColor: alpha(theme.palette.primary.main, 0.2),
    transform: 'scale(1.05)',
  },
}));

interface KBDetailsProps {
  navigationPath: any;
  navigateToDashboard: any;
  navigateToPathIndex: any;
  theme: Theme;
  viewMode: any;
  handleViewChange: any;
  navigateBack: any;
  CompactCard: any;
  items: any;
  pageLoading: any;
  navigateToFolder: any;
  handleMenuOpen: any;
  totalCount: any;
  hasMore: any;
  loadingMore: any;
  handleLoadMore: any;
  currentKB: any;
  loadKBContents: any;
  stableRoute: any;
  currentUserPermission: any;
  setCreateFolderDialog: any;
  setUploadDialog: any;
  openPermissionsDialog: any;
  handleRefresh: any;
  setPage: any;
  setRowsPerPage: any;
  rowsPerPage: any;
  page: any;
}

export const renderKBDetail = ({
  navigationPath,
  navigateToDashboard,
  navigateToPathIndex,
  theme,
  viewMode,
  handleViewChange,
  navigateBack,
  CompactCard,
  items,
  pageLoading,
  navigateToFolder,
  handleMenuOpen,
  totalCount,
  hasMore,
  loadingMore,
  handleLoadMore,
  currentKB,
  loadKBContents,
  stableRoute,
  currentUserPermission,
  setCreateFolderDialog,
  setUploadDialog,
  openPermissionsDialog,
  handleRefresh,
  setPage,
  setRowsPerPage,
  rowsPerPage,
  page,
}: KBDetailsProps) => (
  <Fade in timeout={300}>
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <ModernToolbar
        elevation={0}
        sx={{
          borderBottom: (themeVal) => `1px solid ${themeVal.palette.divider}`,
          backgroundColor: 'background.paper',
          px: { xs: 2, sm: 3 },
          py: 1,
          minHeight: 'auto',
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={2}
          sx={{
            width: '100%',
            minHeight: 48,
          }}
        >
          <Box
            component="button"
            onClick={navigateBack}
            sx={{
              width: 32,
              height: 32,
              borderRadius: 1,
              border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
              backgroundColor: 'transparent',
              color: 'text.secondary',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              transition: 'all 0.2s ease',
              flexShrink: 0,
              '&:hover': {
                borderColor: 'action.active',
                color: 'text.primary',
                backgroundColor: 'action.hover',
              },
            }}
          >
            <Icon icon={arrowLeftIcon} fontSize={16} />
          </Box>

          <Box sx={{ flexGrow: 1, minWidth: 0 }}>
            {renderSmartBreadcrumbs(
              navigationPath,
              navigateToDashboard,
              navigateToPathIndex,
              theme
            )}
          </Box>

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
            <ToggleButton value="list">
              <Icon icon={viewListIcon} fontSize={16} />
            </ToggleButton>
            <ToggleButton value="grid">
              <Icon icon={viewGridIcon} fontSize={16} />
            </ToggleButton>
          </ToggleButtonGroup>

          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            {currentUserPermission?.canCreateFolders && (
              <ActionButton
                variant="outlined"
                size="small"
                startIcon={
                  <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>
                    <Icon icon={folderPlusIcon} fontSize={14} />
                  </Box>
                }
                onClick={() => setCreateFolderDialog(true)}
                sx={{
                  height: 32,
                  px: { xs: 1, sm: 1.5 },
                  borderRadius: 1,
                  borderColor: 'divider',
                  color: 'text.secondary',
                  backgroundColor: 'transparent',
                  fontSize: '0.8125rem',
                  fontWeight: 500,
                  textTransform: 'none',
                  minWidth: { xs: 'auto', sm: 'auto' },
                  '&:hover': {
                    borderColor: 'action.active',
                    backgroundColor: 'action.hover',
                    color: 'text.primary',
                  },
                }}
              >
                <Box sx={{ display: { xs: 'inline', sm: 'none' } }}>
                  <Icon icon={folderPlusIcon} fontSize={16} />
                </Box>
                <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>New Folder</Box>
              </ActionButton>
            )}

            {currentUserPermission?.canUpload && (
              <ActionButton
                variant="outlined"
                size="small"
                startIcon={
                  <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>
                    <Icon icon={fileUploadIcon} fontSize={14} />
                  </Box>
                }
                onClick={() => setUploadDialog(true)}
                sx={{
                  height: 32,
                  px: { xs: 1, sm: 1.5 },
                  borderRadius: 1,
                  borderColor: 'primary.main',
                  color: 'primary.main',
                  backgroundColor: 'transparent',
                  fontSize: '0.8125rem',
                  fontWeight: 500,
                  textTransform: 'none',
                  minWidth: { xs: 'auto', sm: 'auto' },
                  '&:hover': {
                    backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                  },
                }}
              >
                <Box sx={{ display: { xs: 'inline', sm: 'none' } }}>
                  <Icon icon={fileUploadIcon} fontSize={16} />
                </Box>
                <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>Upload</Box>
              </ActionButton>
            )}

            <Box
              component="button"
              onClick={openPermissionsDialog}
              sx={{
                width: 32,
                height: 32,
                borderRadius: 1,
                border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                backgroundColor: 'transparent',
                color: 'text.secondary',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                '&:hover': {
                  borderColor: 'action.active',
                  color: 'text.primary',
                  backgroundColor: 'action.hover',
                },
              }}
            >
              <Icon icon={accountMultipleIcon} fontSize={16} />
            </Box>

            <Box
              component="button"
              onClick={handleRefresh}
              sx={{
                width: 32,
                height: 32,
                borderRadius: 1,
                border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                backgroundColor: 'transparent',
                color: 'text.secondary',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                '&:hover': {
                  borderColor: 'action.active',
                  color: 'text.primary',
                  backgroundColor: 'action.hover',
                },
              }}
            >
              <Icon icon={refreshIcon} fontSize={16} />
            </Box>
          </Stack>
        </Stack>
      </ModernToolbar>

      <Box sx={{ flexGrow: 1, overflow: 'auto', p: { xs: 1.5, sm: 2, md: 3 } }}>
        {items.length === 0 && !pageLoading ? (
          <Fade in>
            <Box
              sx={{
                textAlign: 'center',
                py: { xs: 4, sm: 6, md: 8 },
                color: 'text.secondary',
                px: 2,
              }}
            >
              <Icon
                icon={folderIcon}
                style={{
                  fontSize: 48,
                  marginBottom: 16,
                  opacity: 0.3,
                  color: theme.palette.text.secondary,
                }}
              />
              <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
                This location is empty
              </Typography>
              <Typography variant="body2" sx={{ mb: 3 }}>
                Upload files or create folders to get started
              </Typography>
              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                spacing={2}
                justifyContent="center"
                sx={{ maxWidth: 300, mx: 'auto' }}
              >
                <ActionButton
                  variant="outlined"
                  startIcon={<Icon icon={fileUploadIcon} fontSize={14} />}
                  onClick={() => setUploadDialog(true)}
                  size="small"
                  sx={{
                    width: { xs: '100%', sm: 'auto' },
                    borderColor: 'primary.main',
                    color: 'primary.main',
                    '&:hover': {
                      backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                    },
                  }}
                >
                  Upload Files
                </ActionButton>
                <ActionButton
                  variant="outlined"
                  startIcon={<Icon icon={folderPlusIcon} fontSize={14} />}
                  onClick={() => setCreateFolderDialog(true)}
                  size="small"
                  sx={{
                    width: { xs: '100%', sm: 'auto' },
                  }}
                >
                  Create Folder
                </ActionButton>
              </Stack>
            </Box>
          </Fade>
        ) : viewMode === 'grid' ? (
          <GridView
            items={items}
            pageLoading={pageLoading}
            navigateToFolder={navigateToFolder}
            handleMenuOpen={handleMenuOpen}
            CompactCard={CompactCard}
            CompactIconButton={CompactIconButton}
            totalCount={totalCount}
            hasMore={hasMore}
            loadingMore={loadingMore}
            onLoadMore={handleLoadMore}
          />
        ) : (
          <ListView
            items={items}
            pageLoading={pageLoading}
            navigateToFolder={navigateToFolder}
            handleMenuOpen={handleMenuOpen}
            totalCount={totalCount}
            rowsPerPage={rowsPerPage}
            page={page}
            setPage={setPage}
            setRowsPerPage={setRowsPerPage}
            currentKB={currentKB}
            loadKBContents={loadKBContents}
            route={stableRoute}
            CompactIconButton={CompactIconButton}
          />
        )}
      </Box>
    </Box>
  </Fade>
);
