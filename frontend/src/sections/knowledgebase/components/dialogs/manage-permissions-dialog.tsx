// components/dialogs/ManagePermissionsDialog.tsx
import type { User} from 'src/context/UserContext';

import { Icon } from '@iconify/react';
import addIcon from '@iconify-icons/mdi/plus';
import closeIcon from '@iconify-icons/mdi/close';
import React, { useState, useEffect } from 'react';
import peopleIcon from '@iconify-icons/eva/people-fill';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import searchIcon from '@iconify-icons/eva/search-outline';

import {
  Box,
  Chip,
  Fade,
  Table,
  Paper,
  Stack,
  Alert,
  alpha,
  Dialog,
  Button,
  Select,
  Avatar,
  Tooltip,
  Divider,
  useTheme,
  TableRow,
  MenuItem,
  Collapse,
  TableBody,
  TableCell,
  TableHead,
  TextField,
  Typography,
  IconButton,
  DialogTitle,
  FormControl,
  Autocomplete,
  DialogContent,
  DialogActions,
  TableContainer,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import { useUsers } from 'src/context/UserContext';

import type { KBPermission, CreatePermissionRequest, UpdatePermissionRequest } from '../../types/kb';

interface ManagePermissionsDialogProps {
  open: boolean;
  onClose: () => void;
  kbId: string;
  kbName: string;
  permissions: KBPermission[];
  onCreatePermissions: (data: CreatePermissionRequest) => Promise<void>;
  onUpdatePermission: (userId: string, data: UpdatePermissionRequest) => Promise<void>;
  onRemovePermission: (userId: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  loading?: boolean;
}

const ROLE_OPTIONS = [
  { value: 'OWNER', label: 'Owner', description: 'Full control and ownership' },
  // { value: 'ORGANIZER', label: 'Organizer', description: 'Manage content structure' },
  // { value: 'FILE_ORGANIZER', label: 'File Organizer', description: 'Organize files and folders' },
  { value: 'WRITER', label: 'Writer', description: 'Create and edit content' },
  { value: 'COMMENTER', label: 'Commenter', description: 'Add comments only' },
  { value: 'READER', label: 'Reader', description: 'View content only' },
];

const getRoleColor = (role: string) => {
  switch (role) {
    case 'OWNER': return 'error';
    case 'ORGANIZER': return 'warning';
    case 'FILE_ORGANIZER': return 'info';
    case 'WRITER': return 'success';
    case 'COMMENTER': return 'secondary';
    case 'READER': return 'default';
    default: return 'default';
  }
};

// Get initials from full name
const getInitials = (fullName: string) =>
  fullName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase();

export const ManagePermissionsDialog: React.FC<ManagePermissionsDialogProps> = ({
  open,
  onClose,
  kbId,
  kbName,
  permissions,
  onCreatePermissions,
  onUpdatePermission,
  onRemovePermission,
  onRefresh,
  loading = false,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const users = useUsers();
  
  const [showAddForm, setShowAddForm] = useState(false);
  const [selectedUsers, setSelectedUsers] = useState<User[]>([]);
  const [newRole, setNewRole] = useState<string>('READER');
  const [editingUser, setEditingUser] = useState<string | null>(null);
  const [editRole, setEditRole] = useState<string>('');
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setShowAddForm(false);
      setSelectedUsers([]);
      setNewRole('READER');
      setEditingUser(null);
      setError(null);
      setPermissionsPage(0);
    }
  }, [open]);

  // Add pagination state for permissions list
  const [permissionsPage, setPermissionsPage] = useState(0);
  const [permissionsPerPage] = useState(10);

  // Filter available users
  const availableUsers = users?.filter(user => 
    !permissions.some(permission => permission.userId === user._id)
  ) || [];

  const handleAddPermissions = async () => {
    if (selectedUsers.length === 0) {
      setError('Please select at least one user');
      return;
    }

    setActionLoading(true);
    setError(null);

    try {
      const userIds = selectedUsers.map(user => user._id).filter(_id => _id);

      if (userIds.length === 0) {
        setError('Selected users must have valid IDs');
        return;
      }

      await onCreatePermissions({
        users: userIds,
        role: newRole as any,
      });

      setShowAddForm(false);
      setSelectedUsers([]);
      setNewRole('READER');
      await onRefresh();
    } catch (err: any) {
      setError(err.message || 'Failed to add permissions');
    } finally {
      setActionLoading(false);
    }
  };

  const handleUpdatePermission = async (userId: string, role: string) => {
    setActionLoading(true);
    setError(null);

    try {
      await onUpdatePermission(userId, { role: role as any });
      setEditingUser(null);
      await onRefresh();
    } catch (err: any) {
      setError(err.message || 'Failed to update permission');
    } finally {
      setActionLoading(false);
    }
  };

  const handleRemovePermission = async (userId: string) => {
    if (!window.confirm('Are you sure you want to remove this user\'s access?')) {
      return;
    }

    setActionLoading(true);
    setError(null);

    try {
      await onRemovePermission(userId);
      await onRefresh();
    } catch (err: any) {
      setError(err.message || 'Failed to remove permission');
    } finally {
      setActionLoading(false);
    }
  };

  const startEdit = (userId: string, currentRole: string) => {
    setEditingUser(userId);
    setEditRole(currentRole);
  };

  const cancelEdit = () => {
    setEditingUser(null);
    setEditRole('');
  };

  // Get avatar color based on name
  const getAvatarColor = (name: string) => {
    const colors = [
      theme.palette.primary.main,
      theme.palette.info.main,
      theme.palette.success.main,
      theme.palette.warning.main,
      theme.palette.error.main,
    ];
    const hash = name.split('').reduce((acc, char) => char.charCodeAt(0) + (acc * 32 - acc), 0);
    return colors[Math.abs(hash) % colors.length];
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      TransitionComponent={Fade}
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(4px)',
          backgroundColor: alpha(theme.palette.common.black, isDark ? 0.6 : 0.4),
        },
      }}
      PaperProps={{
        elevation: isDark ? 6 : 2,
        sx: {
          borderRadius: 1,
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
          borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Box
            sx={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              bgcolor: alpha(theme.palette.primary.main, 0.1),
            }}
          >
            <Icon
              icon={peopleIcon}
              width={18}
              height={18}
              style={{ color: theme.palette.primary.main }}
            />
          </Box>
          <Box>
            <Typography
              variant="h6"
              sx={{
                fontWeight: 600,
                fontSize: '1.125rem',
                color: theme.palette.text.primary,
              }}
            >
              Manage Access
            </Typography>
            <Typography
              variant="body2"
              sx={{
                color: theme.palette.text.secondary,
                fontSize: '0.875rem',
              }}
            >
              {kbName}
            </Typography>
          </Box>
        </Stack>

        <IconButton
          onClick={onClose}
          disabled={loading || actionLoading}
          size="small"
          sx={{
            color: theme.palette.text.secondary,
            '&:hover': {
              color: theme.palette.text.primary,
              bgcolor: alpha(theme.palette.action.hover, 0.1),
            },
          }}
        >
          <Icon icon={closeIcon} width={18} height={18} />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ px: 3, py: 0 }}>
        {error && (
          <Alert 
            severity="error" 
            sx={{ 
              mb: 2,
              borderRadius: 1,
            }} 
            onClose={() => setError(null)}
          >
            {error}
          </Alert>
        )}

        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress />
          </Box>
        ) : (
          <>
            {/* Add new permissions section */}
            <Box sx={{ py: 2 }}>
              {!showAddForm && (
                <Stack direction="row" alignItems="center" justifyContent="flex-end" sx={{ mb: 2 }}>
                  <Button
                    variant="contained"
                    size="small"
                    startIcon={<Icon icon={addIcon} width={16} height={16} />}
                    onClick={() => setShowAddForm(true)}
                    disabled={actionLoading || availableUsers.length === 0}
                    sx={{ 
                      textTransform: 'none',
                      fontWeight: 500,
                      fontSize: '0.875rem',
                      px: 2.5,
                      py: 0.75,
                      borderRadius: 1,
                    }}
                  >
                    Add People
                  </Button>
                </Stack>
              )}

              {availableUsers.length === 0 && !showAddForm && (
                <Alert 
                  severity="info" 
                  sx={{ 
                    mb: 2,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.info.main, 0.05),
                    border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                  }}
                >
                  All available users already have access to this knowledge base.
                </Alert>
              )}

              <Collapse in={showAddForm}>
                <Paper 
                  variant="outlined" 
                  sx={{ 
                    p: 3, 
                    mb: 2, 
                    borderRadius: 1,
                    bgcolor: isDark 
                      ? alpha(theme.palette.background.paper, 0.6)
                      : alpha(theme.palette.background.default, 0.5),
                    border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
                  }}
                >
                  <Stack spacing={3}>
                    <Stack direction="row" alignItems="center" justifyContent="space-between">
                      <Typography variant="subtitle1" sx={{ fontWeight: 600, color: theme.palette.text.primary }}>
                        Add Team Members
                      </Typography>
                      <IconButton
                        onClick={() => {
                          setShowAddForm(false);
                          setSelectedUsers([]);
                        }}
                        size="small"
                        disabled={actionLoading}
                        sx={{
                          color: theme.palette.text.secondary,
                          '&:hover': {
                            bgcolor: alpha(theme.palette.action.hover, 0.1),
                          },
                        }}
                      >
                        <Icon icon={closeIcon} width={16} height={16} />
                      </IconButton>
                    </Stack>

                    <Stack spacing={2}>
                      <Box>
                        <Typography variant="body2" sx={{ mb: 1, fontWeight: 500, color: theme.palette.text.primary }}>
                          Select Users
                        </Typography>
                        <Autocomplete
                          multiple
                          options={availableUsers}
                          getOptionLabel={(option) => option.fullName || option.email || 'Unknown User'}
                          renderInput={(params) => (
                            <TextField
                              {...params}
                              placeholder="Search and select users..."
                              size="small"
                              InputProps={{
                                ...params.InputProps,
                                startAdornment: (
                                  <>
                                    <InputAdornment position="start">
                                      <Icon
                                        icon={searchIcon}
                                        width={18}
                                        height={18}
                                        style={{ color: theme.palette.text.secondary }}
                                      />
                                    </InputAdornment>
                                    {params.InputProps.startAdornment}
                                  </>
                                ),
                              }}
                              sx={{
                                '& .MuiOutlinedInput-root': {
                                  bgcolor: isDark 
                                    ? alpha(theme.palette.background.paper, 0.8)
                                    : theme.palette.background.paper,
                                },
                              }}
                            />
                          )}
                          onChange={(event, newValue) => setSelectedUsers(newValue)}
                          value={selectedUsers}
                          renderTags={(value, getTagProps) =>
                            value.map((option, index) => (
                              <Chip
                                {...getTagProps({ index })}
                                label={option.fullName || option.email || 'Unknown User'}
                                size="small"
                                avatar={
                                  <Avatar
                                    sx={{
                                      width: 20,
                                      height: 20,
                                      fontSize: '0.625rem',
                                      bgcolor: getAvatarColor(option.fullName || option.email || 'U'),
                                    }}
                                  >
                                    {getInitials(option.fullName || option.email || 'U')}
                                  </Avatar>
                                }
                                sx={{ 
                                  height: 28,
                                  borderRadius: 1,
                                  '& .MuiChip-deleteIcon': {
                                    width: 16,
                                    height: 16,
                                  },
                                }}
                              />
                            ))
                          }
                          renderOption={(props, option) => (
                            <li {...props} key={option._id}>
                              <Stack direction="row" alignItems="center" spacing={2} sx={{ py: 1, width: '100%' }}>
                                <Avatar
                                  sx={{
                                    width: 32,
                                    height: 32,
                                    fontSize: '0.75rem',
                                    bgcolor: getAvatarColor(option.fullName || option.email || 'U'),
                                  }}
                                >
                                  {getInitials(option.fullName || option.email || 'U')}
                                </Avatar>
                                <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                                  <Typography
                                    variant="body2"
                                    sx={{
                                      fontWeight: 500,
                                      overflow: 'hidden',
                                      textOverflow: 'ellipsis',
                                      whiteSpace: 'nowrap',
                                    }}
                                  >
                                    {option.fullName || 'Unnamed User'}
                                  </Typography>
                                  <Typography variant="caption" color="text.secondary">
                                    {option.email || 'No email'}
                                  </Typography>
                                </Box>
                              </Stack>
                            </li>
                          )}
                          ListboxProps={{
                            style: { maxHeight: 240 }
                          }}
                        />
                      </Box>

                      <Stack direction="row" spacing={2} alignItems="flex-end" justifyContent="space-between">
                        <FormControl size="small" sx={{ minWidth: 280 }}>
                          <Typography variant="body2" sx={{ mb: 1, fontWeight: 500, color: theme.palette.text.primary }}>
                            Permission Role
                          </Typography>
                          <Select
                            value={newRole}
                            onChange={(e) => setNewRole(e.target.value)}
                            sx={{
                              bgcolor: isDark 
                                ? alpha(theme.palette.background.paper, 0.8)
                                : theme.palette.background.paper,
                            }}
                          >
                            {ROLE_OPTIONS.map((option) => (
                              <MenuItem key={option.value} value={option.value}>
                                <Box>
                                  <Typography variant="body2" fontWeight={500}>
                                    {option.label}
                                  </Typography>
                                  <Typography variant="caption" color="text.secondary">
                                    {option.description}
                                  </Typography>
                                </Box>
                              </MenuItem>
                            ))}
                          </Select>
                        </FormControl>
                        
                        <Stack direction="row" spacing={1}>
                          <Button
                            variant="outlined"
                            onClick={() => {
                              setShowAddForm(false);
                              setSelectedUsers([]);
                            }}
                            disabled={actionLoading}
                            sx={{
                              textTransform: 'none',
                              fontWeight: 500,
                              px: 2,
                            }}
                          >
                            Cancel
                          </Button>
                          <Button
                            variant="contained"
                            onClick={handleAddPermissions}
                            disabled={actionLoading || selectedUsers.length === 0}
                            startIcon={actionLoading ? <CircularProgress size={16} color="inherit" /> : <Icon icon={addIcon} width={16} height={16} />}
                            sx={{
                              textTransform: 'none',
                              fontWeight: 500,
                              px: 3,
                            }}
                          >
                            {actionLoading ? 'Adding...' : `Add ${selectedUsers.length > 0 ? selectedUsers.length : ''} User${selectedUsers.length !== 1 ? 's' : ''}`}
                          </Button>
                        </Stack>
                      </Stack>
                    </Stack>
                  </Stack>
                </Paper>
              </Collapse>
            </Box>

            <Divider />

            {/* Current Permissions List */}
            <Box sx={{ py: 2 }}>
              <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
                <Typography 
                  variant="h6" 
                  sx={{ 
                    fontWeight: 600,
                    fontSize: '1rem',
                  }}
                >
                  Team Members ({permissions.length})
                </Typography>
                <Button
                  variant="text"
                  onClick={onRefresh}
                  disabled={loading || actionLoading}
                  startIcon={<Icon icon="eva:refresh-fill" width={16} height={16} />}
                  sx={{
                    textTransform: 'none',
                    fontWeight: 500,
                    fontSize: '0.8125rem',
                    color: theme.palette.text.secondary,
                    minWidth: 'auto',
                    px: 1,
                    '&:hover': {
                      color: theme.palette.primary.main,
                      bgcolor: alpha(theme.palette.primary.main, 0.08),
                    },
                  }}
                >
                  Refresh
                </Button>
              </Stack>

              {permissions.length === 0 ? (
                <Paper
                  variant="outlined"
                  sx={{
                    p: 4,
                    textAlign: 'center',
                    borderStyle: 'dashed',
                    borderColor: alpha(theme.palette.divider, 0.5),
                    bgcolor: alpha(theme.palette.action.hover, 0.02),
                  }}
                >
                  <Icon
                    icon={peopleIcon}
                    width={40}
                    height={40}
                    style={{ 
                      color: alpha(theme.palette.text.secondary, 0.4),
                      marginBottom: 12 
                    }}
                  />
                  <Typography variant="body1" sx={{ mb: 1, fontWeight: 500 }}>
                    No team members yet
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Add people to start collaborating
                  </Typography>
                  <Button
                    variant="outlined"
                    onClick={() => setShowAddForm(true)}
                    disabled={availableUsers.length === 0}
                    size="small"
                    sx={{ textTransform: 'none' }}
                  >
                    Add Your First Member
                  </Button>
                </Paper>
              ) : (
                <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
                  <TableContainer sx={{ maxHeight: 400 }}>
                    <Table stickyHeader size="small">
                      <TableHead>
                        <TableRow
                          sx={{
                            '& th': {
                              bgcolor: alpha(theme.palette.grey[50], isDark ? 0.1 : 0.8),
                              borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                              fontWeight: 600,
                              py: 1,
                              fontSize: '0.75rem',
                              color: theme.palette.text.secondary,
                            },
                          }}
                        >
                          <TableCell>User</TableCell>
                          <TableCell>Role</TableCell>
                          <TableCell>Added</TableCell>
                          <TableCell align="right">Actions</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {permissions
                          .slice(permissionsPage * permissionsPerPage, permissionsPage * permissionsPerPage + permissionsPerPage)
                          .map((permission) => (
                          <TableRow 
                            key={permission.userId}
                            sx={{
                              '&:hover': {
                                bgcolor: alpha(theme.palette.primary.main, 0.04),
                              },
                            }}
                          >
                            <TableCell sx={{ py: 1.5 }}>
                              <Stack direction="row" alignItems="center" spacing={1.5}>
                                <Avatar
                                  sx={{
                                    bgcolor: getAvatarColor(permission.userName || permission.userEmail),
                                    width: 32,
                                    height: 32,
                                    fontSize: '0.75rem',
                                    fontWeight: 600,
                                  }}
                                >
                                  {getInitials(permission.userName || permission.userEmail)}
                                </Avatar>
                                <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                                  <Typography 
                                    variant="body2" 
                                    sx={{ 
                                      fontWeight: 500,
                                      overflow: 'hidden',
                                      textOverflow: 'ellipsis',
                                      whiteSpace: 'nowrap',
                                    }}
                                  >
                                    {permission.userName || permission.userEmail}
                                  </Typography>
                                  {permission.userName && (
                                    <Typography
                                      variant="caption"
                                      color="text.secondary"
                                      sx={{ 
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis',
                                        whiteSpace: 'nowrap',
                                      }}
                                    >
                                      {permission.userEmail}
                                    </Typography>
                                  )}
                                </Box>
                              </Stack>
                            </TableCell>
                            
                            <TableCell sx={{ py: 1.5 }}>
                              {editingUser === permission.userId ? (
                                <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 240 }}>
                                  <FormControl size="small" sx={{ minWidth: 120 }}>
                                    <Select
                                      value={editRole}
                                      onChange={(e) => setEditRole(e.target.value)}
                                      disabled={actionLoading}
                                      sx={{
                                        '& .MuiSelect-select': {
                                          py: 0.75,
                                        },
                                      }}
                                    >
                                      {ROLE_OPTIONS.map((option) => (
                                        <MenuItem key={option.value} value={option.value}>
                                          <Box>
                                            <Typography variant="body2" fontWeight={500}>
                                              {option.label}
                                            </Typography>
                                            <Typography variant="caption" color="text.secondary">
                                              {option.description}
                                            </Typography>
                                          </Box>
                                        </MenuItem>
                                      ))}
                                    </Select>
                                  </FormControl>
                                  <Button
                                    size="small"
                                    variant="contained"
                                    onClick={() => handleUpdatePermission(permission.userId, editRole)}
                                    disabled={actionLoading || editRole === permission.role}
                                    sx={{ 
                                      textTransform: 'none',
                                      minWidth: 55,
                                      px: 1.5,
                                      py: 0.5,
                                      fontSize: '0.75rem',
                                    }}
                                  >
                                    Save
                                  </Button>
                                  <Button
                                    size="small"
                                    variant="outlined"
                                    onClick={cancelEdit}
                                    disabled={actionLoading}
                                    sx={{ 
                                      textTransform: 'none',
                                      minWidth: 55,
                                      px: 1.5,
                                      py: 0.5,
                                      fontSize: '0.75rem',
                                    }}
                                  >
                                    Cancel
                                  </Button>
                                </Stack>
                              ) : (
                                <Chip
                                  label={ROLE_OPTIONS.find(r => r.value === permission.role)?.label || permission.role}
                                  color={getRoleColor(permission.role) as any}
                                  variant="filled"
                                  size="small"
                                  sx={{
                                    height: 28,
                                    fontSize: '0.75rem',
                                    fontWeight: 600,
                                    borderRadius: 1,
                                    '&.MuiChip-colorDefault': {
                                      bgcolor: alpha(theme.palette.text.secondary, 0.1),
                                      color: theme.palette.text.secondary,
                                    },
                                    '&.MuiChip-colorPrimary': {
                                      bgcolor: alpha(theme.palette.primary.main, 0.1),
                                      color: theme.palette.primary.main,
                                    },
                                    '&.MuiChip-colorSecondary': {
                                      bgcolor: alpha(theme.palette.secondary.main, 0.1),
                                      color: theme.palette.secondary.main,
                                    },
                                    '&.MuiChip-colorError': {
                                      bgcolor: alpha(theme.palette.error.main, 0.1),
                                      color: theme.palette.error.main,
                                    },
                                    '&.MuiChip-colorWarning': {
                                      bgcolor: alpha(theme.palette.warning.main, 0.1),
                                      color: theme.palette.warning.main,
                                    },
                                    '&.MuiChip-colorInfo': {
                                      bgcolor: alpha(theme.palette.info.main, 0.1),
                                      color: theme.palette.info.main,
                                    },
                                    '&.MuiChip-colorSuccess': {
                                      bgcolor: alpha(theme.palette.success.main, 0.1),
                                      color: theme.palette.success.main,
                                    },
                                  }}
                                />
                              )}
                            </TableCell>
                            
                            <TableCell sx={{ py: 1.5 }}>
                              <Typography variant="caption" color="text.secondary">
                                {new Date(permission.createdAtTimestamp).toLocaleDateString('en-US', {
                                  month: 'short',
                                  day: 'numeric',
                                  year: 'numeric'
                                })}
                              </Typography>
                            </TableCell>
                            
                            <TableCell align="right" sx={{ py: 1.5 }}>
                              {editingUser === permission.userId ? null : (
                                <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                                  <Tooltip title="Edit permissions">
                                    <IconButton
                                      size="small"
                                      onClick={() => startEdit(permission.userId, permission.role)}
                                      disabled={actionLoading || permission.role === 'OWNER'}
                                      sx={{
                                        color: theme.palette.text.secondary,
                                        '&:hover': {
                                          bgcolor: alpha(theme.palette.primary.main, 0.08),
                                          color: theme.palette.primary.main,
                                        },
                                      }}
                                    >
                                      <Icon icon={editIcon} width={14} height={14} />
                                    </IconButton>
                                  </Tooltip>
                                  <Tooltip title="Remove access">
                                    <IconButton
                                      size="small"
                                      onClick={() => handleRemovePermission(permission.userId)}
                                      disabled={actionLoading || permission.role === 'OWNER'}
                                      sx={{
                                        color: theme.palette.text.secondary,
                                        '&:hover': {
                                          bgcolor: alpha(theme.palette.error.main, 0.08),
                                          color: theme.palette.error.main,
                                        },
                                      }}
                                    >
                                      <Icon icon={deleteIcon} width={14} height={14} />
                                    </IconButton>
                                  </Tooltip>
                                </Stack>
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>

                  {/* Simple Pagination */}
                  {permissions.length > permissionsPerPage && (
                    <Box sx={{ 
                      display: 'flex', 
                      justifyContent: 'center', 
                      p: 1,
                      borderTop: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                      bgcolor: alpha(theme.palette.background.default, 0.3),
                    }}>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <IconButton
                          size="small"
                          onClick={() => setPermissionsPage(Math.max(0, permissionsPage - 1))}
                          disabled={permissionsPage === 0}
                        >
                          <Icon icon="eva:chevron-left-fill" width={16} height={16} />
                        </IconButton>
                        
                        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 100, textAlign: 'center' }}>
                          {permissionsPage * permissionsPerPage + 1}-{Math.min((permissionsPage + 1) * permissionsPerPage, permissions.length)} of {permissions.length}
                        </Typography>
                        
                        <IconButton
                          size="small"
                          onClick={() => setPermissionsPage(permissionsPage + 1)}
                          disabled={(permissionsPage + 1) * permissionsPerPage >= permissions.length}
                        >
                          <Icon icon="eva:chevron-right-fill" width={16} height={16} />
                        </IconButton>
                      </Stack>
                    </Box>
                  )}
                </Paper>
              )}
            </Box>
          </>
        )}
      </DialogContent>

      <DialogActions 
        sx={{ 
          px: 3, 
          py: 2,
          borderTop: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          gap: 1,
        }}
      >
        <Button 
          onClick={onClose} 
          disabled={loading || actionLoading}
          variant="text"
          color="inherit"
          sx={{
            textTransform: 'none',
            fontWeight: 500,
            fontSize: '0.875rem',
          }}
        >
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};