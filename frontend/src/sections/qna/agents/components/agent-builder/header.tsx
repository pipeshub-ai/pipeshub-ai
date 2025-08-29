// src/sections/qna/agents/components/flow-builder-header.tsx
import React, { useState } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Tooltip,
  TextField,
  Button,
  Stack,
  Breadcrumbs,
  Link,
  useTheme,
  alpha,
  CircularProgress,
  Snackbar,
  Alert,
  useMediaQuery,
  ButtonGroup,
  Chip,
} from '@mui/material';
import { Icon } from '@iconify/react';
import saveIcon from '@iconify-icons/mdi/content-save';
import homeIcon from '@iconify-icons/mdi/home';
import menuIcon from '@iconify-icons/mdi/menu';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import fileIcon from '@iconify-icons/mdi/file-document-outline';
import shareIcon from '@iconify-icons/mdi/share-outline';
import closeIcon from '@iconify-icons/eva/close-outline';
import type { AgentBuilderHeaderProps } from '../../types/agent';
import AgentPermissionsDialog from './agent-permissions-dialog';

type SnackbarSeverity = 'success' | 'error' | 'warning' | 'info';

const AgentBuilderHeader: React.FC<AgentBuilderHeaderProps> = ({
  sidebarOpen,
  setSidebarOpen,
  agentName,
  setAgentName,
  saving,
  onSave,
  onClose,
  editingAgent,
  originalAgentName,
  templateDialogOpen,
  setTemplateDialogOpen,
  templatesLoading,
  agentId,
}) => {
  const theme = useTheme();
  const [shareAgentDialogOpen, setShareAgentDialogOpen] = useState(false);
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const isTablet = useMediaQuery(theme.breakpoints.down('md'));
  const isLargeScreen = useMediaQuery(theme.breakpoints.up('xl'));

  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: SnackbarSeverity;
  }>({
    open: false,
    message: '',
    severity: 'success',
  });

  const handleCloseSnackbar = (): void => {
    setSnackbar({
      open: false,
      message: '',
      severity: 'success',
    });
  };

  return (
    <Box
      sx={{
        px: { xs: 1, sm: 2, md: 3 },
        py: 1.5,
        borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
        bgcolor: alpha(theme.palette.background.paper, 0.8),
        backdropFilter: 'blur(12px)',
        display: 'flex',
        alignItems: 'center',
        gap: { xs: 1, sm: 1.5, md: 2 },
        flexShrink: 0,
        minHeight: { xs: 56, sm: 64 },
        boxSizing: 'border-box',
        position: 'sticky',
        top: 0,
        zIndex: 1200,
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}
    >
      {/* Sidebar Toggle */}
      <Tooltip title={sidebarOpen ? 'Hide Sidebar' : 'Show Sidebar'}>
        <IconButton
          onClick={() => setSidebarOpen(!sidebarOpen)}
          size={isMobile ? 'small' : 'medium'}
          sx={{
            border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
            borderRadius: 2,
            bgcolor: alpha(theme.palette.background.default, 0.5),
            '&:hover': {
              bgcolor: alpha(theme.palette.primary.main, 0.08),
              borderColor: alpha(theme.palette.primary.main, 0.3),
              color: theme.palette.primary.main,
            },
            transition: 'all 0.2s ease',
          }}
        >
          <Icon icon={menuIcon} width={isMobile ? 18 : 20} height={isMobile ? 18 : 20} />
        </IconButton>
      </Tooltip>

      {/* Breadcrumbs - Hidden on mobile */}
      {!isMobile && (
        <Breadcrumbs
          separator="›"
          sx={{
            ml: 0.5,
            '& .MuiBreadcrumbs-separator': {
              color: theme.palette.text.secondary,
              mx: 0.5,
            },
          }}
        >
          <Link
            underline="hover"
            color="inherit"
            onClick={onClose}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              cursor: 'pointer',
              fontWeight: 500,
              fontSize: '0.875rem',
              color: theme.palette.text.secondary,
              transition: 'color 0.2s ease',
              '&:hover': { color: theme.palette.primary.main },
            }}
          >
            <Icon icon={homeIcon} width={14} height={14} />
            Agents
          </Link>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              color: theme.palette.text.primary,
              fontWeight: 600,
              fontSize: '0.875rem',
            }}
          >
            <Icon icon={sparklesIcon} width={14} height={14} />
            Flow Builder
          </Box>
        </Breadcrumbs>
      )}

      {/* Mobile Status Indicator */}
      {isMobile && (
        <Chip
          label={editingAgent ? 'Editing' : 'Creating'}
          size="small"
          color={editingAgent ? 'warning' : 'primary'}
          variant="outlined"
          sx={{
            height: 24,
            fontSize: '0.7rem',
            fontWeight: 600,
          }}
        />
      )}

      <Box sx={{ flexGrow: 1 }} />

      {/* Agent Name Input - Responsive width */}
      <TextField
        label="Agent Name"
        value={agentName || ''}
        onChange={(e) => setAgentName(e.target.value)}
        size="small"
        placeholder={isMobile ? 'Agent name...' : 'Enter agent name...'}
        sx={{
          width: { xs: 150, sm: 200, md: 280, lg: 320 },
          '& .MuiOutlinedInput-root': {
            borderRadius: 2,
            bgcolor: alpha(theme.palette.background.default, 0.6),
            backdropFilter: 'blur(8px)',
            border: `1px solid ${alpha(theme.palette.divider, 0.15)}`,
            '&:hover': {
              bgcolor: alpha(theme.palette.background.default, 0.8),
              borderColor: alpha(theme.palette.primary.main, 0.3),
            },
            '&.Mui-focused': {
              bgcolor: theme.palette.background.default,
              borderColor: theme.palette.primary.main,
            },
          },
          '& .MuiInputLabel-root': {
            color: theme.palette.text.secondary,
            fontSize: isMobile ? '0.8rem' : '0.875rem',
          },
        }}
      />

      <Box sx={{ flexGrow: 1 }} />

      {/* Action Buttons - Responsive layout */}
      <Stack direction="row" spacing={1} alignItems="center">
        {/* Template Button - Hidden on mobile, icon only on tablet */}
        {!isMobile && (
          <Tooltip title="Use Template">
            <Button
              variant="outlined"
              size="small"
              startIcon={
                templatesLoading ? (
                  <CircularProgress size={14} color="inherit" />
                ) : (
                  <Icon icon={fileIcon} width={16} height={16} />
                )
              }
              onClick={() => setTemplateDialogOpen(true)}
              disabled={saving || templatesLoading}
              sx={{
                height: 36,
                px: isTablet ? 1 : 1.5,
                borderRadius: 2,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
                bgcolor: alpha(theme.palette.background.default, 0.5),
                color: theme.palette.text.secondary,
                '&:hover': {
                  bgcolor: alpha(theme.palette.primary.main, 0.08),
                  borderColor: alpha(theme.palette.primary.main, 0.3),
                  color: theme.palette.primary.main,
                },
                transition: 'all 0.2s ease',
              }}
            >
              {!isTablet && (templatesLoading ? 'Loading...' : 'Template')}
            </Button>
          </Tooltip>
        )}

        {/* Share Button - Icon only on mobile */}
        {editingAgent && (
          <Tooltip title="Share Agent">
            <Button
              variant="outlined"
              size="small"
              startIcon={<Icon icon={shareIcon} width={16} height={16} />}
              onClick={() => setShareAgentDialogOpen(true)}
              disabled={saving || templatesLoading}
              sx={{
                height: 36,
                px: isMobile ? 1 : 1.5,
                borderRadius: 2,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
                bgcolor: alpha(theme.palette.background.default, 0.5),
                color: theme.palette.text.secondary,
                '&:hover': {
                  bgcolor: alpha(theme.palette.info.main, 0.08),
                  borderColor: alpha(theme.palette.info.main, 0.3),
                  color: theme.palette.info.main,
                },
                transition: 'all 0.2s ease',
              }}
            >
              {!isMobile && 'Share'}
            </Button>
          </Tooltip>
        )}

        {/* Main Action Button Group */}
        <ButtonGroup
          variant="contained"
          size="small"
          sx={{
            '& .MuiButtonGroup-grouped': {
              borderRadius: 2,
              '&:not(:last-of-type)': {
                borderRight: 'none',
                borderTopRightRadius: 0,
                borderBottomRightRadius: 0,
              },
              '&:not(:first-of-type)': {
                borderTopLeftRadius: 0,
                borderBottomLeftRadius: 0,
              },
            },
          }}
        >
          {/* Cancel/Close Button */}
          {editingAgent && (
            <Button
              onClick={onClose}
              disabled={saving}
              startIcon={<Icon icon={closeIcon} width={14} height={14} />}
              sx={{
                height: 36,
                px: isMobile ? 1 : 1.5,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                bgcolor: alpha(theme.palette.grey[500], 0.1),
                color: theme.palette.text.secondary,
                borderColor: alpha(theme.palette.divider, 0.2),
                '&:hover': {
                  bgcolor: alpha(theme.palette.error.main, 0.1),
                  color: theme.palette.error.main,
                },
              }}
            >
              {!isMobile && 'Cancel'}
            </Button>
          )}

          {/* Save/Update Button */}
          <Button
            startIcon={
              saving ? (
                <CircularProgress size={14} color="inherit" />
              ) : (
                <Icon icon={saveIcon} width={16} height={16} />
              )
            }
            onClick={onSave}
            disabled={saving || !agentName}
            sx={{
              height: 36,
              px: isMobile ? 1.5 : 2,
              fontSize: '0.8125rem',
              fontWeight: 600,
              textTransform: 'none',
              bgcolor: editingAgent ? theme.palette.warning.main : theme.palette.primary.main,
              color: 'white',
              boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
              '&:hover': {
                bgcolor: editingAgent ? theme.palette.warning.dark : theme.palette.primary.dark,
                boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
              },
              '&:disabled': {
                bgcolor: alpha(theme.palette.action.disabled, 0.12),
                color: theme.palette.action.disabled,
              },
              transition: 'all 0.2s ease',
            }}
          >
            {isMobile
              ? saving
                ? '...'
                : editingAgent
                  ? 'Update'
                  : 'Save'
              : saving
                ? editingAgent
                  ? 'Updating...'
                  : 'Saving...'
                : editingAgent
                  ? 'Update Agent'
                  : 'Save Agent'}
          </Button>
        </ButtonGroup>
      </Stack>

      {/* Permissions Dialog */}
      <AgentPermissionsDialog
        open={shareAgentDialogOpen}
        onClose={() => setShareAgentDialogOpen(false)}
        agentId={agentId || ''}
        agentName={agentName}
      />

      {/* Snackbar */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{
          vertical: 'top',
          horizontal: isMobile ? 'center' : 'right',
        }}
      >
        <Alert
          severity={snackbar.severity}
          onClose={handleCloseSnackbar}
          sx={{
            borderRadius: 2,
            boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default AgentBuilderHeader;
