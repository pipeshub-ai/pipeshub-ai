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
import addIcon from '@iconify-icons/eva/plus-fill';
import searchIcon from '@iconify-icons/eva/search-fill';
import closeIcon from '@iconify-icons/mdi/close';
import infoIcon from '@iconify-icons/eva/info-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import settingsIcon from '@iconify-icons/mdi/settings-outline';
import { User, TeamFormData, RoleOption, TeamRole as TeamRoleType } from '../../types/teams';

interface CreateTeamDialogProps {
  open: boolean;
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

const CreateTeamDialog: React.FC<CreateTeamDialogProps> = ({
  open,
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
        newRoles[userId] = bulkRoleSelected;
      }
    });
    setMemberRoles(newRoles);
    setTeamRole(bulkRoleSelected);
    setBulkRoleMenuAnchor(null);
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

  const handleMemberRemove = (memberToRemove: User) => {
    const userId = memberToRemove.id || memberToRemove._key || memberToRemove._id;
    setTeamMembers((prev) => prev.filter((m) => {
      const mId = m.id || m._key || m._id;
      return mId !== userId;
    }));
    if (userId) {
      setMemberRoles((prev) => {
        const newRoles = { ...prev };
        delete newRoles[userId];
        return newRoles;
      });
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
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
            <Icon icon={addIcon} width={24} height={24} style={{ color: theme.palette.primary.main }} />
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
              Create New Team
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
              Set up a new team and add members
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
                    Enter basic details for your team
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
                  autoFocus
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

              </Stack>
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
                    bgcolor: alpha(theme.palette.primary.main, 0.1),
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Icon icon={addIcon} width={16} color={theme.palette.primary.main} />
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
                    Add members to your team
                  </Typography>
                </Box>
                {teamMembers.length > 0 && (
                  <Box
                    sx={{
                      px: 1,
                      py: 0.25,
                      borderRadius: 1,
                      bgcolor: alpha(theme.palette.primary.main, 0.12),
                    }}
                  >
                    <Typography variant="caption" sx={{ color: theme.palette.primary.main, fontWeight: 600, fontSize: '0.75rem' }}>
                      {teamMembers.length} selected
                    </Typography>
                  </Box>
                )}
              </Box>

              {/* Selected Members List with Role Dropdowns */}
              {teamMembers.length > 0 && (
                <Box sx={{ mb: 2 }}>
                  <Stack direction="row" spacing={1.5} alignItems="center" justifyContent="space-between" sx={{ mb: 1.5 }}>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="caption" sx={{ fontWeight: 600, fontSize: '0.75rem', color: 'text.secondary' }}>
                        Selected Members
                      </Typography>
                      <Chip
                        label={teamMembers.length}
                        size="small"
                        sx={{
                          height: 20,
                          fontSize: '0.6875rem',
                          fontWeight: 600,
                          bgcolor: alpha(theme.palette.primary.main, 0.12),
                          color: theme.palette.primary.main,
                        }}
                      />
                    </Stack>
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
                      <Tooltip title="Apply selected role to all members" arrow>
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
                  <Box
                    sx={{
                      borderRadius: 1.25,
                      bgcolor: isDark
                        ? alpha(theme.palette.background.default, 0.2)
                        : alpha(theme.palette.background.default, 0.3),
                      border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                      maxHeight: 200,
                      overflow: 'auto',
                    }}
                  >
                    <List sx={{ py: 0.5 }}>
                      {teamMembers.map((member, index) => {
                        const userId = member.id || member._key || member._id || '';
                        const currentRole = memberRoles[userId] || teamRole;
                        return (
                          <React.Fragment key={userId || index}>
                            <ListItem
                              sx={{
                                py: 1,
                                px: 1.5,
                                '&:hover': {
                                  bgcolor: alpha(theme.palette.action.hover, 0.5),
                                },
                              }}
                              secondaryAction={
                                <Stack direction="row" spacing={1} alignItems="center">
                                  <FormControl size="small" sx={{ minWidth: 100 }}>
                                    <Select
                                      value={currentRole}
                                      onChange={(e) => handleMemberRoleChange(userId, e.target.value)}
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
                                  <IconButton
                                    size="small"
                                    onClick={() => handleMemberRemove(member)}
                                    sx={{
                                      width: 28,
                                      height: 28,
                                      color: theme.palette.error.main,
                                      '&:hover': {
                                        bgcolor: alpha(theme.palette.error.main, 0.08),
                                      },
                                    }}
                                  >
                                    <Icon icon={deleteIcon} width={14} height={14} />
                                  </IconButton>
                                </Stack>
                              }
                            >
                              <ListItemAvatar>
                                <Avatar
                                  sx={{
                                    width: 32,
                                    height: 32,
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
                        );
                      })}
                    </List>
                  </Box>
                </Box>
              )}

              {/* Add Members Autocomplete */}
              <Autocomplete
                multiple
                options={users}
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
                    placeholder="Search users to add..."
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
                  Set individual roles for each member or use bulk edit to set the same role for all selected members.
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
                <Icon icon={addIcon} width={14} height={14} />
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
            {submitting ? 'Creating...' : 'Create Team'}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default CreateTeamDialog;
