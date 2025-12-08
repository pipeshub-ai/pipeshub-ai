import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Drawer,
  Box,
  Stack,
  Typography,
  IconButton,
  TextField,
  Button,
  Avatar,
  Paper,
  Divider,
  Tooltip,
  CircularProgress,
  InputAdornment,
  alpha,
  useTheme,
  List,
  ListItem,
  ListItemAvatar,
  ListItemText,
  ListItemSecondaryAction,
  TablePagination,
  Chip,
  Skeleton,
} from '@mui/material';
import { Icon } from '@iconify/react';
import axios from 'src/utils/axios';
import closeIcon from '@iconify-icons/mdi/close';
import teamIcon from '@iconify-icons/mdi/account-group';
import searchIcon from '@iconify-icons/eva/search-fill';
import addIcon from '@iconify-icons/eva/plus-fill';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import refreshIcon from '@iconify-icons/eva/refresh-fill';
import personAddIcon from '@iconify-icons/eva/person-add-fill';
import { createScrollableContainerStyle } from 'src/sections/qna/chatbot/utils/styles/scrollbar';
import CreateTeamDialog from './dialogs/create-team-dialog';
import EditTeamDialog from './dialogs/edit-team-dialog';
import DeleteTeamDialog from './dialogs/delete-team-dialog';
import TeamDetailsDialog from './dialogs/team-details-dialog';
import { Team, User, TeamFormData } from '../types/teams';

interface TeamManagementSliderProps {
  open: boolean;
  onClose: () => void;
}

const getInitials = (fullName: string) =>
  fullName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

