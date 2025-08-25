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
} from '@mui/material';
import { Icon } from '@iconify/react';
import saveIcon from '@iconify-icons/mdi/content-save';
import homeIcon from '@iconify-icons/mdi/home';
import menuIcon from '@iconify-icons/mdi/menu';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import fileIcon from '@iconify-icons/mdi/file-document-outline';
import shareIcon from '@iconify-icons/mdi/share-outline';
import type { AgentBuilderHeaderProps } from '../../types/agent';
import ShareAgentDialog from './share-dialog';
import { useGroups } from '../../../../../context/GroupsContext';
import { useUsers } from '../../../../../context/UserContext';

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
}) => {
  const theme = useTheme();
  const [shareAgentDialogOpen, setShareAgentDialogOpen] = useState(false);
  const users = useUsers();
  const groups = useGroups();

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
        p: 2,
        borderBottom: `1px solid ${theme.palette.divider}`,
        backgroundColor: theme.palette.background.paper,
        display: 'flex',
        alignItems: 'center',
        gap: 2,
        flexShrink: 0,
        height: 64,
        boxSizing: 'border-box',
      }}
    >
      <Tooltip title={sidebarOpen ? 'Hide Sidebar' : 'Show Sidebar'}>
        <IconButton
          onClick={() => setSidebarOpen(!sidebarOpen)}
          sx={{
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: 1.5,
            '&:hover': {
              backgroundColor: alpha(theme.palette.text.primary, 0.04),
              borderColor: alpha(theme.palette.text.primary, 0.2),
            },
          }}
        >
          <Icon icon={menuIcon} width={20} height={20} />
        </IconButton>
      </Tooltip>

      <Breadcrumbs separator="›" sx={{ ml: 1 }}>
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
            '&:hover': { color: theme.palette.primary.main },
          }}
        >
          <Icon icon={homeIcon} width={16} height={16} />
          Agents
        </Link>
        <Typography
          color="text.primary"
          sx={{
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            gap: 0.5,
          }}
        >
          <Icon icon={sparklesIcon} width={16} height={16} />
          Flow Builder
        </Typography>
      </Breadcrumbs>

      <Box sx={{ flexGrow: 1 }} />

      {/* Agent Name Input */}
      <TextField
        label="Agent Name"
        value={agentName || 'New Agent'}
        onChange={(e) => setAgentName(e.target.value)}
        size="small"
        placeholder="Enter agent name..."
        sx={{
          minWidth: 300,
          '& .MuiOutlinedInput-root': {
            borderRadius: 1.5,
            backgroundColor: alpha(theme.palette.background.default, 0.5),
            '&:hover': {
              backgroundColor: alpha(theme.palette.background.default, 0.8),
            },
            '&.Mui-focused': {
              backgroundColor: theme.palette.background.default,
            },
          },
          '& .MuiInputLabel-root': {
            color: theme.palette.text.secondary,
          },
        }}
      />

      <Box sx={{ flexGrow: 1 }} />

      {/* Template Selector Button */}
      <Tooltip title="Use Template">
        <Button
          variant="outlined"
          startIcon={
            templatesLoading ? (
              <CircularProgress size={16} color="inherit" />
            ) : (
              <Icon icon={fileIcon} width={18} height={18} />
            )
          }
          onClick={() => setTemplateDialogOpen(true)}
          disabled={saving || templatesLoading}
          sx={{
            height: 32,
            px: 1.5,
            borderRadius: 1,
            fontSize: '0.8125rem',
            fontWeight: 500,
            textTransform: 'none',
            borderColor: 'grey.400',
            color: 'text.secondary',
            '&:hover': {
              backgroundColor: alpha(theme.palette.grey[500], 0.05),
              borderColor: 'grey.600',
            },
          }}
        >
          {templatesLoading ? 'Loading...' : 'Use Template'}
        </Button>
      </Tooltip>
      
      <Tooltip title="Share Agent">
        <Button
          variant="outlined"
          startIcon={
            <Icon icon={shareIcon} width={18} height={18} />
          }
          onClick={() => {
            setShareAgentDialogOpen(true);
          }}
          disabled={saving || templatesLoading}
          sx={{
            height: 32,
            px: 1.5,
            borderRadius: 1,
            fontSize: '0.8125rem',
            fontWeight: 500,
            textTransform: 'none',
            borderColor: 'grey.400',
            color: 'text.secondary',
            '&:hover': {
              backgroundColor: alpha(theme.palette.grey[500], 0.05),
              borderColor: 'grey.600',
            },
          }}
        >
          {templatesLoading ? 'Loading...' : 'Share Agent'}
        </Button>
      </Tooltip>
      <ShareAgentDialog
        open={shareAgentDialogOpen}
        onClose={() => setShareAgentDialogOpen(false)}
        onShare={() => {
          setSnackbar({
            open: true,
            message: 'Agent shared successfully',
            severity: 'success',
          });
          return Promise.resolve();
        }}
        agentName={agentName}
        users={users}
        groups={groups}
        sharing={Boolean(editingAgent)}
      />



      {/* Action Buttons */}
      <Stack direction="row" spacing={1}>
        {editingAgent && (
          <Button
            variant="outlined"
            onClick={onClose}
            disabled={saving}
            sx={{
              height: 32,
              px: 1.5,
              borderRadius: 1,
              fontSize: '0.8125rem',
              fontWeight: 500,
              textTransform: 'none',
              borderColor: 'grey.400',
              color: 'text.secondary',
              '&:hover': {
                backgroundColor: alpha(theme.palette.grey[500], 0.05),
                borderColor: 'grey.600',
              },
            }}
          >
            Cancel
          </Button>
        )}
        <Button
          variant={editingAgent ? "contained" : "outlined"}
          startIcon={
            saving ? (
              <CircularProgress size={16} color="inherit" />
            ) : (
              <Icon icon={saveIcon} width={18} height={18} />
            )
          }
          onClick={onSave}
          disabled={saving}
          sx={{
            height: 32,
            px: 1.5,
            borderRadius: 1,
            fontSize: '0.8125rem',
            fontWeight: 500,
            textTransform: 'none',
            ...(editingAgent ? {
              // Contained style for update
              backgroundColor: 'warning.main',
              color: 'white',
              '&:hover': {
                backgroundColor: 'warning.dark',
              },
            } : {
              // Outlined style for create
              borderColor: 'primary.main',
              color: 'primary.main',
              '&:hover': {
                backgroundColor: alpha(theme.palette.primary.main, 0.05),
                borderColor: 'primary.dark',
              },
            })
          }}
        >
          <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>
            {saving 
              ? (editingAgent ? 'Updating...' : 'Saving...') 
              : (editingAgent ? 'Update Agent' : 'Save Agent')
            }
          </Box>
        </Button>
      </Stack>
      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert severity={snackbar.severity} onClose={handleCloseSnackbar}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default AgentBuilderHeader;
