import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Box,
  Stack,
  Typography,
  TextField,
  Button,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  CircularProgress,
  InputAdornment,
  Avatar,
  Autocomplete,
  alpha,
  useTheme,
  IconButton,
  Paper,
  Alert,
  List,
  ListItem,
  ListItemAvatar,
  ListItemText,
  Divider,
  Chip,
  Tooltip,
  Menu,
} from '@mui/material';
import { Icon } from '@iconify/react';
import axios from 'src/utils/axios';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import searchIcon from '@iconify-icons/eva/search-fill';
import closeIcon from '@iconify-icons/mdi/close';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import personAddIcon from '@iconify-icons/eva/person-add-fill';
import infoIcon from '@iconify-icons/eva/info-outline';
import settingsIcon from '@iconify-icons/mdi/settings-outline';
import teamIcon from '@iconify-icons/mdi/account-group';
import { User, Team, TeamFormData, RoleOption } from '../../types/teams';

interface TeamDetailsDialogProps {
  open: boolean;
  team: Team | null;
  onClose: () => void;
  onUpdate: (formData: TeamFormData) => Promise<void>;
  onDelete: () => Promise<void>;
  onSuccess: (message: string) => void;
  onError: (message: string) => void;
  onTeamUpdated?: () => void; // Callback to refresh team data
}

const roleOptions: RoleOption[] = [
  { value: 'READER', label: 'Reader', description: 'Can view team resources' },
  { value: 'WRITER', label: 'Writer', description: 'Can view and edit team resources' },
  { value: 'OWNER', label: 'Owner', description: 'Full access and team management' },
];

const getInitials = (fullName: string) =>
  fullName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

