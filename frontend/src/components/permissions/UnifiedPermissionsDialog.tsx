import React, { useEffect, useMemo, useState } from 'react';
import { Icon } from '@iconify/react';
import addIcon from '@iconify-icons/mdi/plus';
import closeIcon from '@iconify-icons/mdi/close';
import peopleIcon from '@iconify-icons/eva/people-fill';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import searchIcon from '@iconify-icons/eva/search-outline';
import teamIcon from '@iconify-icons/mdi/account-group';
import warningIcon from '@iconify-icons/eva/alert-triangle-outline';
import CreateTeamDialog from 'src/sections/knowledgebase/components/dialogs/create-team-dialog';

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
  DialogContentText,
} from '@mui/material';

// Shared, normalized types used by this dialog
export type UnifiedRole =
  | 'OWNER'
  | 'WRITER'
  | 'READER'
  | 'COMMENTER'
  | 'ORGANIZER'
  | 'FILE_ORGANIZER';

export interface User {
  id: string;
  userId: string;
  name?: string;
  email?: string;
  isActive?: boolean;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
}

export interface Team {
  id: string; // team key/id (normalized)
  name: string;
  description?: string;
  createdBy?: string;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
  members?: {
    id: string;
    userId: string;
    userName: string;
    userEmail: string;
    role: UnifiedRole;
    isOwner: boolean;
  }[];
  memberCount?: number;
  canEdit?: boolean;
  canDelete?: boolean;
  canManageMembers?: boolean;
  currentUserPermission?: UnifiedPermission;
}

export interface UnifiedPermission {
  id: string;
  userId?: string; // Only for users
  type: 'USER' | 'TEAM';
  name: string;
  email?: string;
  role?: UnifiedRole; // Optional - teams don't have roles
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
}

// API contract via callbacks so agents/KB can plug in their own endpoints
export interface UnifiedPermissionsApi {
  // Loads all permissions for the subject (agent/knowledge base)
  loadPermissions: () => Promise<UnifiedPermission[]>;
  // Loads selectable users
  loadUsers: () => Promise<User[]>;
  // Loads selectable teams
  loadTeams: () => Promise<Team[]>;
  // Create a new team and optionally add members with a default role
  createTeam: (data: {
    name: string;
    description?: string;
    userIds?: string[];
    role?: UnifiedRole;
    memberRoles?: Array<{ userId: string; role: UnifiedRole }>;
  }) => Promise<Team>;
  // Grant permissions to users and/or teams
  // Role is required for users, but optional for teams (teams don't have roles)
  createPermissions: (data: {
    userIds: string[];
    teamIds: string[];
    role?: UnifiedRole; // Optional - required if userIds provided
  }) => Promise<void>;
  // Update role for a specific user or team (pass exactly one of userIds/teamIds with a single id)
  updatePermissions: (data: {
    userIds?: string[];
    teamIds?: string[];
    role: UnifiedRole;
  }) => Promise<void>;
  // Remove permissions for specific principals
  removePermissions: (data: { userIds?: string[]; teamIds?: string[] }) => Promise<void>;
}

interface UnifiedPermissionsDialogProps {
  open: boolean;
  onClose: () => void;
  subjectName: string; // e.g., Agent name / Knowledge Base name (display only)
  api: UnifiedPermissionsApi;
  // Optional feature flags and labels
  title?: string; // default: Manage Access
  addPeopleLabel?: string; // default: Add People
}

const ROLE_OPTIONS: { value: UnifiedRole; label: string; description: string }[] = [
  { value: 'OWNER', label: 'Owner', description: 'Full control and ownership' },
  { value: 'WRITER', label: 'Writer', description: 'Create and edit content' },
  { value: 'COMMENTER', label: 'Commenter', description: 'Add comments only' },
  { value: 'READER', label: 'Reader', description: 'View only and use' },
];