const TeamManagementSlider: React.FC<TeamManagementSliderProps> = ({ open, onClose }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const scrollableStyle = createScrollableContainerStyle(theme);
  const teamSearchTimeoutRef = useRef<NodeJS.Timeout>();

  // State
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [detailsDialogOpen, setDetailsDialogOpen] = useState(false);
  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);

  // Pagination state
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [totalCount, setTotalCount] = useState(0);

  // Messages
  const [success, setSuccess] = useState('');
  const [error, setError] = useState('');

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

  const fetchTeams = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = {
        page: page + 1,
        limit: rowsPerPage,
      };
      if (debouncedSearchQuery) {
        params.search = debouncedSearchQuery;
      }
      const { data } = await axios.get('/api/v1/teams/user/teams', { params });
      setTeams(data?.teams || []);
      if (data?.pagination) {
        setTotalCount(data.pagination.total || 0);
      }
    } catch (err: any) {
      console.error('Error fetching teams:', err);
      setError(err.message || 'Failed to fetch teams');
      setTeams([]);
    } finally {
      setLoading(false);
    }
  }, [page, rowsPerPage, debouncedSearchQuery]);

  // Debounced team search
  useEffect(() => {
    if (teamSearchTimeoutRef.current) {
      clearTimeout(teamSearchTimeoutRef.current);
    }

    teamSearchTimeoutRef.current = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery);
      setPage(0);
    }, 300);

    return () => {
      if (teamSearchTimeoutRef.current) {
        clearTimeout(teamSearchTimeoutRef.current);
      }
    };
  }, [searchQuery]);

  useEffect(() => {
    if (open) {
      fetchTeams();
    }
  }, [open, page, rowsPerPage, debouncedSearchQuery, fetchTeams]);

  const handleCreateTeam = async (formData: TeamFormData) => {
    try {
      const body: any = {
        name: formData.name.trim(),
      };

      if (formData.description?.trim()) {
        body.description = formData.description.trim();
      }

      // Use new format with individual user roles if available
      if (formData.memberRoles && formData.memberRoles.length > 0) {
        body.userRoles = formData.memberRoles;
      } else if (formData.members && formData.members.length > 0) {
        // Legacy format: single role for all users
        const validUserIds = formData.members
          .map((u) => u._key || u.id || u._id)
          .filter((id) => id != null && typeof id === 'string' && id.trim() !== '');
        if (validUserIds.length > 0) {
          body.userIds = validUserIds;
          body.role = formData.role;
        }
      }

      await axios.post('/api/v1/teams', body);
      setSuccess('Team created successfully');
      setCreateDialogOpen(false);
      fetchTeams();
    } catch (err: any) {
      console.error('Error creating team:', err);
      throw new Error(err.response?.data?.message || err.message || 'Failed to create team');
    }
  };

  const handleUpdateTeam = async (formData: TeamFormData) => {
    if (!selectedTeam) return;

    try {
      // Calculate current and new member IDs
      const currentMemberIds = new Set(
        selectedTeam.members.map((m) => m.id).filter(Boolean)
      );
      const newMemberIds = new Set(
        formData.members
          .map((u) => u.id || u._key || u._id)
          .filter((id) => id && typeof id === 'string' && id.trim() !== '')
      );

      const toAdd = Array.from(newMemberIds).filter((id) => !currentMemberIds.has(id));
      const toRemove = Array.from(currentMemberIds).filter((id) => !newMemberIds.has(id));

      // Prepare update body with name, description, and member changes
      const updateBody: any = {
        name: formData.name.trim(),
      };

      if (formData.description?.trim()) {
        updateBody.description = formData.description.trim();
      }

      // Handle member additions with individual roles
      if (toAdd.length > 0) {
        if (formData.memberRoles && formData.memberRoles.length > 0) {
          // Use new format: individual roles for new members
          updateBody.addUserRoles = formData.memberRoles.filter((ur) => toAdd.includes(ur.userId));
        } else {
          // Legacy format: single role for all new members
          updateBody.addUserIds = toAdd;
          updateBody.role = formData.role;
        }
      }

      // Handle member removals
      if (toRemove.length > 0) {
        updateBody.removeUserIds = toRemove;
      }

      // Handle role updates for existing members
      if (formData.memberRoles && formData.memberRoles.length > 0) {
        const existingMemberIds = Array.from(currentMemberIds).filter((id) => !toAdd.includes(id) && !toRemove.includes(id));
        const roleUpdates = formData.memberRoles.filter((ur) => {
          // Check if role changed for existing members
          const existingMember = selectedTeam.members.find((m) => m.id === ur.userId);
          return existingMember && existingMember.role !== ur.role && existingMemberIds.includes(ur.userId);
        });
        if (roleUpdates.length > 0) {
          updateBody.updateUserRoles = roleUpdates;
        }
      }

      // Single API call to update everything
      await axios.put(`/api/v1/teams/${selectedTeam.id}`, updateBody);

      setSuccess('Team updated successfully');
      setEditDialogOpen(false);
      setDetailsDialogOpen(false);
      setSelectedTeam(null);
      fetchTeams();
    } catch (err: any) {
      console.error('Error updating team:', err);
      throw new Error(err.response?.data?.message || err.message || 'Failed to update team');
    }
  };

  const handleDeleteTeam = async () => {
    if (!selectedTeam) return;

    try {
      await axios.delete(`/api/v1/teams/${selectedTeam.id}`);
      setSuccess('Team deleted successfully');
      setDeleteDialogOpen(false);
      setDetailsDialogOpen(false);
      setSelectedTeam(null);
      fetchTeams();
    } catch (err: any) {
      console.error('Error deleting team:', err);
      throw new Error(err.response?.data?.message || err.message || 'Failed to delete team');
    }
  };

  const openDetailsDialog = (team: Team) => {
    setSelectedTeam(team);
    setDetailsDialogOpen(true);
  };

  const openEditDialog = (team: Team) => {
    setSelectedTeam(team);
    setEditDialogOpen(true);
  };

  const openDeleteDialog = (team: Team) => {
    setSelectedTeam(team);
    setDeleteDialogOpen(true);
  };

  const handleChangePage = (_event: unknown, newPage: number) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        PaperProps={{
          sx: {
            width: { xs: '100%', sm: 640, md: 720 },
            maxWidth: '90vw',
            boxShadow: theme.palette.mode === 'dark' 
              ? '0 24px 48px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)'
              : '0 20px 60px rgba(0, 0, 0, 0.12)',
            bgcolor: theme.palette.mode === 'dark' 
              ? alpha(theme.palette.background.paper, 0.95)
              : theme.palette.background.paper,
            backdropFilter: 'blur(8px)',
            border: theme.palette.mode === 'dark' ? '1px solid rgba(255, 255, 255, 0.08)' : 'none',
          },
        }}
        slotProps={{
          backdrop: {
            sx: {
              backgroundColor: theme.palette.mode === 'dark' ? 'rgba(0, 0, 0, 0.35)' : 'rgba(0, 0, 0, 0.5)',
            },
          },
        }}
      >
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
          {/* Header */}
          <Box
            sx={{
              px: 2.5,
              py: 2,
              borderBottom: theme.palette.mode === 'dark' 
                ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
                : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
              flexShrink: 0,
            }}
          >
            <Stack direction="row" spacing={1.5} alignItems="center" justifyContent="space-between">
              <Stack direction="row" spacing={1.25} alignItems="center">
                <Box
                  sx={{
                    width: 32,
                    height: 32,
                    borderRadius: 1.5,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    bgcolor: alpha(theme.palette.primary.main, 0.1),
                    color: theme.palette.primary.main,
                  }}
                >
                  <Icon icon={teamIcon} width={18} height={18} />
                </Box>
                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 600, fontSize: '1rem', lineHeight: 1.2 }}>
                    Teams
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem', lineHeight: 1.2 }}>
                    {totalCount} {totalCount === 1 ? 'team' : 'teams'}
                  </Typography>
                </Box>
              </Stack>
              <IconButton 
                onClick={onClose} 
                size="small"
                sx={{
                  width: 32,
                  height: 32,
                  color: theme.palette.mode === 'dark' 
                    ? alpha(theme.palette.text.secondary, 0.8)
                    : theme.palette.text.secondary,
                  '&:hover': {
                    bgcolor: theme.palette.mode === 'dark' 
                      ? alpha(theme.palette.common.white, 0.1)
                      : alpha(theme.palette.text.secondary, 0.08),
                    color: theme.palette.text.primary,
                  },
                  transition: 'all 0.2s ease',
                }}
              >
                <Icon icon={closeIcon} width={18} height={18} />
              </IconButton>
            </Stack>
          </Box>

          {/* Search and Actions */}
          <Box 
            sx={{ 
              px: 2.5,
              py: 1.5, 
              borderBottom: theme.palette.mode === 'dark' 
                ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
                : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
              flexShrink: 0,
            }}
          >
            <Stack direction="row" spacing={1} alignItems="center">
              <TextField
                size="small"
                placeholder="Search teams..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                sx={{ 
                  flex: 1,
                  '& .MuiOutlinedInput-root': {
                    height: 36,
                    fontSize: '0.875rem',
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
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start" sx={{ ml: 0.5 }}>
                      <Icon icon={searchIcon} width={16} height={16} style={{ color: theme.palette.text.secondary }} />
                    </InputAdornment>
                  ),
                }}
              />
              <Tooltip title="Refresh" arrow>
                <IconButton 
                  onClick={fetchTeams} 
                  disabled={loading}
                  size="small"
                  sx={{
                    width: 36,
                    height: 36,
                    border: `1px solid ${theme.palette.divider}`,
                    '&:hover': {
                      bgcolor: alpha(theme.palette.action.hover, 0.5),
                    },
                  }}
                >
                  <Icon icon={refreshIcon} width={16} height={16} />
                </IconButton>
              </Tooltip>
              <Button
                variant="outlined"
                startIcon={<Icon icon={addIcon} width={16} height={16} />}
                onClick={() => setCreateDialogOpen(true)}
                sx={{
                    height: 32,
                    px: 1.5,
                    borderRadius: 1,
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    textTransform: 'none',
                    borderColor: 'primary.main',
                    color: 'primary.main',
                    '&:hover': {
                      backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                      borderColor: 'primary.dark',
                    },
                  }}
              >
                New Team
              </Button>
            </Stack>
          </Box>

          {/* Teams List */}
          <Box sx={{ flex: 1, overflow: 'auto', minHeight: 0, ...scrollableStyle }}>
            {loading ? (
              <Box sx={{ p: 2 }}>
                {Array.from(new Array(5)).map((_, index) => (
                  <React.Fragment key={index}>
                    <ListItem sx={{ py: 2, px: 2.5 }}>
                      <ListItemAvatar>
                        <Skeleton variant="circular" width={40} height={40} />
                      </ListItemAvatar>
                      <ListItemText
                        primary={<Skeleton variant="text" width="40%" height={20} />}
                        secondary={<Skeleton variant="text" width="60%" height={16} />}
                      />
                      <ListItemSecondaryAction>
                        <Skeleton variant="rectangular" width={64} height={32} sx={{ borderRadius: 1 }} />
                      </ListItemSecondaryAction>
                    </ListItem>
                    {index < 4 && <Divider />}
                  </React.Fragment>
                ))}
              </Box>
            ) : teams.length === 0 ? (
              <Box sx={{ p: 6, textAlign: 'center' }}>
                <Box
                  sx={{
                    width: 64,
                    height: 64,
                    borderRadius: 2,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    bgcolor: alpha(theme.palette.primary.main, 0.08),
                    color: theme.palette.primary.main,
                    mx: 'auto',
                    mb: 2,
                  }}
                >
                  <Icon icon={teamIcon} width={32} height={32} />
                </Box>
                <Typography variant="body1" sx={{ fontWeight: 600, mb: 0.5, color: 'text.primary' }}>
                  {searchQuery ? 'No teams found' : 'No teams yet'}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 3, fontSize: '0.875rem' }}>
                  {searchQuery ? 'Try adjusting your search terms' : 'Create your first team to get started'}
                </Typography>
                {!searchQuery && (
                  <Button
                    variant="contained"
                    startIcon={<Icon icon={addIcon} width={16} height={16} />}
                    onClick={() => setCreateDialogOpen(true)}
                    sx={{
                      textTransform: 'none',
                      fontWeight: 600,
                      px: 3,
                    }}
                  >
                    Create Team
                  </Button>
                )}
              </Box>
            ) : (
              <Box sx={{ px: 0 }}>
                <List sx={{ py: 0 }}>
                  {teams.map((team, index) => (
                    <React.Fragment key={team.id}>
                      <ListItem
                        onClick={() => openDetailsDialog(team)}
                        sx={{
                          py: 1.5,
                          px: 2.5,
                          cursor: 'pointer',
                          minHeight: 64,
                          '&:hover': {
                            bgcolor: 'action.hover',
                          },
                          transition: 'background-color 0.15s ease',
                        }}
                      >
                        <ListItemAvatar sx={{ minWidth: 44, mr: 2 }}>
                          <Box
                            sx={{
                              width: 36,
                              height: 36,
                              borderRadius: 1.5,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              bgcolor: alpha(getAvatarColor(team.name), 0.1),
                              color: getAvatarColor(team.name),
                              fontWeight: 600,
                              fontSize: '0.875rem',
                              flexShrink: 0,
                            }}
                          >
                            {getInitials(team.name)}
                          </Box>
                        </ListItemAvatar>
                        <ListItemText
                          primary={
                            <Typography 
                              variant="body2" 
                              sx={{ 
                                fontWeight: 600,
                                fontSize: '0.875rem',
                                color: 'text.primary',
                                mb: 0.25,
                              }}
                            >
                              {team.name}
                            </Typography>
                          }
                          secondary={
                            <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
                              <Chip
                                icon={<Icon icon={personAddIcon} width={12} height={12} />}
                                label={`${team.memberCount} ${team.memberCount === 1 ? 'member' : 'members'}`}
                                size="small"
                                variant="outlined"
                                sx={{
                                  height: 22,
                                  fontSize: '0.7rem',
                                  fontWeight: 500,
                                  borderRadius: 1,
                                  borderColor: 'divider',
                                  color: 'text.secondary',
                                  '& .MuiChip-icon': {
                                    color: 'text.secondary',
                                    ml: 0.5,
                                    fontSize: '0.75rem',
                                  },
                                }}
                              />
                              <Typography 
                                variant="caption" 
                                color="text.secondary"
                                sx={{ 
                                  fontSize: '0.75rem',
                                  fontWeight: 400,
                                }}
                              >
                                {new Date(team.createdAtTimestamp).toLocaleDateString('en-US', {
                                  month: 'short',
                                  day: 'numeric',
                                  year: 'numeric',
                                })}
                              </Typography>
                            </Stack>
                          }
                        />
                        <Icon
                          icon="mdi:chevron-right"
                          width={20}
                          height={20}
                          style={{ 
                            color: theme.palette.text.secondary,
                            flexShrink: 0,
                            opacity: 0.6,
                          }}
                        />
                      </ListItem>
                      {index < teams.length - 1 && (
                        <Divider 
                          sx={{ 
                            mx: 2.5,
                            borderColor: alpha(theme.palette.divider, 0.08),
                          }} 
                        />
                      )}
                    </React.Fragment>
                  ))}
                </List>
              </Box>
            )}
          </Box>

          {/* Pagination */}
          {!loading && teams.length > 0 && (
            <Box 
              sx={{ 
                borderTop: theme.palette.mode === 'dark'
                  ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
                  : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                flexShrink: 0,
              }}
            >
              <TablePagination
                component="div"
                count={totalCount}
                page={page}
                onPageChange={handleChangePage}
                rowsPerPage={rowsPerPage}
                onRowsPerPageChange={handleChangeRowsPerPage}
                rowsPerPageOptions={[5, 10, 25, 50]}
                sx={{
                  '& .MuiTablePagination-toolbar': {
                    px: 2.5,
                    py: 1.5,
                    minHeight: 'auto',
                  },
                  '& .MuiTablePagination-selectLabel, & .MuiTablePagination-displayedRows': {
                    fontSize: '0.875rem',
                    color: 'text.secondary',
                    fontWeight: 500,
                    margin: 0,
                  },
                  '& .MuiTablePagination-select': {
                    fontSize: '0.875rem',
                    fontWeight: 500,
                    borderRadius: 1,
                  },
                  '& .MuiTablePagination-actions': {
                    '& .MuiIconButton-root': {
                      borderRadius: 1,
                      width: 32,
                      height: 32,
                      '&:hover': {
                        backgroundColor: 'action.hover',
                      },
                    },
                  },
                }}
              />
            </Box>
          )}
        </Box>
      </Drawer>

      {/* Dialogs */}
      <CreateTeamDialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        onSubmit={handleCreateTeam}
        onSuccess={setSuccess}
        onError={setError}
      />

      <EditTeamDialog
        open={editDialogOpen}
        team={selectedTeam}
        onClose={() => {
          setEditDialogOpen(false);
          setSelectedTeam(null);
        }}
        onSubmit={handleUpdateTeam}
        onSuccess={setSuccess}
        onError={setError}
      />

      <DeleteTeamDialog
        open={deleteDialogOpen}
        team={selectedTeam}
        onClose={() => {
          setDeleteDialogOpen(false);
          setSelectedTeam(null);
        }}
        onSubmit={handleDeleteTeam}
        onSuccess={setSuccess}
        onError={setError}
      />

      <TeamDetailsDialog
        open={detailsDialogOpen}
        team={selectedTeam}
        onClose={() => {
          setDetailsDialogOpen(false);
          setSelectedTeam(null);
        }}
        onUpdate={handleUpdateTeam}
        onDelete={handleDeleteTeam}
        onSuccess={setSuccess}
        onError={setError}
        onTeamUpdated={async () => {
          // Refresh the team data
          await fetchTeams();
          // Update selectedTeam with fresh data if dialog is still open
          if (selectedTeam) {
            const params: any = {
              page: page + 1,
              limit: rowsPerPage,
            };
            if (debouncedSearchQuery) {
              params.search = debouncedSearchQuery;
            }
            try {
              const { data } = await axios.get('/api/v1/teams/user/teams', { params });
              const updatedTeam = data?.teams?.find((t: Team) => t.id === selectedTeam.id);
              if (updatedTeam) {
                setSelectedTeam(updatedTeam);
              }
            } catch (err) {
              console.error('Error refreshing team data:', err);
            }
          }
        }}
      />
    </>
  );
};

export default TeamManagementSlider;