const TeamDetailsDialog: React.FC<TeamDetailsDialogProps> = ({
  open,
  team,
  onClose,
  onUpdate,
  onDelete,
  onSuccess,
  onError,
  onTeamUpdated,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const searchTimeoutRef = useRef<NodeJS.Timeout>();

  const [isEditMode, setIsEditMode] = useState(false);
  const [teamName, setTeamName] = useState('');
  const [teamDescription, setTeamDescription] = useState('');
  const [teamRole, setTeamRole] = useState<string>('READER');
  const [teamMembers, setTeamMembers] = useState<User[]>([]);
  const [memberRoles, setMemberRoles] = useState<Record<string, string>>({});
  const [bulkRoleSelected, setBulkRoleSelected] = useState<string>('READER');
  const [bulkRoleMenuAnchor, setBulkRoleMenuAnchor] = useState<null | HTMLElement>(null);
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [users, setUsers] = useState<User[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [userSearchQuery, setUserSearchQuery] = useState('');
  const [debouncedUserSearchQuery, setDebouncedUserSearchQuery] = useState('');
  const [userPage, setUserPage] = useState(1);
  const [userHasMore, setUserHasMore] = useState(false);

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

  useEffect(() => {
    if (open && team) {
      setTeamName(team.name);
      setTeamDescription(team.description || '');
      const membersAsUsers: User[] = team.members.map((m) => {
        const memberId = m.id || m.userId || '';
        return {
          _key: memberId,
          _id: memberId,
          id: memberId,
          userId: m.userId,
          fullName: m.userName || '',
          name: m.userName || '',
          email: m.userEmail || '',
        };
      });
      setTeamMembers(membersAsUsers);
      
      const roles: Record<string, string> = {};
      team.members.forEach((m) => {
        if (m.id) {
          roles[m.id] = m.role;
        }
      });
      setMemberRoles(roles);
      setIsEditMode(false);
      setUserSearchQuery('');
      setDebouncedUserSearchQuery('');
      setUserPage(1);
    }
  }, [open, team]);

  const fetchUsers = useCallback(
    async (search = '', pageNum = 1, append = false) => {
      setLoadingUsers(true);
      try {
        const params: any = {
          page: pageNum,
          limit: 20,
        };
        if (search) {
          params.search = search;
        }

        const { data } = await axios.get('/api/v1/users/graph/list', { params });
        const rawUsers = data?.users || [];
        const normalizedUsers = rawUsers.map((user: any) => {
          const userId = user.id || user._key || user._id || '';
          return {
            ...user,
            _key: userId,
            _id: userId,
            id: userId,
            fullName: user.name || user.fullName || user.userName || '',
            name: user.name || user.fullName || user.userName || '',
            email: user.email || '',
          };
        });

        if (append) {
          setUsers((prev) => {
            const existingIds = new Set(prev.map((u) => u.id || u._key || u._id).filter(Boolean));
            const newUsers = normalizedUsers.filter((u: User) => {
              const userId = u.id || u._key || u._id;
              return userId && !existingIds.has(userId);
            });
            return [...prev, ...newUsers];
          });
        } else {
          setUsers(normalizedUsers);
        }

        if (data?.pagination) {
          setUserHasMore(data.pagination.hasNext || false);
        }
      } catch (err: any) {
        console.error('Error fetching users:', err);
      } finally {
        setLoadingUsers(false);
      }
    },
    []
  );

  useEffect(() => {
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    if (open && isEditMode) {
      searchTimeoutRef.current = setTimeout(() => {
        setDebouncedUserSearchQuery(userSearchQuery);
        setUserPage(1);
      }, 300);
    }

    return () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
      }
    };
  }, [userSearchQuery, open, isEditMode]);

  useEffect(() => {
    if (open && isEditMode) {
      fetchUsers(debouncedUserSearchQuery, 1, false);
    }
  }, [debouncedUserSearchQuery, open, isEditMode, fetchUsers]);

  const loadMoreUsers = () => {
    if (!loadingUsers && userHasMore) {
      const nextPage = userPage + 1;
      setUserPage(nextPage);
      fetchUsers(userSearchQuery, nextPage, true);
    }
  };

  const handleMemberRoleChange = (userId: string, role: string) => {
    setMemberRoles((prev) => ({
      ...prev,
      [userId]: role,
    }));
  };

  const handleBulkRoleSelect = (role: string) => {
    setBulkRoleSelected(role);
    setBulkRoleMenuAnchor(null);
  };

  const handleBulkRoleApply = () => {
    const newRoles: Record<string, string> = { ...memberRoles };
    teamMembers.forEach((member) => {
      const userId = member.id || member._key || member._id;
      if (userId) {
        const existingMember = team?.members.find((m) => m.id === userId);
        if (!existingMember?.isOwner) {
          newRoles[userId] = bulkRoleSelected;
        }
      }
    });
    setMemberRoles(newRoles);
    setTeamRole(bulkRoleSelected);
    setBulkRoleMenuAnchor(null);
  };

  const handleMemberAdd = (newMembers: User[]) => {
    setTeamMembers(newMembers);
    const newRoles: Record<string, string> = { ...memberRoles };
    newMembers.forEach((member) => {
      const userId = member.id || member._key || member._id;
      if (userId && !newRoles[userId]) {
        newRoles[userId] = teamRole;
      }
    });
    setMemberRoles(newRoles);
  };

  const handleRemoveMember = (member: User) => {
    const userId = member._key || member.id || member._id;
    setTeamMembers((prev) =>
      prev.filter((u) => {
        const userKey = u._key || u.id || u._id;
        return userKey !== userId;
      })
    );
    if (userId) {
      setMemberRoles((prev) => {
        const newRoles = { ...prev };
        delete newRoles[userId];
        return newRoles;
      });
    }
  };

  const handleSave = async () => {
    if (!teamName.trim() || !team) return;

    setSubmitting(true);
    try {
      const userRoles = teamMembers
        .map((member) => {
          const userId = member.id || member._key || member._id;
          if (!userId) return null;
          return {
            userId,
            role: (memberRoles[userId] || teamRole) as 'READER' | 'WRITER' | 'OWNER',
          };
        })
        .filter((ur): ur is { userId: string; role: 'READER' | 'WRITER' | 'OWNER' } => ur !== null);

      await onUpdate({
        name: teamName,
        description: teamDescription,
        role: teamRole,
        members: teamMembers,
        memberRoles: userRoles,
      });
      setIsEditMode(false);
      if (onTeamUpdated) {
        onTeamUpdated();
      }
    } catch (err: any) {
      onError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!team) return;
    setDeleting(true);
    try {
      await onDelete();
      handleClose();
    } catch (err: any) {
      onError(err.message);
    } finally {
      setDeleting(false);
    }
  };

  const handleClose = () => {
    if (!submitting && !deleting) {
      setIsEditMode(false);
      setUserSearchQuery('');
      setDebouncedUserSearchQuery('');
      setUserPage(1);
      setUsers([]);
      onClose();
    }
  };

  const availableUsers = users.filter((user) => {
    const userId = user.id || user._key || user._id;
    return !teamMembers.some((member) => {
      const memberId = member.id || member._key || member._id;
      return userId === memberId;
    });
  });

  if (!team) return null;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2.5,
          boxShadow: isDark 
            ? '0 24px 48px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)'
            : '0 20px 60px rgba(0, 0, 0, 0.12)',
          overflow: 'hidden',
          border: isDark ? '1px solid rgba(255, 255, 255, 0.08)' : 'none',
        },
      }}
      slotProps={{
        backdrop: {
          sx: {
            backgroundColor: isDark ? 'rgba(0, 0, 0, 0.35)' : 'rgba(0, 0, 0, 0.5)',
          },
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 3,
          py: 2.5,
          backgroundColor: 'transparent',
          flexShrink: 0,
          borderBottom: isDark 
            ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
            : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flex: 1 }}>
          <Box
            sx={{
              p: 1.25,
              borderRadius: 1.5,
              bgcolor: isDark 
                ? alpha(theme.palette.common.white, 0.08)
                : alpha(theme.palette.grey[100], 0.8),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              border: isDark ? `1px solid ${alpha(theme.palette.common.white, 0.1)}` : 'none',
            }}
          >
            <Icon icon={teamIcon} width={24} height={24} style={{ color: theme.palette.primary.main }} />
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            {isEditMode ? (
              <TextField
                value={teamName}
                onChange={(e) => setTeamName(e.target.value)}
                variant="standard"
                fullWidth
                sx={{
                  '& .MuiInputBase-root': {
                    fontSize: '1.125rem',
                    fontWeight: 600,
                    '&:before': { borderBottom: 'none' },
                    '&:after': { borderBottom: `2px solid ${theme.palette.primary.main}` },
                    '&:hover:not(.Mui-disabled):before': { borderBottom: 'none' },
                  },
                }}
              />
            ) : (
              <Typography
                variant="h6"
                sx={{ 
                  fontWeight: 600, 
                  mb: 0.5, 
                  color: theme.palette.text.primary,
                  fontSize: '1.125rem',
                  letterSpacing: '-0.01em',
                }}
              >
                {team.name}
              </Typography>
            )}
            {!isEditMode && team.description && (
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                {team.description}
              </Typography>
            )}
          </Box>
        </Box>

        <Stack direction="row" spacing={1} alignItems="center">
          {!isEditMode && team.canEdit && (
            <Tooltip title="Edit team" arrow>
              <IconButton
                onClick={() => setIsEditMode(true)}
                size="small"
                sx={{
                  color: isDark 
                    ? alpha(theme.palette.text.secondary, 0.8)
                    : theme.palette.text.secondary,
                  '&:hover': {
                    backgroundColor: isDark 
                      ? alpha(theme.palette.common.white, 0.1)
                      : alpha(theme.palette.text.secondary, 0.08),
                    color: theme.palette.primary.main,
                  },
                  transition: 'all 0.2s ease',
                }}
              >
                <Icon icon={editIcon} width={20} height={20} />
              </IconButton>
            </Tooltip>
          )}
          <IconButton
            onClick={handleClose}
            size="small"
            disabled={submitting || deleting}
            sx={{
              color: isDark 
                ? alpha(theme.palette.text.secondary, 0.8)
                : theme.palette.text.secondary,
              '&:hover': {
                backgroundColor: isDark 
                  ? alpha(theme.palette.common.white, 0.1)
                  : alpha(theme.palette.text.secondary, 0.08),
                color: theme.palette.text.primary,
              },
              transition: 'all 0.2s ease',
            }}
          >
            <Icon icon={closeIcon} width={20} height={20} />
          </IconButton>
        </Stack>
      </DialogTitle>

      <DialogContent 
        sx={{ 
          p: 0, 
          overflow: 'hidden', 
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <Box 
          sx={{ 
            px: 2.5,
            py: 2,
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
            overflow: 'auto',
            '&::-webkit-scrollbar': {
              width: '6px',
            },
            '&::-webkit-scrollbar-track': {
              backgroundColor: 'transparent',
            },
            '&::-webkit-scrollbar-thumb': {
              backgroundColor: isDark
                ? alpha(theme.palette.text.secondary, 0.25)
                : alpha(theme.palette.text.secondary, 0.16),
              borderRadius: '3px',
              '&:hover': {
                backgroundColor: isDark
                  ? alpha(theme.palette.text.secondary, 0.4)
                  : alpha(theme.palette.text.secondary, 0.24),
              },
            },
          }}
        >
          <Stack spacing={2.5}>
            {/* Team Information Section */}
            <Paper
              variant="outlined"
              sx={{
                p: 2,
                borderRadius: 1.25,
                bgcolor: isDark
                  ? alpha(theme.palette.background.paper, 0.4)
                  : theme.palette.background.paper,
                borderColor: isDark
                  ? alpha(theme.palette.divider, 0.12)
                  : alpha(theme.palette.divider, 0.1),
                boxShadow: isDark
                  ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
                  : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
                <Box
                  sx={{
                    p: 0.625,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.text.primary, 0.05),
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Icon icon={infoIcon} width={16} color={theme.palette.text.secondary} />
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="subtitle2"
                    sx={{
                      fontWeight: 600,
                      fontSize: '0.875rem',
                      color: theme.palette.text.primary,
                      lineHeight: 1.4,
                    }}
                  >
                    Team Information
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: '0.75rem',
                      color: theme.palette.text.secondary,
                      lineHeight: 1.3,
                    }}
                  >
                    {isEditMode ? 'Update team details' : 'View team details'}
                  </Typography>
                </Box>
              </Box>

              <Stack spacing={2}>
                {isEditMode ? (
                  <>
                    <TextField
                      label="Team Name"
                      placeholder="e.g., Engineering Team, Marketing Squad..."
                      value={teamName}
                      onChange={(e) => setTeamName(e.target.value)}
                      fullWidth
                      required
                      error={!teamName.trim() && teamName.length > 0}
                      helperText={!teamName.trim() && teamName.length > 0 ? 'Team name is required' : ''}
                      size="small"
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          bgcolor: alpha(theme.palette.background.paper, 0.8),
                          '&:hover': {
                            bgcolor: theme.palette.background.paper,
                            '& .MuiOutlinedInput-notchedOutline': {
                              borderColor: alpha(theme.palette.primary.main, 0.3),
                            },
                          },
                          '&.Mui-focused': {
                            bgcolor: theme.palette.background.paper,
                          },
                        },
                      }}
                    />

                    <TextField
                      label="Description (Optional)"
                      placeholder="Brief description of this team's purpose..."
                      value={teamDescription}
                      onChange={(e) => setTeamDescription(e.target.value)}
                      fullWidth
                      multiline
                      rows={2}
                      size="small"
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          bgcolor: alpha(theme.palette.background.paper, 0.8),
                          '&:hover': {
                            bgcolor: theme.palette.background.paper,
                            '& .MuiOutlinedInput-notchedOutline': {
                              borderColor: alpha(theme.palette.primary.main, 0.3),
                            },
                          },
                          '&.Mui-focused': {
                            bgcolor: theme.palette.background.paper,
                          },
                        },
                      }}
                    />

                    <FormControl fullWidth size="small">
                      <InputLabel sx={{ fontSize: '0.875rem', fontWeight: 500 }}>
                        Default Role for New Members
                      </InputLabel>
                      <Select
                        value={teamRole}
                        onChange={(e) => setTeamRole(e.target.value)}
                        label="Default Role for New Members"
                        sx={{
                          borderRadius: 1.25,
                          '& .MuiSelect-select': {
                            fontSize: '0.875rem',
                            fontWeight: 500,
                          },
                          backgroundColor: alpha(theme.palette.background.paper, 0.8),
                          transition: 'all 0.2s',
                          '&:hover': {
                            backgroundColor: theme.palette.background.paper,
                            '& .MuiOutlinedInput-notchedOutline': {
                              borderColor: alpha(theme.palette.primary.main, 0.3),
                            },
                          },
                          '&.Mui-focused': {
                            backgroundColor: theme.palette.background.paper,
                          },
                        }}
                      >
                        {roleOptions.map((option) => (
                          <MenuItem key={option.value} value={option.value} sx={{ fontSize: '0.875rem' }}>
                            <Box>
                              <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
                                {option.label}
                              </Typography>
                              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                                {option.description}
                              </Typography>
                            </Box>
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </>
                ) : (
                  <Stack spacing={1.5}>
                    <Box>
                      <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem', fontWeight: 600, mb: 0.5, display: 'block' }}>
                        Name
                      </Typography>
                      <Typography variant="body2" sx={{ fontSize: '0.9375rem', fontWeight: 500 }}>
                        {team.name}
                      </Typography>
                    </Box>
                    {team.description && (
                      <Box>
                        <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem', fontWeight: 600, mb: 0.5, display: 'block' }}>
                          Description
                        </Typography>
                        <Typography variant="body2" sx={{ fontSize: '0.875rem', color: 'text.secondary', lineHeight: 1.5 }}>
                          {team.description}
                        </Typography>
                      </Box>
                    )}
                    <Box>
                      <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem', fontWeight: 600, mb: 0.5, display: 'block' }}>
                        Created
                      </Typography>
                      <Typography variant="body2" sx={{ fontSize: '0.875rem', color: 'text.secondary' }}>
                        {new Date(team.createdAtTimestamp).toLocaleDateString('en-US', {
                          month: 'long',
                          day: 'numeric',
                          year: 'numeric',
                        })}
                      </Typography>
                    </Box>
                  </Stack>
                )}
              </Stack>
            </Paper>

            {/* Members Section */}
            <Paper
              variant="outlined"
              sx={{
                p: 2,
                borderRadius: 1.25,
                bgcolor: isDark
                  ? alpha(theme.palette.background.paper, 0.4)
                  : theme.palette.background.paper,
                borderColor: isDark
                  ? alpha(theme.palette.divider, 0.12)
                  : alpha(theme.palette.divider, 0.1),
                boxShadow: isDark
                  ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
                  : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
                <Box
                  sx={{
                    p: 0.625,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.primary.main, 0.1),
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Icon icon={personAddIcon} width={16} color={theme.palette.primary.main} />
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="subtitle2"
                    sx={{
                      fontWeight: 600,
                      fontSize: '0.875rem',
                      color: theme.palette.text.primary,
                      lineHeight: 1.4,
                    }}
                  >
                    Team Members
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: '0.75rem',
                      color: theme.palette.text.secondary,
                      lineHeight: 1.3,
                    }}
                  >
                    {isEditMode ? 'Manage team members and roles' : `${team.memberCount} ${team.memberCount === 1 ? 'member' : 'members'}`}
                  </Typography>
                </Box>
                {teamMembers.length > 0 && (
                  <Chip
                    label={teamMembers.length}
                    size="small"
                    sx={{
                      height: 22,
                      fontWeight: 700,
                      fontSize: '0.75rem',
                      bgcolor: alpha(theme.palette.primary.main, 0.12),
                      color: theme.palette.primary.main,
                    }}
                  />
                )}
                {isEditMode && teamMembers.length > 0 && (
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Tooltip title="Select a role to apply to all members" arrow>
                      <Button
                        variant="outlined"
                        size="small"
                        endIcon={<Icon icon={settingsIcon} width={14} height={14} />}
                        onClick={(e) => setBulkRoleMenuAnchor(e.currentTarget)}
                        sx={{
                          height: 32,
                          px: 1.5,
                          fontSize: '0.75rem',
                          fontWeight: 500,
                          textTransform: 'none',
                          borderColor: theme.palette.divider,
                          color: 'text.secondary',
                          minWidth: 120,
                          justifyContent: 'space-between',
                          '&:hover': {
                            borderColor: theme.palette.primary.main,
                            bgcolor: alpha(theme.palette.primary.main, 0.08),
                            color: theme.palette.primary.main,
                          },
                        }}
                      >
                        {roleOptions.find((r) => r.value === bulkRoleSelected)?.label || 'Select Role'}
                      </Button>
                    </Tooltip>
                    <Menu
                      anchorEl={bulkRoleMenuAnchor}
                      open={Boolean(bulkRoleMenuAnchor)}
                      onClose={() => setBulkRoleMenuAnchor(null)}
                      PaperProps={{
                        sx: {
                          mt: 0.5,
                          minWidth: 180,
                          borderRadius: 1.25,
                          boxShadow: isDark
                            ? '0 8px 24px rgba(0, 0, 0, 0.4)'
                            : '0 8px 24px rgba(0, 0, 0, 0.12)',
                          border: isDark
                            ? `1px solid ${alpha(theme.palette.divider, 0.2)}`
                            : 'none',
                        },
                      }}
                    >
                      {roleOptions.map((option) => (
                        <MenuItem
                          key={option.value}
                          onClick={() => handleBulkRoleSelect(option.value)}
                          selected={bulkRoleSelected === option.value}
                          sx={{
                            fontSize: '0.8125rem',
                            py: 1,
                            px: 1.5,
                            '&.Mui-selected': {
                              bgcolor: alpha(theme.palette.primary.main, 0.12),
                              '&:hover': {
                                bgcolor: alpha(theme.palette.primary.main, 0.2),
                              },
                            },
                          }}
                        >
                          <Box>
                            <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.8125rem' }}>
                              {option.label}
                            </Typography>
                            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                              {option.description}
                            </Typography>
                          </Box>
                        </MenuItem>
                      ))}
                    </Menu>
                    <Tooltip title="Apply selected role to all members (except owners)" arrow>
                      <span>
                        <Button
                          variant="contained"
                          size="small"
                          onClick={handleBulkRoleApply}
                          disabled={teamMembers.length === 0}
                          sx={{
                            height: 32,
                            px: 2,
                            fontSize: '0.75rem',
                            fontWeight: 600,
                            textTransform: 'none',
                            boxShadow: 'none',
                            '&:hover': {
                              boxShadow: isDark
                                ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}`
                                : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
                            },
                            '&:disabled': {
                              opacity: 0.5,
                            },
                          }}
                        >
                          Apply to All
                        </Button>
                      </span>
                    </Tooltip>
                  </Stack>
                )}
              </Box>

              {teamMembers.length === 0 ? (
                <Box
                  sx={{
                    p: 3,
                    textAlign: 'center',
                    borderRadius: 1.25,
                    bgcolor: isDark
                      ? alpha(theme.palette.background.default, 0.3)
                      : alpha(theme.palette.background.default, 0.5),
                    border: `1px dashed ${alpha(theme.palette.divider, 0.3)}`,
                  }}
                >
                  <Box
                    sx={{
                      width: 48,
                      height: 48,
                      borderRadius: 1.5,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      bgcolor: alpha(theme.palette.text.secondary, 0.08),
                      mx: 'auto',
                      mb: 1.5,
                    }}
                  >
                    <Icon icon={personAddIcon} width={24} height={24} style={{ color: theme.palette.text.secondary }} />
                  </Box>
                  <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 500, fontSize: '0.875rem' }}>
                    No members in this team yet
                  </Typography>
                  {isEditMode && (
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                      Add members below to get started
                    </Typography>
                  )}
                </Box>
              ) : (
                <Box
                  sx={{
                    borderRadius: 1.25,
                    bgcolor: isDark
                      ? alpha(theme.palette.background.default, 0.2)
                      : alpha(theme.palette.background.default, 0.3),
                    maxHeight: isEditMode ? 280 : 320,
                    overflow: 'auto',
                    border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                  }}
                >
                  <List sx={{ py: 0.5 }}>
                    {teamMembers.map((member, index) => {
                      const userId = member.id || member._key || member._id || '';
                      const existingMember = team?.members.find((m) => m.id === userId);
                      const currentRole = memberRoles[userId] || existingMember?.role || teamRole;
                      const isOwner = existingMember?.isOwner || false;

                      return (
                        <React.Fragment key={userId || index}>
                          <ListItem
                            sx={{
                              py: 1.25,
                              px: 2,
                              '&:hover': {
                                bgcolor: alpha(theme.palette.action.hover, 0.5),
                              },
                            }}
                            secondaryAction={
                              isEditMode ? (
                                <Stack direction="row" spacing={1} alignItems="center">
                                  <FormControl size="small" sx={{ minWidth: 100 }}>
                                    <Select
                                      value={currentRole}
                                      onChange={(e) => handleMemberRoleChange(userId, e.target.value)}
                                      disabled={isOwner}
                                      sx={{
                                        height: 32,
                                        fontSize: '0.75rem',
                                        '& .MuiSelect-select': {
                                          py: 0.5,
                                          px: 1,
                                        },
                                      }}
                                    >
                                      {roleOptions.map((option) => (
                                        <MenuItem key={option.value} value={option.value} sx={{ fontSize: '0.75rem' }}>
                                          {option.label}
                                        </MenuItem>
                                      ))}
                                    </Select>
                                  </FormControl>
                                  {!isOwner && (
                                    <IconButton
                                      edge="end"
                                      size="small"
                                      onClick={() => handleRemoveMember(member)}
                                      sx={{
                                        width: 32,
                                        height: 32,
                                        color: theme.palette.error.main,
                                        bgcolor: alpha(theme.palette.error.main, 0.08),
                                        '&:hover': {
                                          bgcolor: alpha(theme.palette.error.main, 0.16),
                                        },
                                      }}
                                    >
                                      <Icon icon={deleteIcon} width={16} height={16} />
                                    </IconButton>
                                  )}
                                </Stack>
                              ) : (
                                <Chip
                                  label={currentRole}
                                  size="small"
                                  sx={{
                                    height: 24,
                                    fontSize: '0.75rem',
                                    fontWeight: 600,
                                    bgcolor: isOwner
                                      ? alpha(theme.palette.warning.main, 0.12)
                                      : alpha(theme.palette.primary.main, 0.12),
                                    color: isOwner
                                      ? theme.palette.warning.main
                                      : theme.palette.primary.main,
                                  }}
                                />
                              )
                            }
                          >
                            <ListItemAvatar>
                              <Avatar
                                sx={{
                                  width: 36,
                                  height: 36,
                                  fontSize: '0.75rem',
                                  fontWeight: 600,
                                  bgcolor: getAvatarColor(member.fullName || member.email || 'U'),
                                }}
                              >
                                {getInitials(member.fullName || member.email || 'U')}
                              </Avatar>
                            </ListItemAvatar>
                            <ListItemText
                              primary={
                                <Stack direction="row" spacing={1} alignItems="center">
                                  <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
                                    {member.fullName || member.name || member.email}
                                  </Typography>
                                  {isOwner && (
                                    <Chip
                                      label="Owner"
                                      size="small"
                                      sx={{
                                        height: 18,
                                        fontSize: '0.6875rem',
                                        fontWeight: 600,
                                        bgcolor: alpha(theme.palette.warning.main, 0.12),
                                        color: theme.palette.warning.main,
                                      }}
                                    />
                                  )}
                                </Stack>
                              }
                              secondary={
                                member.email && member.fullName ? (
                                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                                    {member.email}
                                  </Typography>
                                ) : null
                              }
                            />
                          </ListItem>
                          {index < teamMembers.length - 1 && <Divider component="li" />}
                        </React.Fragment>
                      );
                    })}
                  </List>
                </Box>
              )}

              {/* Add Members Section - Only in Edit Mode */}
              {isEditMode && (
                <Box sx={{ mt: 2 }}>
                  <Autocomplete
                    multiple
                    options={availableUsers}
                    loading={loadingUsers}
                    value={[]}
                    onChange={(_, newValue) => {
                      const existingIds = new Set(teamMembers.map((m) => m.id || m._key || m._id).filter(Boolean));
                      const toAdd = newValue.filter((u) => {
                        const userId = u.id || u._key || u._id;
                        return userId && !existingIds.has(userId);
                      });
                      handleMemberAdd([...teamMembers, ...toAdd]);
                    }}
                    onInputChange={(_, newInputValue, reason) => {
                      if (reason === 'input') {
                        setUserSearchQuery(newInputValue);
                      }
                    }}
                    getOptionLabel={(option) => option.fullName || option.name || option.email || 'User'}
                    isOptionEqualToValue={(option, value) => {
                      const optId = option.id || option._key || option._id;
                      const valId = value.id || value._key || value._id;
                      return optId === valId;
                    }}
                    renderInput={(params) => (
                      <TextField
                        {...params}
                        placeholder="Search and select users to add..."
                        size="small"
                        InputProps={{
                          ...params.InputProps,
                          startAdornment: (
                            <>
                              <InputAdornment position="start" sx={{ ml: 0.5 }}>
                                <Icon icon={searchIcon} width={16} height={16} style={{ color: theme.palette.text.secondary }} />
                              </InputAdornment>
                              {params.InputProps.startAdornment}
                            </>
                          ),
                        }}
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            borderRadius: 1.25,
                            bgcolor: alpha(theme.palette.background.paper, 0.8),
                            '&:hover': {
                              bgcolor: theme.palette.background.paper,
                              '& .MuiOutlinedInput-notchedOutline': {
                                borderColor: alpha(theme.palette.primary.main, 0.3),
                              },
                            },
                            '&.Mui-focused': {
                              bgcolor: theme.palette.background.paper,
                            },
                          },
                        }}
                      />
                    )}
                    renderOption={(props, option) => {
                      const userId = option.id || option._key || option._id;
                      const isSelected = teamMembers.some((m) => {
                        const mId = m.id || m._key || m._id;
                        return mId === userId;
                      });
                      return (
                        <li {...props} key={userId} style={{ opacity: isSelected ? 0.5 : 1 }}>
                          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ py: 0.5, width: '100%' }}>
                            <Avatar
                              sx={{
                                width: 32,
                                height: 32,
                                fontSize: '0.75rem',
                                fontWeight: 600,
                                bgcolor: getAvatarColor(option.fullName || option.name || option.email || 'U'),
                              }}
                            >
                              {getInitials(option.fullName || option.name || option.email || 'U')}
                            </Avatar>
                            <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                              <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
                                {option.fullName || option.name || option.email}
                              </Typography>
                              {(option.fullName || option.name) && option.email && (
                                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                                  {option.email}
                                </Typography>
                              )}
                            </Box>
                            {isSelected && (
                              <Chip label="Added" size="small" sx={{ height: 20, fontSize: '0.6875rem' }} />
                            )}
                          </Stack>
                        </li>
                      );
                    }}
                    ListboxProps={{
                      style: { maxHeight: 240 },
                      onScroll: (e: any) => {
                        const { target } = e;
                        if (
                          target.scrollTop + target.clientHeight >= target.scrollHeight - 10 &&
                          userHasMore &&
                          !loadingUsers
                        ) {
                          loadMoreUsers();
                        }
                      },
                    }}
                    onOpen={() => {
                      if (!loadingUsers && users.length === 0) {
                        fetchUsers('', 1, false);
                      }
                    }}
                    filterOptions={(options) => options}
                    noOptionsText={
                      loadingUsers
                        ? 'Loading users...'
                        : availableUsers.length === 0 && users.length > 0
                        ? 'All users are already members'
                        : debouncedUserSearchQuery
                        ? `No users found matching "${debouncedUserSearchQuery}"`
                        : 'Start typing to search users'
                    }
                    loadingText="Loading users..."
                  />

                  <Alert
                    variant="outlined"
                    severity="info"
                    sx={{
                      mt: 1.5,
                      borderRadius: 1.25,
                      py: 1,
                      px: 1.75,
                      fontSize: '0.875rem',
                      '& .MuiAlert-icon': { fontSize: '1.25rem', py: 0.5 },
                      '& .MuiAlert-message': { py: 0.25 },
                      alignItems: 'center',
                      bgcolor: isDark
                        ? alpha(theme.palette.info.main, 0.08)
                        : alpha(theme.palette.info.main, 0.02),
                      borderColor: isDark
                        ? alpha(theme.palette.info.main, 0.25)
                        : alpha(theme.palette.info.main, 0.1),
                    }}
                  >
                    <Typography variant="body2" sx={{ fontSize: '0.8125rem', lineHeight: 1.5, fontWeight: 400 }}>
                      New members will be added with the default role. Remove members by clicking the delete icon next to their name above.
                    </Typography>
                  </Alert>
                </Box>
              )}
            </Paper>
          </Stack>
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          px: 2.5,
          py: 2,
          borderTop: isDark
            ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
            : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          flexShrink: 0,
        }}
      >
        <Box sx={{ display: 'flex', gap: 1.5, width: '100%', justifyContent: 'space-between' }}>
          <Box>
            {!isEditMode && team.canDelete && (
              <Button
                onClick={handleDelete}
                disabled={deleting}
                variant="outlined"
                color="error"
                startIcon={deleting ? <CircularProgress size={14} /> : <Icon icon={deleteIcon} width={14} height={14} />}
                sx={{
                  textTransform: 'none',
                  fontWeight: 500,
                  px: 2.5,
                  py: 0.625,
                  borderRadius: 1,
                  fontSize: '0.8125rem',
                }}
              >
                {deleting ? 'Deleting...' : 'Delete Team'}
              </Button>
            )}
          </Box>
          <Stack direction="row" spacing={1.5}>
            {isEditMode && (
              <Button
                onClick={() => {
                  setIsEditMode(false);
                  // Reset to original values
                  if (team) {
                    setTeamName(team.name);
                    setTeamDescription(team.description || '');
                    const membersAsUsers: User[] = team.members.map((m) => {
                      const memberId = m.id || m.userId || '';
                      return {
                        _key: memberId,
                        _id: memberId,
                        id: memberId,
                        userId: m.userId,
                        fullName: m.userName || '',
                        name: m.userName || '',
                        email: m.userEmail || '',
                      };
                    });
                    setTeamMembers(membersAsUsers);
                    const roles: Record<string, string> = {};
                    team.members.forEach((m) => {
                      if (m.id) {
                        roles[m.id] = m.role;
                      }
                    });
                    setMemberRoles(roles);
                  }
                }}
                disabled={submitting}
                variant="outlined"
                sx={{
                  textTransform: 'none',
                  fontWeight: 500,
                  px: 2.5,
                  py: 0.625,
                  borderRadius: 1,
                  fontSize: '0.8125rem',
                  borderColor: isDark
                    ? alpha(theme.palette.divider, 0.3)
                    : alpha(theme.palette.divider, 0.2),
                  color: isDark
                    ? alpha(theme.palette.text.secondary, 0.9)
                    : theme.palette.text.secondary,
                  '&:hover': {
                    borderColor: isDark
                      ? alpha(theme.palette.text.secondary, 0.5)
                      : alpha(theme.palette.text.secondary, 0.4),
                    backgroundColor: isDark
                      ? alpha(theme.palette.common.white, 0.08)
                      : alpha(theme.palette.text.secondary, 0.04),
                  },
                  transition: 'all 0.2s ease',
                }}
              >
                Cancel
              </Button>
            )}
            <Button
              onClick={isEditMode ? handleSave : handleClose}
              disabled={isEditMode ? (submitting || !teamName.trim()) : false}
              variant={isEditMode ? 'contained' : 'outlined'}
              startIcon={
                isEditMode && submitting ? (
                  <CircularProgress size={14} color="inherit" />
                ) : isEditMode ? (
                  <Icon icon={editIcon} width={14} height={14} />
                ) : null
              }
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                px: 3,
                py: 0.625,
                borderRadius: 1,
                fontSize: '0.8125rem',
                ...(isEditMode
                  ? {
                      boxShadow: isDark
                        ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}`
                        : 'none',
                      '&:hover': {
                        boxShadow: isDark
                          ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}`
                          : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
                      },
                    }
                  : {
                      borderColor: isDark
                        ? alpha(theme.palette.divider, 0.3)
                        : alpha(theme.palette.divider, 0.2),
                      color: isDark
                        ? alpha(theme.palette.text.secondary, 0.9)
                        : theme.palette.text.secondary,
                      '&:hover': {
                        borderColor: isDark
                          ? alpha(theme.palette.text.secondary, 0.5)
                          : alpha(theme.palette.text.secondary, 0.4),
                        backgroundColor: isDark
                          ? alpha(theme.palette.common.white, 0.08)
                          : alpha(theme.palette.text.secondary, 0.04),
                      },
                    }),
                transition: 'all 0.2s ease',
              }}
            >
              {isEditMode ? (submitting ? 'Saving...' : 'Save Changes') : 'Close'}
            </Button>
          </Stack>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default TeamDetailsDialog;