const getRoleColor = (role: UnifiedRole) => {
  switch (role) {
    case 'OWNER':
      return 'error';
    case 'ORGANIZER':
      return 'warning';
    case 'FILE_ORGANIZER':
      return 'info';
    case 'WRITER':
      return 'success';
    case 'COMMENTER':
      return 'secondary';
    case 'READER':
      return 'default';
    default:
      return 'default';
  }
};

const getInitials = (fullName: string) =>
  fullName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase();

const UnifiedPermissionsDialog: React.FC<UnifiedPermissionsDialogProps> = ({
  open,
  onClose,
  subjectName,
  api,
  title = 'Manage Access',
  addPeopleLabel = 'Add People',
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // Data
  const [permissions, setPermissions] = useState<UnifiedPermission[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);

  // Loading states
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  // Errors
  const [error, setError] = useState<string | null>(null);

  // UI states (Add People section like KB)
  const [showAddForm, setShowAddForm] = useState(false);
  const [selectedUsers, setSelectedUsers] = useState<User[]>([]);
  const [selectedTeams, setSelectedTeams] = useState<Team[]>([]);
  const [newRole, setNewRole] = useState<UnifiedRole>('READER');

  // Edit inline
  const [editingEntity, setEditingEntity] = useState<{ type: 'USER' | 'TEAM'; id: string } | null>(
    null
  );
  const [editRole, setEditRole] = useState<UnifiedRole>('READER');

  // Delete dialog
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [toDelete, setToDelete] = useState<UnifiedPermission | null>(null);

  // Create Team dialog
  const [teamDialogOpen, setTeamDialogOpen] = useState(false);

  // Pagination
  const [permissionsPage, setPermissionsPage] = useState(0);
  const [permissionsPerPage] = useState(10);

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

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, u, t] = await Promise.all([
        api.loadPermissions(),
        api.loadUsers(),
        api.loadTeams(),
      ]);
      setPermissions(p || []);
      setUsers(u || []);
      setTeams(t || []);
    } catch (e: any) {
      setError(e?.message || 'Failed to load permissions');
      setPermissions([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      setShowAddForm(false);
      setSelectedUsers([]);
      setSelectedTeams([]);
      setNewRole('READER');
      setEditingEntity(null);
      setError(null);
      setPermissionsPage(0);
      loadAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const availableUsers = useMemo(() => {
    const withAccess = new Set(permissions.filter((p) => p.type === 'USER').map((p) => p.id));
    return users.filter((u) => !withAccess.has(u.id));
  }, [users, permissions]);

  const availableTeams = useMemo(() => {
    const withAccess = new Set(permissions.filter((p) => p.type === 'TEAM').map((p) => p.id));
    return teams.filter((t) => !withAccess.has(t.id));
  }, [teams, permissions]);

  const handleAddPermissions = async () => {
    if (selectedUsers.length === 0 && selectedTeams.length === 0) {
      setError('Please select at least one user or team');
      return;
    }
    
    const userIds = selectedUsers.map((u) => u.id).filter(Boolean);
    const teamIds = selectedTeams.map((t) => t.id).filter(Boolean);
    
    // Role is required if users are selected
    if (userIds.length > 0 && !newRole) {
      setError('Please select a role for users');
      return;
    }
    
    setActionLoading(true);
    setError(null);
    try {
      await api.createPermissions({ 
        userIds, 
        teamIds, 
        role: userIds.length > 0 ? newRole : undefined // Only send role if users are selected
      });
      setShowAddForm(false);
      setSelectedUsers([]);
      setSelectedTeams([]);
      setNewRole('READER');
      await loadAll();
    } catch (e: any) {
      setError(e?.message || 'Failed to add permissions');
    } finally {
      setActionLoading(false);
    }
  };

  const startEdit = (p: UnifiedPermission) => {
    // Teams don't have roles, so we can't edit them
    if (p.type === 'TEAM') {
      setError('Teams do not have roles. Access is determined by individual team member roles.');
      return;
    }
    // For USER type, role should always be defined
    if (!p.role) {
      setError('Cannot edit permission: role is not defined.');
      return;
    }
    setEditingEntity({ type: p.type, id: p.id });
    setEditRole(p.role);
  };

  const cancelEdit = () => {
    setEditingEntity(null);
  };

  const handleUpdate = async () => {
    if (!editingEntity) return;
    // Teams don't have roles, so we can only update users
    if (editingEntity.type === 'TEAM') {
      setError('Teams do not have roles. Only user permissions can be updated.');
      return;
    }
    setActionLoading(true);
    setError(null);
    try {
      await api.updatePermissions({ userIds: [editingEntity.id], role: editRole });
      setEditingEntity(null);
      await loadAll();
    } catch (e: any) {
      setError(e?.message || 'Failed to update permission');
    } finally {
      setActionLoading(false);
    }
  };

  const confirmRemove = async () => {
    if (!toDelete) return;
    setActionLoading(true);
    setError(null);
    try {
      if (toDelete.type === 'USER') {
        await api.removePermissions({ userIds: [toDelete.id] });
      } else {
        await api.removePermissions({ teamIds: [toDelete.id] });
      }
      setDeleteOpen(false);
      setToDelete(null);
      await loadAll();
    } catch (e: any) {
      setError(e?.message || 'Failed to remove permission');
    } finally {
      setActionLoading(false);
    }
  };

  const handleOpenTeamDialog = () => {
    setTeamDialogOpen(true);
  };

  const handleCloseTeamDialog = () => {
    setTeamDialogOpen(false);
  };

  const handleCreateTeam = async (formData: {
    name: string;
    description?: string;
    role: string;
    members: any[];
    memberRoles?: Array<{ userId: string; role: string }>;
  }) => {
    setError(null);
    try {
      // Convert TeamFormData format to UnifiedPermissionsApi format
      const memberRoles: Array<{ userId: string; role: UnifiedRole }> = formData.memberRoles 
        ? formData.memberRoles.map((mr) => ({
            userId: mr.userId,
            role: mr.role as UnifiedRole,
          }))
        : formData.members.map((member) => ({
            userId: member.id || member._key || member._id || member.userId || '',
            role: (formData.role || 'READER') as UnifiedRole,
          })).filter((mr) => mr.userId);

      const created = await api.createTeam({
        name: formData.name,
        description: formData.description,
        memberRoles,
      });

      // Reload teams list to ensure we have the latest data
      const updatedTeams = await api.loadTeams();
      setTeams(updatedTeams || []);

      // Add the newly created team to selected teams if it's not already there
      if (created && created.id) {
        setSelectedTeams((prev) => {
          const exists = prev.some((t) => t.id === created.id);
          return exists ? prev : [created, ...prev];
        });
      }

      handleCloseTeamDialog();
    } catch (e: any) {
      console.error('Error creating team:', e);
      throw new Error(e?.message || 'Failed to create team');
    }
  };

  return (
    <>
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
                {title}
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  color: theme.palette.text.secondary,
                  fontSize: '0.875rem',
                }}
              >
                {subjectName}
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
              <Box sx={{ py: 2 }}>
                {!showAddForm && (
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    sx={{ mb: 2 }}
                  >
                    <Button
                      variant="contained"
                      size="small"
                      startIcon={<Icon icon={addIcon} width={16} height={16} />}
                      onClick={() => setShowAddForm(true)}
                      disabled={actionLoading}
                      sx={{
                        textTransform: 'none',
                        fontWeight: 500,
                        fontSize: '0.875rem',
                        px: 2.5,
                        py: 0.75,
                        borderRadius: 1,
                      }}
                    >
                      {addPeopleLabel}
                    </Button>

                    <Button
                      variant="text"
                      onClick={loadAll}
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
                        <Typography
                          variant="subtitle1"
                          sx={{ fontWeight: 600, color: theme.palette.text.primary }}
                        >
                          Add People & Teams
                        </Typography>
                        <Stack direction="row" spacing={1}>
                          <Tooltip title="Create new team">
                            <Button
                              variant="outlined"
                              size="small"
                              onClick={handleOpenTeamDialog}
                              startIcon={<Icon icon={teamIcon} width={16} height={16} />}
                              sx={{ textTransform: 'none' }}
                            >
                              New Team
                            </Button>
                          </Tooltip>
                          <IconButton
                            onClick={() => {
                              setShowAddForm(false);
                              setSelectedUsers([]);
                              setSelectedTeams([]);
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
                      </Stack>

                      <Stack spacing={2}>
                        <Box>
                          <Typography
                            variant="body2"
                            sx={{ mb: 1, fontWeight: 500, color: theme.palette.text.primary }}
                          >
                            Select Users
                          </Typography>
                          <Autocomplete
                            multiple
                            options={users}
                            getOptionLabel={(option) =>
                              option.name || option.email || 'Unknown User'
                            }
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
                            onChange={(_, newValue) => setSelectedUsers(newValue)}
                            value={selectedUsers}
                            renderTags={(value, getTagProps) =>
                              value.map((option, index) => (
                                <Chip
                                  {...getTagProps({ index })}
                                  label={option.name || option.email || 'Unknown User'}
                                  size="small"
                                  avatar={
                                    <Avatar
                                      sx={{
                                        width: 20,
                                        height: 20,
                                        fontSize: '0.625rem',
                                        bgcolor: getAvatarColor(option.name || option.email || 'U'),
                                      }}
                                    >
                                      {getInitials(option.name || option.email || 'U')}
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
                              <li {...props} key={option.id}>
                                <Stack
                                  direction="row"
                                  alignItems="center"
                                  spacing={2}
                                  sx={{ py: 1, width: '100%' }}
                                >
                                  <Avatar
                                    sx={{
                                      width: 32,
                                      height: 32,
                                      fontSize: '0.75rem',
                                      bgcolor: getAvatarColor(option.name || option.email || 'U'),
                                    }}
                                  >
                                    {getInitials(option.name || option.email || 'U')}
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
                                      {option.name || option.email || 'Unknown User'}
                                    </Typography>
                                    {option.name && option.email && (
                                      <Typography variant="caption" color="text.secondary">
                                        {option.email}
                                      </Typography>
                                    )}
                                  </Box>
                                </Stack>
                              </li>
                            )}
                            ListboxProps={{
                              style: { maxHeight: 240 },
                            }}
                          />
                        </Box>

                        <Box>
                          <Typography
                            variant="body2"
                            sx={{ mb: 1, fontWeight: 500, color: theme.palette.text.primary }}
                          >
                            Select Teams
                          </Typography>
                          <Autocomplete
                            multiple
                            options={teams}
                            getOptionLabel={(option) => option.name}
                            renderInput={(params) => (
                              <TextField
                                {...params}
                                placeholder="Search and select teams..."
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
                            onChange={(_, newValue) => setSelectedTeams(newValue)}
                            value={selectedTeams}
                            renderTags={(value, getTagProps) =>
                              value.map((option, index) => (
                                <Chip
                                  {...getTagProps({ index })}
                                  label={option.name}
                                  size="small"
                                  icon={<Icon icon={teamIcon} width={14} height={14} />}
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
                              <li {...props} key={option.id}>
                                <Stack
                                  direction="row"
                                  alignItems="center"
                                  spacing={2}
                                  sx={{ py: 1, width: '100%' }}
                                >
                                  <Box
                                    sx={{
                                      width: 32,
                                      height: 32,
                                      borderRadius: '50%',
                                      display: 'flex',
                                      alignItems: 'center',
                                      justifyContent: 'center',
                                      bgcolor: alpha(theme.palette.info.main, 0.15),
                                    }}
                                  >
                                    <Icon
                                      icon={teamIcon}
                                      width={18}
                                      height={18}
                                      style={{ color: theme.palette.info.main }}
                                    />
                                  </Box>
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
                                      {option.name}
                                    </Typography>
                                    {option.description && (
                                      <Typography variant="caption" color="text.secondary">
                                        {option.description}
                                      </Typography>
                                    )}
                                  </Box>
                                </Stack>
                              </li>
                            )}
                            ListboxProps={{
                              style: { maxHeight: 240 },
                            }}
                          />
                        </Box>

                        <Stack
                          direction="row"
                          spacing={2}
                          alignItems="flex-end"
                          justifyContent="space-between"
                        >
                          {selectedUsers.length > 0 && (
                            <FormControl size="small" sx={{ minWidth: 280 }}>
                              <Typography
                                variant="body2"
                                sx={{ mb: 1, fontWeight: 500, color: theme.palette.text.primary }}
                              >
                                Permission Role {selectedTeams.length > 0 && '(for users only)'}
                              </Typography>
                              <Select
                                value={newRole}
                                onChange={(e) => setNewRole(e.target.value as UnifiedRole)}
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
                          )}
                          {selectedTeams.length > 0 && selectedUsers.length === 0 && (
                            <Typography
                              variant="body2"
                              sx={{ 
                                color: theme.palette.text.secondary,
                                fontStyle: 'italic',
                                fontSize: '0.875rem'
                              }}
                            >
                              Teams don&apos;t have roles. Access is determined by individual team member roles.
                            </Typography>
                          )}

                          <Stack direction="row" spacing={1}>
                            <Button
                              variant="outlined"
                              onClick={() => {
                                setShowAddForm(false);
                                setSelectedUsers([]);
                                setSelectedTeams([]);
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
                              disabled={
                                actionLoading ||
                                (selectedUsers.length === 0 && selectedTeams.length === 0)
                              }
                              startIcon={
                                actionLoading ? (
                                  <CircularProgress size={16} color="inherit" />
                                ) : (
                                  <Icon icon={addIcon} width={16} height={16} />
                                )
                              }
                              sx={{
                                textTransform: 'none',
                                fontWeight: 500,
                                px: 3,
                              }}
                            >
                              {actionLoading
                                ? 'Adding...'
                                : (() => {
                                    const parts = [];
                                    if (selectedUsers.length > 0) {
                                      parts.push(`${selectedUsers.length} ${selectedUsers.length === 1 ? 'Member' : 'Members'}`);
                                    }
                                    if (selectedTeams.length > 0) {
                                      parts.push(`${selectedTeams.length} ${selectedTeams.length === 1 ? 'Team' : 'Teams'}`);
                                    }
                                    return parts.length > 0 ? `Add ${parts.join(' and ')}` : 'Add';
                                  })()}
                            </Button>
                          </Stack>
                        </Stack>
                      </Stack>
                    </Stack>
                  </Paper>
                </Collapse>
              </Box>

              <Divider />

              {/* Current permissions list */}
              <Box sx={{ py: 2 }}>
                <Stack
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                  sx={{ mb: 2 }}
                >
                  <Typography
                    variant="h6"
                    sx={{
                      fontWeight: 600,
                      fontSize: '1rem',
                    }}
                  >
                    Members ({permissions.length})
                  </Typography>
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
                        marginBottom: 12,
                      }}
                    />
                    <Typography variant="body1" sx={{ mb: 1, fontWeight: 500 }}>
                      No members yet
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                      Add people or teams to start collaborating
                    </Typography>
                    <Button
                      variant="outlined"
                      onClick={() => setShowAddForm(true)}
                      size="small"
                      sx={{ textTransform: 'none' }}
                    >
                      Add Your First Member
                    </Button>
                  </Paper>
                ) : (
                  <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
                    <TableContainer sx={{ maxHeight: 420 }}>
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
                            <TableCell>Principal</TableCell>
                            <TableCell>Type</TableCell>
                            <TableCell>Role</TableCell>
                            <TableCell>Added</TableCell>
                            <TableCell align="right">Actions</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {permissions
                            .slice(
                              permissionsPage * permissionsPerPage,
                              permissionsPage * permissionsPerPage + permissionsPerPage
                            )
                            .map((p) => (
                              <TableRow
                                key={p.id}
                                sx={{
                                  '&:hover': { bgcolor: alpha(theme.palette.primary.main, 0.04) },
                                }}
                              >
                                <TableCell sx={{ py: 1.5 }}>
                                  <Stack direction="row" alignItems="center" spacing={1.5}>
                                    <Avatar
                                      sx={{
                                        bgcolor: getAvatarColor(p.name || p.email || p.type + p.id),
                                        width: 32,
                                        height: 32,
                                        fontSize: '0.75rem',
                                        fontWeight: 600,
                                      }}
                                    >
                                      {p.type === 'TEAM' ? (
                                        <Icon icon={teamIcon} width={18} height={18} />
                                      ) : (
                                        getInitials(p.name || p.email || 'U')
                                      )}
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
                                        {p.name}
                                      </Typography>
                                      {p.email && (
                                        <Typography
                                          variant="caption"
                                          color="text.secondary"
                                          sx={{
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                            whiteSpace: 'nowrap',
                                          }}
                                        >
                                          {p.email}
                                        </Typography>
                                      )}
                                    </Box>
                                  </Stack>
                                </TableCell>
                                <TableCell sx={{ py: 1.5 }}>
                                  <Chip
                                    label={p.type}
                                    size="small"
                                    variant="outlined"
                                    sx={{
                                      height: 24,
                                      fontSize: '0.7rem',
                                      fontWeight: 600,
                                    }}
                                  />
                                </TableCell>
                                <TableCell sx={{ py: 1.5, minWidth: 240 }}>
                                  {editingEntity &&
                                  editingEntity.id === p.id &&
                                  editingEntity.type === p.type ? (
                                    <Stack direction="row" alignItems="center" spacing={1}>
                                      <FormControl size="small" sx={{ minWidth: 160 }}>
                                        <Select
                                          value={editRole}
                                          onChange={(e) =>
                                            setEditRole(e.target.value as UnifiedRole)
                                          }
                                          disabled={actionLoading}
                                          sx={{ '& .MuiSelect-select': { py: 0.75 } }}
                                        >
                                          {ROLE_OPTIONS.map((option) => (
                                            <MenuItem key={option.value} value={option.value}>
                                              <Box>
                                                <Typography variant="body2" fontWeight={500}>
                                                  {option.label}
                                                </Typography>
                                                <Typography
                                                  variant="caption"
                                                  color="text.secondary"
                                                >
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
                                        onClick={handleUpdate}
                                        disabled={actionLoading}
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
                                    p.type === 'TEAM' ? (
                                      <Chip
                                        label="Team Access"
                                        color="info"
                                        variant="outlined"
                                        size="small"
                                        sx={{
                                          height: 28,
                                          fontSize: '0.75rem',
                                          fontWeight: 600,
                                          borderRadius: 1,
                                        }}
                                      />
                                    ) : (
                                      (() => {
                                        const role = p.role;
                                        if (!role) {
                                          return (
                                            <Chip
                                              label="No Role"
                                              color="default"
                                              variant="outlined"
                                              size="small"
                                              sx={{
                                                height: 28,
                                                fontSize: '0.75rem',
                                                fontWeight: 600,
                                                borderRadius: 1,
                                              }}
                                            />
                                          );
                                        }
                                        return (
                                          <Chip
                                            label={
                                              ROLE_OPTIONS.find((r) => r.value === role)?.label ||
                                              role
                                            }
                                            color={getRoleColor(role)}
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
                                            }}
                                          />
                                        );
                                      })()
                                    )
                                  )}
                                </TableCell>
                                <TableCell sx={{ py: 1.5 }}>
                                  <Typography variant="caption" color="text.secondary">
                                    {(() => {
                                      const ts = p.createdAtTimestamp || p.createdAtTimestamp;
                                      if (!ts) return '-';
                                      try {
                                        return new Date(ts).toLocaleDateString('en-US', {
                                          month: 'short',
                                          day: 'numeric',
                                          year: 'numeric',
                                        });
                                      } catch {
                                        return '-';
                                      }
                                    })()}
                                  </Typography>
                                </TableCell>
                                <TableCell align="right" sx={{ py: 1.5 }}>
                                  <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                                    {/* Only show edit button for non-OWNER users and non-TEAM types */}
                                    {p.type !== 'TEAM' && p.role !== 'OWNER' && (
                                      <Tooltip title="Edit permissions">
                                        <span>
                                          <IconButton
                                            size="small"
                                            onClick={() => startEdit(p)}
                                            disabled={actionLoading}
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
                                        </span>
                                      </Tooltip>
                                    )}
                                    {/* Only show delete button for non-OWNER users */}
                                    {p.role !== 'OWNER' && (
                                      <Tooltip title="Remove access">
                                        <span>
                                          <IconButton
                                            size="small"
                                            onClick={() => {
                                              setToDelete(p);
                                              setDeleteOpen(true);
                                            }}
                                            disabled={actionLoading}
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
                                        </span>
                                      </Tooltip>
                                    )}
                                  </Stack>
                                </TableCell>
                              </TableRow>
                            ))}
                        </TableBody>
                      </Table>
                    </TableContainer>

                    {permissions.length > permissionsPerPage && (
                      <Box
                        sx={{
                          display: 'flex',
                          justifyContent: 'center',
                          p: 1,
                          borderTop: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                          bgcolor: alpha(theme.palette.background.default, 0.3),
                        }}
                      >
                        <Stack direction="row" spacing={1} alignItems="center">
                          <IconButton
                            size="small"
                            onClick={() => setPermissionsPage(Math.max(0, permissionsPage - 1))}
                            disabled={permissionsPage === 0}
                          >
                            <Icon icon="eva:chevron-left-fill" width={16} height={16} />
                          </IconButton>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ minWidth: 100, textAlign: 'center' }}
                          >
                            {permissionsPage * permissionsPerPage + 1}-
                            {Math.min(
                              (permissionsPage + 1) * permissionsPerPage,
                              permissions.length
                            )}{' '}
                            of {permissions.length}
                          </Typography>
                          <IconButton
                            size="small"
                            onClick={() => setPermissionsPage(permissionsPage + 1)}
                            disabled={
                              (permissionsPage + 1) * permissionsPerPage >= permissions.length
                            }
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

      {/* Remove confirmation dialog */}
      <Dialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        maxWidth="xs"
        fullWidth
        TransitionComponent={Fade}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            zIndex: 1400,
          },
        }}
      >
        <DialogTitle sx={{ p: 2.5 }}>
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Box
              sx={{
                width: 32,
                height: 32,
                borderRadius: '6px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                bgcolor: alpha(theme.palette.error.main, 0.1),
                color: theme.palette.error.main,
              }}
            >
              <Icon icon={warningIcon} width={16} height={16} />
            </Box>
            <Typography variant="subtitle1" fontWeight={600}>
              Remove Access
            </Typography>
          </Stack>
        </DialogTitle>
        <DialogContent sx={{ p: 3 }}>
          <DialogContentText>
            Are you sure you want to remove {toDelete?.name}&apos;s access?
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ p: 2.5 }}>
          <Button
            onClick={() => setDeleteOpen(false)}
            disabled={actionLoading}
            sx={{ color: theme.palette.text.secondary, fontWeight: 500 }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            disabled={actionLoading}
            onClick={confirmRemove}
            startIcon={
              actionLoading ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Icon icon={deleteIcon} width={16} height={16} />
              )
            }
            sx={{ fontWeight: 500, px: 3 }}
          >
            {actionLoading ? 'Removing...' : 'Remove Access'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Create Team dialog */}
      <CreateTeamDialog
        open={teamDialogOpen}
        onClose={handleCloseTeamDialog}
        onSubmit={handleCreateTeam}
        onSuccess={() => {
          // Success is handled in handleCreateTeam
        }}
        onError={(message) => {
          setError(message);
        }}
      />
    </>
  );
};

export default UnifiedPermissionsDialog;
