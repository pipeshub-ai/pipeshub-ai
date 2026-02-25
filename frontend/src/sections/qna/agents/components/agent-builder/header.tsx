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
  FormControlLabel,
  Switch,
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
  shareWithOrg,
  setShareWithOrg,
  hasToolsets,
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

  const isDark = theme.palette.mode === 'dark';

  return (
    <Box
      sx={{
        px: { xs: 2, sm: 3, md: 4 },
        py: 2,
        borderBottom: `1px solid ${theme.palette.divider}`,
        backgroundColor: theme.palette.background.paper,
        display: 'flex',
        alignItems: 'center',
        gap: { xs: 1.5, sm: 2, md: 2.5 },
        flexShrink: 0,
        minHeight: { xs: 64, sm: 72 },
        boxSizing: 'border-box',
        position: 'sticky',
        top: 0,
        zIndex: 1200,
        boxShadow: isDark 
          ? `0 2px 8px rgba(0, 0, 0, 0.2)`
          : `0 2px 8px rgba(0, 0, 0, 0.04)`,
      }}
    >
      {/* Sidebar Toggle */}
      <Tooltip title={sidebarOpen ? 'Hide Sidebar' : 'Show Sidebar'}>
        <IconButton
          onClick={() => setSidebarOpen(!sidebarOpen)}
          size={isMobile ? 'small' : 'medium'}
          sx={{
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: 1.5,
            backgroundColor: theme.palette.background.paper,
            color: theme.palette.text.secondary,
            width: { xs: 36, sm: 40 },
            height: { xs: 36, sm: 40 },
            transition: 'all 0.2s ease',
            '&:hover': {
              backgroundColor: theme.palette.action.hover,
              borderColor: theme.palette.text.secondary,
              color: theme.palette.text.primary,
              transform: 'scale(1.05)',
            },
          }}
        >
          <Icon icon={menuIcon} width={isMobile ? 18 : 20} height={isMobile ? 18 : 20} />
        </IconButton>
      </Tooltip>

      {/* Premium Breadcrumbs - Hidden on mobile */}
      {!isMobile && (
        <Breadcrumbs
          separator={
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                color: theme.palette.text.secondary,
                mx: 1,
                fontSize: '0.875rem',
              }}
            >
              ›
            </Box>
          }
          sx={{
            ml: 1,
            '& .MuiBreadcrumbs-ol': {
              alignItems: 'center',
            },
          }}
        >
          <Link
            underline="none"
            onClick={onClose}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.75,
              cursor: 'pointer',
              fontWeight: 500,
              fontSize: '0.875rem',
              color: theme.palette.text.secondary,
              transition: 'all 0.2s ease',
              px: 1,
              py: 0.5,
              borderRadius: 1,
              '&:hover': {
                color: theme.palette.text.primary,
                backgroundColor: theme.palette.action.hover,
              },
            }}
          >
            <Icon icon={homeIcon} width={16} height={16} />
            Agents
          </Link>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.75,
              color: theme.palette.text.primary,
              fontWeight: 600,
              fontSize: '0.875rem',
              px: 1,
              py: 0.5,
            }}
          >
            <Icon icon={sparklesIcon} width={16} height={16} />
            Flow Builder
          </Box>
        </Breadcrumbs>
      )}

      {/* Mobile Status Indicator */}
      {isMobile && (
        <Chip
          label={editingAgent ? 'Editing' : 'Creating'}
          size="small"
          sx={{
            height: 28,
            fontSize: '0.75rem',
            fontWeight: 600,
            backgroundColor: isDark 
              ? alpha(theme.palette.background.paper, 0.6)
              : alpha(theme.palette.background.default, 0.8),
            border: `1px solid ${theme.palette.divider}`,
            color: theme.palette.text.primary,
          }}
        />
      )}

      <Box sx={{ flexGrow: 1 }} />

      {/* Premium Agent Name Input */}
      <TextField
        label="Agent Name"
        value={agentName || ''}
        onChange={(e) => setAgentName(e.target.value)}
        size="small"
        placeholder={isMobile ? 'Agent name...' : 'Enter agent name...'}
        sx={{
          width: { xs: 160, sm: 220, md: 300, lg: 340 },
          '& .MuiOutlinedInput-root': {
            borderRadius: 1.5,
            backgroundColor: theme.palette.background.paper,
            border: `1px solid ${theme.palette.divider}`,
            transition: 'all 0.2s ease',
            '& fieldset': {
              borderColor: theme.palette.divider,
            },
            '&:hover': {
              backgroundColor: theme.palette.background.paper,
              '& fieldset': {
                borderColor: theme.palette.text.secondary,
              },
            },
            '&.Mui-focused': {
              backgroundColor: theme.palette.background.paper,
              '& fieldset': {
                borderColor: theme.palette.text.secondary,
                borderWidth: '1.5px',
              },
            },
          },
          '& .MuiInputLabel-root': {
            color: theme.palette.text.secondary,
            fontSize: '0.8125rem',
            fontWeight: 500,
          },
          '& .MuiInputBase-input': {
            fontSize: '0.875rem',
            fontWeight: 500,
            color: theme.palette.text.primary,
          },
        }}
      />

      <Box sx={{ flexGrow: 1 }} />

      {/* Share with Org Toggle */}
      {!isMobile && (
        <Tooltip
          title={
            hasToolsets
              ? 'Cannot share with organization: this agent has toolsets that require personal authentication'
              : shareWithOrg
              ? 'Click to stop sharing this agent with the entire organization'
              : 'Share this agent with all members of your organization (not allowed when toolsets are configured)'
          }
        >
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <FormControlLabel
              control={
                <Switch
                  checked={shareWithOrg}
                  onChange={(e) => {
                    if (hasToolsets && e.target.checked) {
                      setSnackbar({
                        open: true,
                        message:
                          'Cannot share with organization: remove all toolsets first. Toolsets require personal authentication.',
                        severity: 'warning',
                      });
                      return;
                    }
                    setShareWithOrg(e.target.checked);
                  }}
                  disabled={saving || hasToolsets}
                  size="small"
                  color="primary"
                />
              }
              label={
                <Typography
                  variant="caption"
                  sx={{
                    fontWeight: 600,
                    fontSize: '0.75rem',
                    color: hasToolsets
                      ? theme.palette.text.disabled
                      : shareWithOrg
                      ? theme.palette.primary.main
                      : theme.palette.text.secondary,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {isTablet ? 'Share Org' : 'Share with Org'}
                </Typography>
              }
              sx={{ mr: 0, ml: 0 }}
            />
          </Box>
        </Tooltip>
      )}

      {/* Action Buttons - Responsive layout */}
      <Stack direction="row" spacing={1} alignItems="center">
        {/* Premium Template Button */}
        {/* {!isMobile && (
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
                height: 38,
                px: isTablet ? 1.5 : 2,
                borderRadius: 1.5,
                fontSize: '0.8125rem',
                fontWeight: 600,
                textTransform: 'none',
                border: `1px solid ${theme.palette.divider}`,
                backgroundColor: theme.palette.background.paper,
                color: theme.palette.text.primary,
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: theme.palette.action.hover,
                  borderColor: theme.palette.text.secondary,
                  transform: 'translateY(-1px)',
                  boxShadow: isDark 
                    ? `0 2px 8px rgba(0, 0, 0, 0.2)`
                    : `0 2px 8px rgba(0, 0, 0, 0.08)`,
                },
                '&:disabled': {
                  backgroundColor: theme.palette.action.disabledBackground,
                  color: theme.palette.action.disabled,
                },
              }}
            >
              {!isTablet && (templatesLoading ? 'Loading...' : 'Template')}
            </Button>
          </Tooltip>
        )} */}

        {/* Premium Share Button */}
        {/* {editingAgent && (
          <Tooltip title="Share Agent">
            <Button
              variant="outlined"
              size="small"
              startIcon={<Icon icon={shareIcon} width={16} height={16} />}
              onClick={() => setShareAgentDialogOpen(true)}
              disabled={saving || templatesLoading}
              sx={{
                height: 38,
                px: isMobile ? 1.5 : 2,
                borderRadius: 1.5,
                fontSize: '0.8125rem',
                fontWeight: 600,
                textTransform: 'none',
                border: `1px solid ${theme.palette.divider}`,
                backgroundColor: theme.palette.background.paper,
                color: theme.palette.text.primary,
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: theme.palette.action.hover,
                  borderColor: theme.palette.text.secondary,
                  transform: 'translateY(-1px)',
                  boxShadow: isDark 
                    ? `0 2px 8px rgba(0, 0, 0, 0.2)`
                    : `0 2px 8px rgba(0, 0, 0, 0.08)`,
                },
                '&:disabled': {
                  backgroundColor: theme.palette.action.disabledBackground,
                  color: theme.palette.action.disabled,
                },
              }}
            >
              {!isMobile && 'Share'}
            </Button>
          </Tooltip>
        )} */}

        {/* Premium Action Buttons */}
        <Stack direction="row" spacing={1.5} alignItems="center">
          {/* Cancel/Close Button */}
          {editingAgent && (
            <Button
              onClick={onClose}
              disabled={saving}
              startIcon={<Icon icon={closeIcon} width={16} height={16} />}
              sx={{
                height: 38,
                px: isMobile ? 1.5 : 2,
                borderRadius: 1.5,
                fontSize: '0.8125rem',
                fontWeight: 600,
                textTransform: 'none',
                border: `1px solid ${theme.palette.divider}`,
                backgroundColor: theme.palette.background.paper,
                color: theme.palette.text.secondary,
                transition: 'all 0.2s ease',
                '&:hover': {
                  backgroundColor: theme.palette.action.hover,
                  borderColor: theme.palette.error.main,
                  color: theme.palette.error.main,
                  transform: 'translateY(-1px)',
                },
                '&:disabled': {
                  backgroundColor: theme.palette.action.disabledBackground,
                  color: theme.palette.action.disabled,
                },
              }}
            >
              {!isMobile && 'Cancel'}
            </Button>
          )}

          {/* Premium Save/Update Button */}
          <Button
            variant="contained"
            startIcon={
              saving ? (
                <CircularProgress size={14} color="inherit" />
              ) : (
                <Icon icon={saveIcon} width={16} height={16} />
              )
            }
            onClick={onSave}
            disabled={saving}
            sx={{
              height: 38,
              px: isMobile ? 2 : 2.5,
              borderRadius: 1.5,
              fontSize: '0.8125rem',
              fontWeight: 600,
              textTransform: 'none',
              backgroundColor: editingAgent ? theme.palette.warning.main : theme.palette.primary.main,
              color: 'white',
              boxShadow: isDark 
                ? `0 2px 8px ${alpha(editingAgent ? theme.palette.warning.main : theme.palette.primary.main, 0.3)}`
                : `0 2px 8px ${alpha(editingAgent ? theme.palette.warning.main : theme.palette.primary.main, 0.2)}`,
              transition: 'all 0.2s ease',
              '&:hover': {
                backgroundColor: editingAgent ? theme.palette.warning.dark : theme.palette.primary.dark,
                transform: 'translateY(-1px)',
                boxShadow: isDark 
                  ? `0 4px 12px ${alpha(editingAgent ? theme.palette.warning.main : theme.palette.primary.main, 0.4)}`
                  : `0 4px 12px ${alpha(editingAgent ? theme.palette.warning.main : theme.palette.primary.main, 0.3)}`,
              },
              '&:disabled': {
                backgroundColor: theme.palette.action.disabledBackground,
                color: theme.palette.action.disabled,
                boxShadow: 'none',
              },
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
        </Stack>
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
