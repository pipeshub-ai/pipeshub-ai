import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  IconButton,
  useTheme,
  alpha,
  Switch,
  FormControlLabel,
  Chip,
  TextField,
  Autocomplete,
  Divider,
  Alert,
} from '@mui/material';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/eva/close-outline';
import shieldIcon from '@iconify-icons/mdi/shield-check-outline';
import userIcon from '@iconify-icons/mdi/account-outline';
import groupIcon from '@iconify-icons/mdi/account-group-outline';
import checkIcon from '@iconify-icons/mdi/check-circle';

interface User {
  _key: string;
  name: string;
  email: string;
  role?: string;
}

interface Group {
  _key: string;
  name: string;
  description?: string;
  memberCount?: number;
}

interface ApprovalsDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (approvalConfig: ApprovalConfig) => void;
  toolName: string;
  users: User[];
  groups: Group[];
  initialConfig?: ApprovalConfig;
}

export interface ApprovalConfig {
  requiresApproval: boolean;
  approvers: {
    users: string[];
    groups: string[];
  };
  approvalThreshold: 'single' | 'majority' | 'unanimous';
  autoApprove: boolean;
}

export default function ApprovalsDialog({
  open,
  onClose,
  onSave,
  toolName,
  users,
  groups,
  initialConfig,
}: ApprovalsDialogProps) {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [config, setConfig] = useState<ApprovalConfig>({
    requiresApproval: false,
    approvers: {
      users: [],
      groups: [],
    },
    approvalThreshold: 'single',
    autoApprove: false,
  });

  const [selectedUsers, setSelectedUsers] = useState<User[]>([]);
  const [selectedGroups, setSelectedGroups] = useState<Group[]>([]);

  useEffect(() => {
    if (initialConfig) {
      setConfig(initialConfig);
      // Set selected users and groups based on initial config
      const selectedUsersList = users.filter(user => 
        initialConfig.approvers.users.includes(user._key)
      );
      const selectedGroupsList = groups.filter(group => 
        initialConfig.approvers.groups.includes(group._key)
      );
      setSelectedUsers(selectedUsersList);
      setSelectedGroups(selectedGroupsList);
    }
  }, [initialConfig, users, groups]);

  const handleRequiresApprovalChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setConfig(prev => ({
      ...prev,
      requiresApproval: event.target.checked,
    }));
  };

  const handleAutoApproveChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setConfig(prev => ({
      ...prev,
      autoApprove: event.target.checked,
    }));
  };

  const handleThresholdChange = (threshold: 'single' | 'majority' | 'unanimous') => {
    setConfig(prev => ({
      ...prev,
      approvalThreshold: threshold,
    }));
  };

  const handleUsersChange = (event: any, newValue: User[]) => {
    setSelectedUsers(newValue);
    setConfig(prev => ({
      ...prev,
      approvers: {
        ...prev.approvers,
        users: newValue.map(user => user._key),
      },
    }));
  };

  const handleGroupsChange = (event: any, newValue: Group[]) => {
    setSelectedGroups(newValue);
    setConfig(prev => ({
      ...prev,
      approvers: {
        ...prev.approvers,
        groups: newValue.map(group => group._key),
      },
    }));
  };

  const handleSave = () => {
    onSave(config);
    onClose();
  };

  const thresholdOptions = [
    { value: 'single', label: 'Single Approval', description: 'Any one approver can approve' },
    { value: 'majority', label: 'Majority', description: 'More than 50% of approvers must approve' },
    { value: 'unanimous', label: 'Unanimous', description: 'All approvers must approve' },
  ];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(4px)',
          backgroundColor: alpha(theme.palette.common.black, isDark ? 0.6 : 0.4),
        },
      }}
      PaperProps={{
        elevation: isDark ? 6 : 2,
        sx: {
          borderRadius: 1.5,
          overflow: 'hidden',
          bgcolor: isDark
            ? alpha(theme.palette.background.paper, 0.9)
            : theme.palette.background.paper,
        },
      }}
    >
      <DialogTitle
        sx={{
          px: 3,
          py: 2,
          borderBottom: '1px solid',
          borderColor: alpha(theme.palette.divider, 0.08),
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              bgcolor: isDark
                ? alpha(theme.palette.primary.main, 0.15)
                : alpha(theme.palette.primary.main, 0.1),
            }}
          >
            <Icon
              icon={shieldIcon}
              width={18}
              height={18}
              style={{ color: theme.palette.primary.main }}
            />
          </Box>
          <Typography
            variant="h6"
            sx={{
              fontWeight: 500,
              fontSize: '1rem',
              color: theme.palette.text.primary,
            }}
          >
            Tool Approvals - {toolName}
          </Typography>
        </Box>

        <IconButton
          onClick={onClose}
          size="small"
          aria-label="close"
          sx={{
            color: theme.palette.text.secondary,
            width: 28,
            height: 28,
            '&:hover': {
              color: theme.palette.text.primary,
              bgcolor: alpha(theme.palette.action.hover, isDark ? 0.2 : 0.1),
            },
          }}
        >
          <Icon icon={closeIcon} width={18} height={18} />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ px: 3, py: 2.5 }}>
        <Box sx={{ mb: 3 }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Configure approval requirements for this tool. When enabled, actions performed by this tool will require approval from selected users or groups.
          </Typography>

          {/* Main Approval Toggle */}
          <Box sx={{ mb: 3 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={config.requiresApproval}
                  onChange={handleRequiresApprovalChange}
                  color="primary"
                />
              }
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Icon icon={shieldIcon} width={16} height={16} />
                  <Typography variant="body1" sx={{ fontWeight: 600 }}>
                    Require Approval for Tool Actions
                  </Typography>
                </Box>
              }
            />
          </Box>

          {config.requiresApproval && (
            <>
              {/* Auto-approve toggle */}
              <Box sx={{ mb: 3 }}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={config.autoApprove}
                      onChange={handleAutoApproveChange}
                      color="secondary"
                    />
                  }
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Icon icon={checkIcon} width={16} height={16} />
                      <Typography variant="body2">
                        Auto-approve after initial approval (for recurring actions)
                      </Typography>
                    </Box>
                  }
                />
              </Box>

              {/* Approval Threshold */}
              <Box sx={{ mb: 3 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, fontWeight: 500 }}>
                  Approval Threshold
                </Typography>
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {thresholdOptions.map((option) => (
                    <Box
                      key={option.value}
                      onClick={() => handleThresholdChange(option.value as any)}
                      sx={{
                        p: 1.5,
                        border: `1px solid ${
                          config.approvalThreshold === option.value
                            ? theme.palette.primary.main
                            : alpha(theme.palette.divider, 0.2)
                        }`,
                        borderRadius: 1,
                        cursor: 'pointer',
                        bgcolor: config.approvalThreshold === option.value
                          ? alpha(theme.palette.primary.main, 0.05)
                          : 'transparent',
                        '&:hover': {
                          bgcolor: alpha(theme.palette.primary.main, 0.05),
                          borderColor: theme.palette.primary.main,
                        },
                        transition: 'all 0.2s ease',
                      }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                        <Box
                          sx={{
                            width: 20,
                            height: 20,
                            borderRadius: '50%',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            bgcolor: config.approvalThreshold === option.value
                              ? theme.palette.primary.main
                              : alpha(theme.palette.text.secondary, 0.2),
                            color: config.approvalThreshold === option.value
                              ? 'white'
                              : theme.palette.text.secondary,
                          }}
                        >
                          <Icon icon={checkIcon} width={12} height={12} />
                        </Box>
                        <Box sx={{ flex: 1 }}>
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>
                            {option.label}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {option.description}
                          </Typography>
                        </Box>
                        {config.approvalThreshold === option.value && (
                          <Icon
                            icon={checkIcon}
                            width={16}
                            height={16}
                            style={{ color: theme.palette.primary.main }}
                          />
                        )}
                      </Box>
                    </Box>
                  ))}
                </Box>
              </Box>

              <Divider sx={{ my: 3 }} />

              {/* User Approvers */}
              <Box sx={{ mb: 3 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, fontWeight: 500 }}>
                  <Icon icon={userIcon} width={16} height={16} style={{ marginRight: 8 }} />
                  User Approvers
                </Typography>
                <Autocomplete
                  multiple
                  options={users}
                  getOptionLabel={(option) => `${option.name} (${option.email})`}
                  value={selectedUsers}
                  onChange={handleUsersChange}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      placeholder="Select users who can approve..."
                      variant="outlined"
                      size="small"
                    />
                  )}
                  renderTags={(value, getTagProps) =>
                    value.map((option, index) => (
                      <Chip
                        {...getTagProps({ index })}
                        key={option._key}
                        label={option.name}
                        size="small"
                        icon={<Icon icon={userIcon} width={14} height={14} />}
                      />
                    ))
                  }
                />
              </Box>

              {/* Group Approvers */}
              <Box sx={{ mb: 3 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, fontWeight: 500 }}>
                  <Icon icon={groupIcon} width={16} height={16} style={{ marginRight: 8 }} />
                  Group Approvers
                </Typography>
                <Autocomplete
                  multiple
                  options={groups}
                  getOptionLabel={(option) => `${option.name}${option.memberCount ? ` (${option.memberCount} members)` : ''}`}
                  value={selectedGroups}
                  onChange={handleGroupsChange}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      placeholder="Select groups who can approve..."
                      variant="outlined"
                      size="small"
                    />
                  )}
                  renderTags={(value, getTagProps) =>
                    value.map((option, index) => (
                      <Chip
                        {...getTagProps({ index })}
                        key={option._key}
                        label={option.name}
                        size="small"
                        icon={<Icon icon={groupIcon} width={14} height={14} />}
                      />
                    ))
                  }
                />
              </Box>

              {/* Summary */}
              <Alert severity="info" sx={{ mt: 2 }}>
                <Typography variant="body2">
                  <strong>Summary:</strong> This tool will require approval from{' '}
                  {config.approvers.users.length + config.approvers.groups.length} approver(s) using{' '}
                  <strong>{config.approvalThreshold}</strong> threshold.
                  {config.autoApprove && ' Auto-approval is enabled for recurring actions.'}
                </Typography>
              </Alert>
            </>
          )}
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          px: 3,
          py: 2,
          borderTop: '1px solid',
          borderColor: alpha(theme.palette.divider, 0.08),
          bgcolor: isDark
            ? alpha(theme.palette.background.default, 0.3)
            : alpha(theme.palette.background.default, 0.2),
          gap: 1.5,
        }}
      >
        <Button
          onClick={onClose}
          variant="text"
          color="inherit"
          sx={{
            borderRadius: 1,
            textTransform: 'none',
            fontWeight: 500,
            fontSize: '0.875rem',
            color: theme.palette.text.secondary,
            '&:hover': {
              backgroundColor: alpha(theme.palette.action.hover, isDark ? 0.1 : 0.05),
              color: theme.palette.text.primary,
            },
          }}
        >
          Cancel
        </Button>

        <Button
          onClick={handleSave}
          variant="contained"
          disableElevation
          startIcon={<Icon icon={shieldIcon} width={18} height={18} />}
          sx={{
            borderRadius: 1,
            textTransform: 'none',
            fontWeight: 500,
            fontSize: '0.875rem',
            boxShadow: 'none',
            px: 2,
            py: 0.75,
            '&:hover': {
              boxShadow: 'none',
              bgcolor: isDark
                ? alpha(theme.palette.primary.main, 0.8)
                : alpha(theme.palette.primary.main, 0.9),
            },
          }}
        >
          Save Approval Settings
        </Button>
      </DialogActions>
    </Dialog>
  );
}
