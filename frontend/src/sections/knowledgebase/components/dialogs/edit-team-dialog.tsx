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
  Divider,
  List,
  ListItem,
  ListItemAvatar,
  ListItemText,
  Paper,
  Chip,
  Alert,
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
import { User, Team, TeamFormData, RoleOption, TeamRole } from '../../types/teams';

interface EditTeamDialogProps {
  open: boolean;
  team: Team | null;
  onClose: () => void;
  onSubmit: (formData: TeamFormData) => Promise<void>;
  onSuccess: (message: string) => void;
  onError: (message: string) => void;
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

const EditTeamDialog: React.FC<EditTeamDialogProps> = ({
  open,
  team,
  onClose,
  onSubmit,
  onSuccess,
  onError,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const searchTimeoutRef = useRef<NodeJS.Timeout>();

  const [teamName, setTeamName] = useState('');
  const [teamDescription, setTeamDescription] = useState('');
  const [teamRole, setTeamRole] = useState<string>('READER');
  const [teamMembers, setTeamMembers] = useState<User[]>([]);
  const [memberRoles, setMemberRoles] = useState<Record<string, string>>({}); // userId -> role
  const [bulkRoleSelected, setBulkRoleSelected] = useState<string>('READER'); // Selected but not yet applied
  const [bulkRoleMenuAnchor, setBulkRoleMenuAnchor] = useState<null | HTMLElement>(null);
  const [submitting, setSubmitting] = useState(false);

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
      
      // Initialize member roles from existing team members
      const roles: Record<string, string> = {};
      team.members.forEach((m) => {
        if (m.id) {
          roles[m.id] = m.role;
        }
      });
      setMemberRoles(roles);
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

    if (open) {
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
  }, [userSearchQuery, open]);

  useEffect(() => {
    if (open) {
      fetchUsers(debouncedUserSearchQuery, 1, false);
    }
  }, [debouncedUserSearchQuery, open, fetchUsers]);

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
        // Don't change owner's role
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
    // Set default role for newly added members
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

  const handleSubmit = async () => {
    if (!teamName.trim()) return;

    setSubmitting(true);
    try {
      // Build memberRoles array from teamMembers and memberRoles state
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

      await onSubmit({
        name: teamName,
        description: teamDescription,
        role: teamRole,
        members: teamMembers,
        memberRoles: userRoles,
      });
      handleClose();
    } catch (err: any) {
      onError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = () => {
    if (!submitting) {
      setTeamName('');
      setTeamDescription('');
      setTeamRole('READER');
      setTeamMembers([]);
      setMemberRoles({});
      setBulkRoleSelected('READER');
      setBulkRoleMenuAnchor(null);
      setUserSearchQuery('');
      setDebouncedUserSearchQuery('');
      setUserPage(1);
      setUsers([]);
      onClose();
    }
  };

  // Filter out users who are already members
  const availableUsers = users.filter((user) => {
    const userId = user.id || user._key || user._id;
    return !teamMembers.some((member) => {
      const memberId = member.id || member._key || member._id;
      return userId === memberId;
    });
  });

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
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
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
            <Icon icon={editIcon} width={24} height={24} style={{ color: theme.palette.primary.main }} />
          </Box>
          <Box>
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
              Edit Team
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
              Update team details and manage members
            </Typography>
          </Box>
        </Box>

        <IconButton
          onClick={handleClose}
          size="small"
          disabled={submitting}
          sx={{
            color: isDark 
              ? alpha(theme.palette.text.secondary, 0.8)
              : theme.palette.text.secondary,
            p: 1,
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
            {/* Team Details Section */}
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
                    Update basic details for your team
                  </Typography>
                </Box>
              </Box>

              <Stack spacing={2}>
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
              </Stack>
            </Paper>

            {/* Current Members Section */}
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
                    Current Members
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: '0.75rem',
                      color: theme.palette.text.secondary,
                      lineHeight: 1.3,
                    }}
                  >
                    Manage existing team members
                  </Typography>
                </Box>
                {teamMembers.length > 0 && (
                  <Stack direction="row" spacing={1.5} alignItems="center">
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
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                    Add members below to get started
                  </Typography>
                </Box>
              ) : (
                <Box
                  sx={{
                    borderRadius: 1.25,
                    bgcolor: isDark
                      ? alpha(theme.palette.background.default, 0.2)
                      : alpha(theme.palette.background.default, 0.3),
                    maxHeight: 280,
                    overflow: 'auto',
                    border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                  }}
                >
                  <List sx={{ py: 0.5 }}>
                    {teamMembers.map((member, index) => (
                      <React.Fragment key={member._key || member.id || index}>
                        <ListItem
                          sx={{
                            py: 1.25,
                            px: 2,
                            '&:hover': {
                              bgcolor: alpha(theme.palette.action.hover, 0.5),
                            },
                          }}
                          secondaryAction={
                            <Stack direction="row" spacing={1} alignItems="center">
                              <FormControl size="small" sx={{ minWidth: 100 }}>
                                <Select
                                  value={memberRoles[member.id || member._key || member._id || ''] || (team?.members.find((m) => m.id === (member.id || member._key || member._id))?.role) || teamRole}
                                  onChange={(e) => handleMemberRoleChange(member.id || member._key || member._id || '', e.target.value)}
                                  disabled={team?.members.find((m) => m.id === (member.id || member._key || member._id))?.isOwner || false}
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
                              {!(team?.members.find((m) => m.id === (member.id || member._key || member._id))?.isOwner) && (
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
                              <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
                                {member.fullName || member.name || member.email}
                              </Typography>
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
                    ))}
                  </List>
                </Box>
              )}
            </Paper>

            {/* Add Members Section */}
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
                    bgcolor: alpha(theme.palette.success.main, 0.1),
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Icon icon={personAddIcon} width={16} color={theme.palette.success.main} />
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
                    Add New Members
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: '0.75rem',
                      color: theme.palette.text.secondary,
                      lineHeight: 1.3,
                    }}
                  >
                    Search and add users to your team
                  </Typography>
                </Box>
              </Box>

              <Autocomplete
                multiple
                options={availableUsers}
                loading={loadingUsers}
                value={[]}
              getOptionDisabled={(option) => {
                const userId = option.id || option._key || option._id;
                return teamMembers.some((m) => {
                  const mId = m.id || m._key || m._id;
                  return mId === userId;
                });
              }}
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
        <Box sx={{ display: 'flex', gap: 1.5, width: '100%', justifyContent: 'flex-end' }}>
          <Button
            onClick={handleClose}
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

          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={submitting || !teamName.trim()}
            startIcon={
              submitting ? (
                <CircularProgress size={14} color="inherit" />
              ) : (
                <Icon icon={editIcon} width={14} height={14} />
              )
            }
            sx={{
              textTransform: 'none',
              fontWeight: 500,
              px: 3,
              py: 0.625,
              borderRadius: 1,
              fontSize: '0.8125rem',
              boxShadow: isDark
                ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}`
                : 'none',
              '&:hover': {
                boxShadow: isDark
                  ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}`
                  : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
              },
              '&:active': {
                boxShadow: 'none',
              },
              '&:disabled': {
                boxShadow: 'none',
              },
              transition: 'all 0.2s ease',
            }}
          >
            {submitting ? 'Saving...' : 'Save Changes'}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default EditTeamDialog;
