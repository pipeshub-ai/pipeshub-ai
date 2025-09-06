import React, { useCallback, useEffect, useMemo, useState } from 'react';
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
  TextField,
  Autocomplete,
  Chip,
  Stack,
  Avatar,
  MenuItem,
  CircularProgress,
  Fade,
  List,
  ListItem,
  ListItemAvatar,
  ListItemText,
  ListItemSecondaryAction,
  Divider,
  Tooltip,
  Paper,
  FormControl,
  InputLabel,
  Select,
} from '@mui/material';
import { Icon } from '@iconify/react';
import axios from 'src/utils/axios';
import { createScrollableContainerStyle } from 'src/sections/qna/chatbot/utils/styles/scrollbar';
import closeIcon from '@iconify-icons/eva/close-outline';
import shareIcon from '@iconify-icons/mdi/share-outline';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import personIcon from '@iconify-icons/eva/person-add-fill';
import searchIcon from '@iconify-icons/eva/search-fill';
import closeFillIcon from '@iconify-icons/eva/close-fill';
import warningIcon from '@iconify-icons/eva/alert-triangle-outline';
import plusIcon from '@iconify-icons/eva/plus-fill';
import editIcon from '@iconify-icons/eva/edit-2-fill';
import deleteIcon from '@iconify-icons/eva/trash-2-fill';
import refreshIcon from '@iconify-icons/eva/refresh-fill';
import { userChipStyle, groupChipStyle } from '../../utils/agent';

// Types
interface ManageAgentPermissionsDialogProps {
  open: boolean;
  onClose: () => void;
  agentId: string;
  agentName: string;
  onPermissionsUpdated?: () => void;
}

interface User {
  _id: string;
  _key: string;
  fullName: string;
  email: string;
}

interface Team {
  _id: string;
  _key?: string;
  name: string;
  description?: string;
}

interface ShareData {
  userIds: string[];
  teamIds: string[];
  message?: string;
  role: string;
}

interface Permission {
  _id: string;
  _key: string;
  entity_id: string;
  entity_key: string;
  entity_name: string;
  entity_email?: string;
  entity_type: 'USER' | 'TEAM';
  role: string;
  created_at: number;
  updated_at: number;
}

interface UpdatePermissionData {
  userIds?: string[];
  teamIds?: string[];
  role: string;
}

// Helper function to get initials
const getInitials = (fullName: string) =>
  fullName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase();

const ManageAgentPermissionsDialog: React.FC<ManageAgentPermissionsDialogProps> = ({
  open,
  onClose,
  agentId,
  agentName,
  onPermissionsUpdated,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const scrollableStyle = createScrollableContainerStyle(theme);

  // State
  const [selectedUsers, setSelectedUsers] = useState<User[]>([]);
  const [selectedTeams, setSelectedTeams] = useState<Team[]>([]);
  const [selectedRole, setSelectedRole] = useState<string>('READER');
  const [message, setMessage] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [loadingUsers, setLoadingUsers] = useState<boolean>(false);
  const [loadingTeams, setLoadingTeams] = useState<boolean>(false);
  const [users, setUsers] = useState<User[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [loadingPermissions, setLoadingPermissions] = useState<boolean>(false);
  const [editingPermission, setEditingPermission] = useState<Permission | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState<boolean>(false);
  const [newRole, setNewRole] = useState<string>('');
  const [updatingPermission, setUpdatingPermission] = useState<boolean>(false);
  const [deletingPermission, setDeletingPermission] = useState<boolean>(false);

  // Create Team Dialog State
  const [teamDialogOpen, setTeamDialogOpen] = useState<boolean>(false);
  const [newTeamName, setNewTeamName] = useState<string>('');
  const [newTeamDescription, setNewTeamDescription] = useState<string>('');
  const [creatingTeam, setCreatingTeam] = useState<boolean>(false);
  const [teamUsers, setTeamUsers] = useState<User[]>([]);
  const [teamRole, setTeamRole] = useState<string>('READER');

  // Search state for filtering current access
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Delete confirmation dialog state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState<boolean>(false);
  const [permissionToDelete, setPermissionToDelete] = useState<Permission | null>(null);

  // Reset form when dialog closes
  const handleClose = () => {
    if (!isSubmitting) {
      setSelectedUsers([]);
      setSelectedTeams([]);
      setSelectedRole('READER');
      setMessage('');
      setTeamDialogOpen(false);
      setNewTeamName('');
      setNewTeamDescription('');
      setTeamUsers([]);
      setTeamRole('READER');
      setEditingPermission(null);
      setEditDialogOpen(false);
      setNewRole('');
      setSearchQuery('');
      setDeleteDialogOpen(false);
      setPermissionToDelete(null);
      onClose();
    }
  };

  const handleShare = async () => {
    if (selectedUsers.length === 0 && selectedTeams.length === 0) return;

    setIsSubmitting(true);
    try {
      const payload: ShareData = {
        userIds: selectedUsers.map((u) => u._key),
        teamIds: selectedTeams.map((t) => t._id || t._key || '').filter(Boolean),
        message: message.trim() || undefined,
        role: selectedRole,
      };
      await axios.post(`/api/v1/agents/${agentId}/share`, payload);

      await fetchPermissions();
      onPermissionsUpdated?.();

      // Reset form
      setSelectedUsers([]);
      setSelectedTeams([]);
      setSelectedRole('READER');
      setMessage('');
    } catch (error) {
      console.error('Error sharing agent:', error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const fetchPermissions = async () => {
    setLoadingPermissions(true);
    try {
      const { data } = await axios.get(`/api/v1/agents/${agentId}/permissions`);
      setPermissions(data?.permissions || []);
    } catch (error) {
      console.error('Error fetching permissions:', error);
      setPermissions([]);
    } finally {
      setLoadingPermissions(false);
    }
  };

  const handleUpdatePermission = async () => {
    if (!editingPermission || !newRole) return;

    setUpdatingPermission(true);
    try {
      const payload: UpdatePermissionData = {
        role: newRole,
      };

      if (editingPermission.entity_type === 'USER') {
        payload.userIds = [editingPermission.entity_key];
      } else {
        payload.teamIds = [editingPermission.entity_key];
      }

      await axios.put(`/api/v1/agents/${agentId}/permissions`, payload);

      await fetchPermissions();
      onPermissionsUpdated?.();
      setEditDialogOpen(false);
      setEditingPermission(null);
      setNewRole('');
    } catch (error) {
      console.error('Error updating permission:', error);
    } finally {
      setUpdatingPermission(false);
    }
  };

  const handleDeletePermission = async (permission: Permission) => {
    setPermissionToDelete(permission);
    setDeleteDialogOpen(true);
  };

  const confirmDeletePermission = async () => {
    if (!permissionToDelete) return;

    setDeletingPermission(true);
    try {
      const payload = {
        userIds: permissionToDelete.entity_type === 'USER' ? [permissionToDelete.entity_key] : [],
        teamIds: permissionToDelete.entity_type === 'TEAM' ? [permissionToDelete.entity_key] : [],
      };

      await axios.post(`/api/v1/agents/${agentId}/unshare`, payload);

      await fetchPermissions();
      onPermissionsUpdated?.();
      setDeleteDialogOpen(false);
      setPermissionToDelete(null);
    } catch (error) {
      console.error('Error removing permission:', error);
    } finally {
      setDeletingPermission(false);
    }
  };

  const fetchUsers = async () => {
    setLoadingUsers(true);
    try {
      const { data } = await axios.get(`/api/v1/users/graph/list`);
      const items: User[] = data?.users || [];
      console.log(items);
      setUsers(items);
    } finally {
      setLoadingUsers(false);
    }
  };

  const fetchTeams = async () => {
    setLoadingTeams(true);
    try {
      const { data } = await axios.get(`/api/v1/teams/list?limit=100`);
      const items = data?.teams || [];
      const mapped: Team[] = items.map((t: any) => ({
        _id: t._key || t._id?.split('/')[1],
        _key: t._key,
        name: t.name,
        description: t.description,
      }));
      setTeams(mapped);
    } finally {
      setLoadingTeams(false);
    }
  };

  const handleCreateTeam = async () => {
    if (!newTeamName.trim()) return;

    setCreatingTeam(true);
    try {
      const body = {
        name: newTeamName.trim(),
        description: newTeamDescription.trim() || undefined,
        userIds: teamUsers.map((u) => u._key),
        role: teamRole,
      };
      const { data } = await axios.post('/api/v1/entity/team', body);
      const created = data?.data || data;
      const createdTeam: Team = {
        _id: created?._key,
        _key: created?._key,
        name: created?.name,
        description: created?.description,
      };

      setTeams((prev) => [createdTeam, ...prev]);
      setSelectedTeams((prev) => [createdTeam, ...prev]);

      // Reset form
      setNewTeamName('');
      setNewTeamDescription('');
      setTeamUsers([]);
      setTeamRole('READER');
      setTeamDialogOpen(false);
    } catch (error) {
      console.error('Failed to create team', error);
    } finally {
      setCreatingTeam(false);
    }
  };

  useEffect(() => {
    if (open) {
      fetchUsers();
      fetchTeams();
      fetchPermissions();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

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

  const canShare = useMemo(
    () => selectedUsers.length > 0 || selectedTeams.length > 0,
    [selectedUsers, selectedTeams]
  );

  const roleOptions = useMemo(() => [
    { value: 'READER', label: 'Reader', description: 'Can view and use the agent' },
    { value: 'WRITER', label: 'Writer', description: 'Can view, use, and modify the agent' },
    { value: 'OWNER', label: 'Owner', description: 'Can manage and share the agent' },
  ], []);

  const getRoleDisplayName = useCallback((role: string) => {
    const option = roleOptions.find((r) => r.value === role);
    return option?.label || role;
  }, [roleOptions]);

  const getRoleBadgeColor = (role: string) => {
    switch (role) {
      case 'OWNER':
        return theme.palette.error.main;
      case 'ORGANIZER':
        return theme.palette.warning.main;
      case 'WRITER':
        return theme.palette.info.main;
      case 'READER':
        return theme.palette.success.main;
      default:
        return theme.palette.grey[500];
    }
  };

  // Filter permissions based on search query
  const filteredPermissions = useMemo(() => {
    if (!searchQuery.trim()) return permissions;

    const query = searchQuery.toLowerCase();
    return permissions.filter((permission) => {
      const nameMatch = permission.entity_name.toLowerCase().includes(query);
      const emailMatch = permission.entity_email?.toLowerCase().includes(query) || false;
      const roleMatch = getRoleDisplayName(permission.role).toLowerCase().includes(query);
      const typeMatch = permission.entity_type.toLowerCase().includes(query);

      return nameMatch || emailMatch || roleMatch || typeMatch;
    });
  }, [permissions, searchQuery, getRoleDisplayName]);

  return (
    <>
      <Dialog
        open={open}
        onClose={handleClose}
        maxWidth="md"
        fullWidth
        TransitionComponent={Fade}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            overflow: 'hidden',
            maxHeight: '90vh',
            zIndex: 1300,
          },
        }}
      >
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            p: 2.5,
            pl: 3,
            color: theme.palette.text.primary,
            borderBottom: '1px solid',
            borderColor: theme.palette.divider,
            fontWeight: 500,
            fontSize: '1rem',
            m: 0,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.primary.main, 0.1),
                color: theme.palette.primary.main,
              }}
            >
              <Icon icon={shareIcon} width={18} height={18} />
            </Box>
            Manage Permissions - {agentName}
          </Box>

          <IconButton
            onClick={handleClose}
            size="small"
            sx={{ color: theme.palette.text.secondary }}
            disabled={isSubmitting}
          >
            <Icon icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        <DialogContent
          sx={{
            p: 0,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            mt:2,
            ...scrollableStyle,
          }}
        >
          <Box sx={{ p: 3, flex: 1, overflow: 'auto', ...scrollableStyle }}>
            {/* Current Permissions Section */}

            {/* Grant New Access Section */}
            <Box>
              <Stack spacing={2.5}>
                {/* Users Selection */}
                <Autocomplete
                  multiple
                  limitTags={2}
                  options={users}
                  loading={loadingUsers}
                  getOptionLabel={(option) => option.fullName || 'Unknown User'}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Select Users"
                      placeholder="Choose users to share with..."
                      size="small"
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          borderRadius: 1.5,
                          bgcolor: alpha(theme.palette.background.paper, 0.4),
                          backdropFilter: 'blur(8px)',
                          border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
                          '&:hover': { bgcolor: alpha(theme.palette.background.paper, 0.6) },
                          '&.Mui-focused': {
                            bgcolor: alpha(theme.palette.background.paper, 0.8),
                            borderColor: theme.palette.primary.main,
                          },
                        },
                      }}
                      InputProps={{
                        ...params.InputProps,
                        startAdornment: (
                          <>
                            <Icon
                              icon={personIcon}
                              width={16}
                              height={16}
                              style={{ color: theme.palette.text.secondary, marginRight: 8 }}
                            />
                            {params.InputProps.startAdornment}
                          </>
                        ),
                      }}
                    />
                  )}
                  onChange={(_, newValue) => setSelectedUsers(newValue)}
                  value={selectedUsers}
                  renderTags={(value, getTagProps) =>
                    value.map((option, index) => (
                      <Chip
                        label={option.fullName || 'Unknown User'}
                        {...getTagProps({ index })}
                        size="small"
                        sx={userChipStyle(isDark, theme)}
                      />
                    ))
                  }
                  renderOption={(props, option) => (
                    <MenuItem
                      {...props}
                      sx={{ py: 1, px: 1.5, borderRadius: 1, my: 0.25, mx: 0.5 }}
                    >
                      <Stack direction="row" alignItems="center" spacing={1.5}>
                        <Avatar
                          sx={{
                            width: 28,
                            height: 28,
                            fontSize: '0.7rem',
                            bgcolor: getAvatarColor(option.fullName || 'U'),
                          }}
                        >
                          {getInitials(option.fullName || 'U')}
                        </Avatar>
                        <Box>
                          <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.85rem' }}>
                            {option.fullName || 'Unknown User'}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontSize: '0.7rem' }}
                          >
                            {option.email || 'No email'}
                          </Typography>
                        </Box>
                      </Stack>
                    </MenuItem>
                  )}
                />

                {/* Teams Selection */}
                <Autocomplete
                  multiple
                  limitTags={2}
                  options={teams}
                  loading={loadingTeams}
                  getOptionLabel={(option) => option.name}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Select Teams"
                      placeholder="Choose teams to share with..."
                      size="small"
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          borderRadius: 1.5,
                          bgcolor: alpha(theme.palette.background.paper, 0.4),
                          backdropFilter: 'blur(8px)',
                          border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
                          '&:hover': { bgcolor: alpha(theme.palette.background.paper, 0.6) },
                          '&.Mui-focused': {
                            bgcolor: alpha(theme.palette.background.paper, 0.8),
                            borderColor: theme.palette.primary.main,
                          },
                        },
                      }}
                      InputProps={{
                        ...params.InputProps,
                        startAdornment: (
                          <>
                            <Icon
                              icon={accountGroupIcon}
                              width={16}
                              height={16}
                              style={{ color: theme.palette.text.secondary, marginRight: 8 }}
                            />
                            {params.InputProps.startAdornment}
                          </>
                        ),
                        endAdornment: (
                          <Stack direction="row" alignItems="center" spacing={0.5}>
                            <Tooltip title="Create New Team" placement="top">
                              <IconButton
                                size="small"
                                onClick={() => setTeamDialogOpen(true)}
                                sx={{
                                  width: 24,
                                  height: 24,
                                  color: theme.palette.text.secondary,
                                  '&:hover': {
                                    bgcolor: alpha(theme.palette.success.main, 0.1),
                                    color: theme.palette.success.main,
                                  },
                                }}
                              >
                                <Icon icon={plusIcon} width={14} height={14} />
                              </IconButton>
                            </Tooltip>
                            {params.InputProps.endAdornment}
                          </Stack>
                        ),
                      }}
                    />
                  )}
                  onChange={(_, newValue) => setSelectedTeams(newValue)}
                  value={selectedTeams}
                  renderTags={(value, getTagProps) =>
                    value.map((option, index) => (
                      <Chip
                        label={option.name}
                        {...getTagProps({ index })}
                        size="small"
                        sx={groupChipStyle(isDark, theme)}
                      />
                    ))
                  }
                  renderOption={(props, option) => (
                    <MenuItem
                      {...props}
                      sx={{ py: 1, px: 1.5, borderRadius: 1, my: 0.25, mx: 0.5 }}
                    >
                      <Stack direction="row" alignItems="center" spacing={1.5}>
                        <Box
                          sx={{
                            width: 28,
                            height: 28,
                            borderRadius: '50%',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            bgcolor: alpha(theme.palette.info.main, 0.15),
                          }}
                        >
                          <Icon
                            icon={accountGroupIcon}
                            width={14}
                            height={14}
                            style={{ color: theme.palette.info.main }}
                          />
                        </Box>
                        <Box>
                          <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.85rem' }}>
                            {option.name}
                          </Typography>
                          {option.description && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontSize: '0.7rem' }}
                            >
                              {option.description}
                            </Typography>
                          )}
                        </Box>
                      </Stack>
                    </MenuItem>
                  )}
                />

                {/* Role Selection */}
                <FormControl fullWidth size="small">
                  <InputLabel sx={{ fontSize: '0.875rem' }}>Permission Level</InputLabel>
                  <Select
                    value={selectedRole}
                    onChange={(e) => setSelectedRole(e.target.value)}
                    label="Permission Level"
                    sx={{
                      borderRadius: 1.5,
                      bgcolor: alpha(theme.palette.background.paper, 0.4),
                      backdropFilter: 'blur(8px)',
                      border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
                      '&:hover': { bgcolor: alpha(theme.palette.background.paper, 0.6) },
                      '&.Mui-focused': {
                        bgcolor: alpha(theme.palette.background.paper, 0.8),
                        '& .MuiOutlinedInput-notchedOutline': {
                          borderColor: theme.palette.primary.main,
                        },
                      },
                    }}
                  >
                    {roleOptions.map((option) => (
                      <MenuItem key={option.value} value={option.value} sx={{ py: 1, px: 1.5 }}>
                        <Box>
                          <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.85rem' }}>
                            {option.label}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontSize: '0.7rem' }}
                          >
                            {option.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                {/* Summary */}
                {canShare && (
                  <Paper
                    sx={{
                      p: 2,
                      borderRadius: 1.5,
                      bgcolor: alpha(theme.palette.success.main, 0.06),
                      border: `1px solid ${alpha(theme.palette.success.main, 0.15)}`,
                      backdropFilter: 'blur(8px)',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
                    }}
                  >
                    <Typography
                      variant="body2"
                      color="success.main"
                      sx={{ fontWeight: 600, fontSize: '0.85rem', lineHeight: 1.3 }}
                    >
                      Ready to grant {getRoleDisplayName(selectedRole)} access to{' '}
                      {selectedUsers.length + selectedTeams.length} recipient
                      {selectedUsers.length + selectedTeams.length !== 1 ? 's' : ''}
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ mt: 0.5, display: 'block', fontSize: '0.7rem' }}
                    >
                      {selectedUsers.length > 0 &&
                        `${selectedUsers.length} user${selectedUsers.length > 1 ? 's' : ''}`}
                      {selectedUsers.length > 0 && selectedTeams.length > 0 && ' and '}
                      {selectedTeams.length > 0 &&
                        `${selectedTeams.length} team${selectedTeams.length > 1 ? 's' : ''}`}
                    </Typography>
                  </Paper>
                )}
              </Stack>
            </Box>
            <Box sx={{ mt: 4 }}>
              <Box sx={{ mt: 2 }}>
                <Stack
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                  spacing={2}
                  sx={{ mb: 2 }}
                >
                  <Typography
                    variant="h6"
                    sx={{ fontWeight: 600, fontSize: '1rem', flexShrink: 0 }}
                  >
                    Current Access ({filteredPermissions.length}
                    {searchQuery &&
                      filteredPermissions.length !== permissions.length &&
                      ` of ${permissions.length}`}
                    )
                  </Typography>

                  <Box
                    sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, maxWidth: 400 }}
                  >
                    <TextField
                      size="small"
                      placeholder="Search permissions..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      sx={{
                        flex: 1,
                        '& .MuiOutlinedInput-root': {
                          borderRadius: 1.5,
                          bgcolor: alpha(theme.palette.background.default, 0.3),
                          '&:hover': { bgcolor: alpha(theme.palette.background.default, 0.4) },
                          '&.Mui-focused': {
                            bgcolor: alpha(theme.palette.background.default, 0.5),
                          },
                        },
                      }}
                      InputProps={{
                        startAdornment: (
                          <Icon
                            icon={searchIcon}
                            width={16}
                            height={16}
                            style={{ color: theme.palette.text.secondary, marginRight: 8 }}
                          />
                        ),
                        endAdornment: searchQuery && (
                          <IconButton
                            size="small"
                            onClick={() => setSearchQuery('')}
                            sx={{ p: 0.5 }}
                          >
                            <Icon icon={closeFillIcon} width={14} height={14} />
                          </IconButton>
                        ),
                      }}
                    />
                    <IconButton
                      size="small"
                      onClick={() => {
                        fetchPermissions();
                        fetchUsers();
                        fetchTeams();
                      }}
                      disabled={loadingPermissions || loadingUsers || loadingTeams}
                      sx={{
                        '&:hover': {
                          bgcolor: alpha(theme.palette.primary.main, 0.1),
                          color: theme.palette.primary.main,
                        },
                      }}
                    >
                      <Icon icon={refreshIcon} width={16} height={16} />
                    </IconButton>
                  </Box>
                </Stack>

                {searchQuery && filteredPermissions.length > 0 && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ mb: 1, display: 'block' }}
                  >
                    Found {filteredPermissions.length} result
                    {filteredPermissions.length !== 1 ? 's' : ''} for &quot;{searchQuery}&quot;
                  </Typography>
                )}

                {loadingPermissions ? (
                  <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
                    <CircularProgress size={20} />
                  </Box>
                ) : permissions.length === 0 ? (
                  <Paper
                    sx={{
                      p: 2.5,
                      textAlign: 'center',
                      bgcolor: alpha(theme.palette.background.default, 0.5),
                      border: `1px dashed ${alpha(theme.palette.divider, 0.3)}`,
                      borderRadius: 2,
                    }}
                  >
                    <Typography variant="body2" color="text.secondary">
                      No users or teams have access yet.
                    </Typography>
                  </Paper>
                ) : (
                  filteredPermissions.length === 0 && (
                    <Paper
                      sx={{
                        p: 2.5,
                        textAlign: 'center',
                        bgcolor: alpha(theme.palette.background.default, 0.5),
                        border: `1px dashed ${alpha(theme.palette.divider, 0.3)}`,
                        borderRadius: 2,
                      }}
                    >
                      <Typography variant="body2" color="text.secondary">
                        No permissions match your search.
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ mt: 0.5, display: 'block' }}
                      >
                        Try different search terms.
                      </Typography>
                    </Paper>
                  )
                )}
              </Box>

              {loadingPermissions ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : permissions.length === 0 ? (
                <Paper
                  sx={{
                    p: 3,
                    textAlign: 'center',
                    bgcolor: alpha(theme.palette.background.default, 0.5),
                    border: `1px dashed ${alpha(theme.palette.divider, 0.3)}`,
                  }}
                >
                  <Typography variant="body2" color="text.secondary">
                    No users or teams have access to this agent yet.
                  </Typography>
                </Paper>
              ) : (
                <Paper
                  sx={{
                    maxHeight: 280,
                    overflow: 'auto',
                    border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
                    borderRadius: 1.5,
                    bgcolor: alpha(theme.palette.background.paper, 0.6),
                    backdropFilter: 'blur(8px)',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
                    '&::-webkit-scrollbar': { width: 4 },
                    '&::-webkit-scrollbar-track': { bgcolor: 'transparent' },
                    '&::-webkit-scrollbar-thumb': {
                      bgcolor: alpha(theme.palette.divider, 0.2),
                      borderRadius: 2,
                      '&:hover': { bgcolor: alpha(theme.palette.divider, 0.3) },
                    },
                  }}
                >
                  <List sx={{ py: 0.5 }}>
                    {filteredPermissions.map((permission, index) => (
                      <React.Fragment key={permission._key}>
                        <ListItem
                          sx={{
                            py: 1.25,
                            px: 2,
                            minHeight: 56,
                            '&:hover': {
                              bgcolor: alpha(theme.palette.primary.main, 0.04),
                              '& .permission-actions': { opacity: 1 },
                            },
                            transition: 'background-color 0.15s ease',
                            borderRadius: 1,
                            mx: 0.5,
                            my: 0.25,
                          }}
                        >
                          <ListItemAvatar sx={{ minWidth: 44 }}>
                            <Avatar
                              sx={{
                                width: 36,
                                height: 36,
                                fontSize: '0.8rem',
                                fontWeight: 600,
                                bgcolor: getAvatarColor(permission.entity_name),
                                boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
                              }}
                            >
                              {permission.entity_type === 'TEAM' ? (
                                <Icon icon={accountGroupIcon} width={18} height={18} />
                              ) : (
                                getInitials(permission.entity_name)
                              )}
                            </Avatar>
                          </ListItemAvatar>

                          <ListItemText
                            primary={
                              <Typography
                                variant="body2"
                                sx={{
                                  fontWeight: 500,
                                  fontSize: '0.875rem',
                                  color: theme.palette.text.primary,
                                  lineHeight: 1.3,
                                }}
                              >
                                {permission.entity_name}
                              </Typography>
                            }
                            secondary={
                              <Stack
                                direction="row"
                                alignItems="center"
                                spacing={1}
                                sx={{ mt: 0.25 }}
                              >
                                <Chip
                                  label={permission.entity_type}
                                  size="small"
                                  variant="outlined"
                                  sx={{
                                    height: 18,
                                    fontSize: '0.65rem',
                                    fontWeight: 500,
                                    borderColor: alpha(theme.palette.divider, 0.3),
                                    color: theme.palette.text.secondary,
                                    bgcolor: 'transparent',
                                    '& .MuiChip-label': { px: 0.75 },
                                  }}
                                />
                                <Chip
                                  label={getRoleDisplayName(permission.role)}
                                  size="small"
                                  sx={groupChipStyle(isDark, theme)}
                                />
                                {permission.entity_email && (
                                  <Typography
                                    variant="caption"
                                    color="text.secondary"
                                    sx={{
                                      fontSize: '0.7rem',
                                      maxWidth: 120,
                                      overflow: 'hidden',
                                      textOverflow: 'ellipsis',
                                      whiteSpace: 'nowrap',
                                    }}
                                  >
                                    {permission.entity_email}
                                  </Typography>
                                )}
                              </Stack>
                            }
                            sx={{ pr: 1 }}
                          />

                          {permission.role !== 'OWNER' && (
                            <ListItemSecondaryAction>
                              <Stack
                                direction="row"
                                spacing={0.25}
                                className="permission-actions"
                                sx={{ opacity: 0.7, transition: 'opacity 0.15s ease' }}
                              >
                                <Tooltip title="Edit Permission" placement="top">
                                  <IconButton
                                    size="small"
                                    onClick={() => {
                                      setEditingPermission(permission);
                                      setNewRole(permission.role);
                                      setEditDialogOpen(true);
                                    }}
                                    disabled={deletingPermission}
                                    sx={{
                                      width: 28,
                                      height: 28,
                                      color: theme.palette.text.secondary,
                                      '&:hover': {
                                        bgcolor: alpha(theme.palette.warning.main, 0.1),
                                        color: theme.palette.warning.main,
                                      },
                                    }}
                                  >
                                    <Icon icon={editIcon} width={14} height={14} />
                                  </IconButton>
                                </Tooltip>
                                <Tooltip title="Remove Access" placement="top">
                                  <IconButton
                                    size="small"
                                    onClick={() => handleDeletePermission(permission)}
                                    disabled={deletingPermission}
                                    sx={{
                                      width: 28,
                                      height: 28,
                                      color: theme.palette.text.secondary,
                                      '&:hover': {
                                        bgcolor: alpha(theme.palette.error.main, 0.1),
                                        color: theme.palette.error.main,
                                      },
                                    }}
                                  >
                                    <Icon icon={deleteIcon} width={14} height={14} />
                                  </IconButton>
                                </Tooltip>
                              </Stack>
                            </ListItemSecondaryAction>
                          )}
                        </ListItem>
                        {index < filteredPermissions.length - 1 && (
                          <Divider
                            sx={{
                              mx: 2,
                              borderColor: alpha(theme.palette.divider, 0.08),
                            }}
                          />
                        )}
                      </React.Fragment>
                    ))}
                  </List>
                </Paper>
              )}
            </Box>
          </Box>
        </DialogContent>

        <DialogActions
          sx={{
            p: 2.5,
            borderTop: '1px solid',
            borderColor: theme.palette.divider,
            bgcolor: alpha(theme.palette.background.default, 0.5),
          }}
        >
          <Button
            variant="text"
            onClick={handleClose}
            disabled={isSubmitting}
            sx={{
              color: theme.palette.text.secondary,
              fontWeight: 500,
            }}
          >
            Close
          </Button>

          <Button
            onClick={handleShare}
            variant="contained"
            color="primary"
            disabled={!canShare || isSubmitting}
            startIcon={
              isSubmitting ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Icon icon={shareIcon} width={18} height={18} />
              )
            }
            sx={{
              fontWeight: 500,
              px: 3,
            }}
          >
            {isSubmitting ? 'Granting Access...' : 'Grant Access'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit Permission Dialog */}
      <Dialog
        open={editDialogOpen}
        onClose={() => setEditDialogOpen(false)}
        maxWidth="xs"
        fullWidth
        TransitionComponent={Fade}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            zIndex: 1400,
            transform: 'scale(0.95)',
            transition: 'transform 0.2s ease',
            '&.MuiDialog-paper': {
              transform: 'scale(1)',
            },
          },
        }}
      >
        <DialogTitle
          sx={{
            p: 2.5,
            pl: 3,
            borderBottom: '1px solid',
            borderColor: theme.palette.divider,
            fontWeight: 500,
            fontSize: '1rem',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.warning.main, 0.1),
                color: theme.palette.warning.main,
              }}
            >
              <Icon icon={editIcon} width={16} height={16} />
            </Box>
            Edit Permission
          </Box>
        </DialogTitle>

        <DialogContent sx={{ p: 3 }}>
          <Stack spacing={3} sx={{ mt: 1 }}>
            <Box>
              <Stack direction="row" alignItems="center" spacing={2}>
                <Avatar
                  sx={{
                    width: 40,
                    height: 40,
                    fontSize: '0.875rem',
                    bgcolor: getAvatarColor(editingPermission?.entity_name || ''),
                  }}
                >
                  {editingPermission?.entity_type === 'TEAM' ? (
                    <Icon icon={accountGroupIcon} width={20} height={20} />
                  ) : (
                    getInitials(editingPermission?.entity_name || '')
                  )}
                </Avatar>
                <Box>
                  <Typography variant="body1" sx={{ fontWeight: 500 }}>
                    {editingPermission?.entity_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {editingPermission?.entity_type}
                    {editingPermission?.entity_email && ` • ${editingPermission.entity_email}`}
                  </Typography>
                </Box>
              </Stack>
            </Box>

            <FormControl fullWidth>
              <InputLabel>New Role</InputLabel>
              <Select
                value={newRole}
                onChange={(e) => setNewRole(e.target.value)}
                label="New Role"
                sx={{
                  borderRadius: 1,
                  backgroundColor: alpha(theme.palette.background.default, 0.3),
                }}
              >
                {roleOptions.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
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
          </Stack>
        </DialogContent>

        <DialogActions
          sx={{
            p: 2.5,
            borderTop: '1px solid',
            borderColor: theme.palette.divider,
            bgcolor: alpha(theme.palette.background.default, 0.5),
          }}
        >
          <Button
            onClick={() => setEditDialogOpen(false)}
            disabled={updatingPermission}
            sx={{ color: theme.palette.text.secondary, fontWeight: 500 }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={updatingPermission || !newRole}
            onClick={handleUpdatePermission}
            startIcon={
              updatingPermission ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Icon icon={editIcon} width={16} height={16} />
              )
            }
            sx={{ fontWeight: 500, px: 3 }}
          >
            {updatingPermission ? 'Updating...' : 'Update Role'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Create Team Dialog */}
      <Dialog
        open={teamDialogOpen}
        onClose={() => setTeamDialogOpen(false)}
        maxWidth="sm"
        fullWidth
        TransitionComponent={Fade}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            overflow: 'hidden',
            zIndex: 1400,
            transform: 'scale(0.95)',
            transition: 'transform 0.2s ease',
            '&.MuiDialog-paper': {
              transform: 'scale(1)',
            },
          },
        }}
      >
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            p: 2.5,
            pl: 3,
            borderBottom: '1px solid',
            borderColor: theme.palette.divider,
            fontWeight: 500,
            fontSize: '1rem',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.success.main, 0.1),
                color: theme.palette.success.main,
              }}
            >
              <Icon icon={plusIcon} width={16} height={16} />
            </Box>
            Create New Team
          </Box>

          <IconButton
            onClick={() => setTeamDialogOpen(false)}
            size="small"
            sx={{ color: theme.palette.text.secondary }}
            disabled={creatingTeam}
          >
            <Icon icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        <DialogContent sx={{ p: 3, minHeight: 400 }}>
          <Stack spacing={3} sx={{ mt: 1 }}>
            <TextField
              label="Team Name"
              value={newTeamName}
              onChange={(e) => setNewTeamName(e.target.value)}
              fullWidth
              autoFocus
              required
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1,
                  backgroundColor: alpha(theme.palette.background.default, 0.3),
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.background.default, 0.4),
                  },
                  '&.Mui-focused': {
                    backgroundColor: alpha(theme.palette.background.default, 0.5),
                  },
                },
              }}
            />

            <TextField
              label="Description"
              value={newTeamDescription}
              onChange={(e) => setNewTeamDescription(e.target.value)}
              fullWidth
              multiline
              rows={3}
              placeholder="Describe the team's purpose..."
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1,
                  backgroundColor: alpha(theme.palette.background.default, 0.3),
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.background.default, 0.4),
                  },
                  '&.Mui-focused': {
                    backgroundColor: alpha(theme.palette.background.default, 0.5),
                  },
                },
              }}
            />

            <FormControl fullWidth>
              <InputLabel>Default Role for Team Members</InputLabel>
              <Select
                value={teamRole}
                onChange={(e) => setTeamRole(e.target.value)}
                label="Default Role for Team Members"
                sx={{
                  borderRadius: 1,
                  backgroundColor: alpha(theme.palette.background.default, 0.3),
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.background.default, 0.4),
                  },
                  '&.Mui-focused': {
                    backgroundColor: alpha(theme.palette.background.default, 0.5),
                  },
                }}
              >
                {roleOptions.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
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

            <Autocomplete
              multiple
              options={users}
              loading={loadingUsers}
              value={teamUsers}
              onChange={(_, newValue) => setTeamUsers(newValue)}
              getOptionLabel={(option) => option.fullName || option.email || 'User'}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Add Team Members"
                  placeholder="Select users to add to the team..."
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      backgroundColor: alpha(theme.palette.background.default, 0.3),
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.background.default, 0.4),
                      },
                      '&.Mui-focused': {
                        backgroundColor: alpha(theme.palette.background.default, 0.5),
                      },
                    },
                  }}
                  InputProps={{
                    ...params.InputProps,
                    startAdornment: (
                      <>
                        <Box mr={1} display="flex" alignItems="center">
                          <Icon
                            icon={personIcon}
                            width={18}
                            height={18}
                            style={{ color: theme.palette.text.secondary }}
                          />
                        </Box>
                        {params.InputProps.startAdornment}
                      </>
                    ),
                  }}
                />
              )}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option.fullName || option.email}
                    {...getTagProps({ index })}
                    size="small"
                    sx={userChipStyle(isDark, theme)}
                  />
                ))
              }
              renderOption={(props, option) => (
                <MenuItem
                  {...props}
                  sx={{
                    py: 1.5,
                    px: 2,
                    borderRadius: 1,
                    my: 0.25,
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={2}>
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
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {option.fullName || option.email}
                      </Typography>
                      {option.fullName && option.email && (
                        <Typography variant="caption" color="text.secondary">
                          {option.email}
                        </Typography>
                      )}
                    </Box>
                  </Stack>
                </MenuItem>
              )}
            />

            {teamUsers.length > 0 && (
              <Paper
                sx={{
                  p: 2,
                  borderRadius: 1,
                  bgcolor: alpha(theme.palette.info.main, 0.08),
                  border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                }}
              >
                <Typography variant="body2" color="info.main" sx={{ fontWeight: 500 }}>
                  Team will be created with {teamUsers.length} member
                  {teamUsers.length > 1 ? 's' : ''}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  All members will have {getRoleDisplayName(teamRole)} permissions
                </Typography>
              </Paper>
            )}
          </Stack>
        </DialogContent>

        <DialogActions
          sx={{
            p: 2.5,
            borderTop: '1px solid',
            borderColor: theme.palette.divider,
            bgcolor: alpha(theme.palette.background.default, 0.5),
          }}
        >
          <Button
            onClick={() => setTeamDialogOpen(false)}
            disabled={creatingTeam}
            sx={{ color: theme.palette.text.secondary, fontWeight: 500 }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={creatingTeam || !newTeamName.trim()}
            onClick={handleCreateTeam}
            startIcon={
              creatingTeam ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Icon icon={plusIcon} width={16} height={16} />
              )
            }
            sx={{ fontWeight: 500, px: 3 }}
          >
            {creatingTeam ? 'Creating...' : 'Create Team'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
        maxWidth="xs"
        fullWidth
        TransitionComponent={Fade}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            zIndex: 1400,
            transform: 'scale(0.95)',
            transition: 'transform 0.2s ease',
            '&.MuiDialog-paper': {
              transform: 'scale(1)',
            },
          },
        }}
      >
        <DialogTitle
          sx={{
            p: 2.5,
            pl: 3,
            borderBottom: '1px solid',
            borderColor: theme.palette.divider,
            fontWeight: 500,
            fontSize: '1rem',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.error.main, 0.1),
                color: theme.palette.error.main,
              }}
            >
              <Icon icon={warningIcon} width={16} height={16} />
            </Box>
            Remove Access
          </Box>
        </DialogTitle>

        <DialogContent sx={{ p: 3 }}>
          <Stack spacing={2}>
            <Typography variant="body1" sx={{ fontWeight: 500, lineHeight: 1.6 }}>
              Are you sure you want to remove{' '}  
              <strong>{permissionToDelete?.entity_name}</strong>&apos;s access to this agent?
            </Typography>

            <Box
              sx={{
                p: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.error.main, 0.08),
                border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
              }}
            >
              <Typography variant="body2" color="error.main" sx={{ fontWeight: 500 }}>
                ⚠️ This action cannot be undone
              </Typography>
            </Box>
          </Stack>
        </DialogContent>

        <DialogActions
          sx={{
            p: 2.5,
            borderTop: '1px solid',
            borderColor: theme.palette.divider,
            bgcolor: alpha(theme.palette.background.default, 0.5),
          }}
        >
          <Button
            onClick={() => setDeleteDialogOpen(false)}
            disabled={deletingPermission}
            sx={{ color: theme.palette.text.secondary, fontWeight: 500 }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            disabled={deletingPermission}
            onClick={confirmDeletePermission}
            startIcon={
              deletingPermission ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Icon icon={deleteIcon} width={16} height={16} />
              )
            }
            sx={{ fontWeight: 500, px: 3 }}
          >
            {deletingPermission ? 'Removing...' : 'Remove Access'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default ManageAgentPermissionsDialog;
