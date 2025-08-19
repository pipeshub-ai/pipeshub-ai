import type { KeyboardEvent } from 'react';

import { useDispatch } from 'react-redux';
import { useNavigate } from 'react-router-dom';
import editIcon from '@iconify-icons/eva/edit-fill';
import closeIcon from '@iconify-icons/eva/close-fill';
import peopleIcon from '@iconify-icons/eva/people-fill';
import searchIcon from '@iconify-icons/eva/search-fill';
import emailIcon from '@iconify-icons/eva/email-outline';
import trashIcon from '@iconify-icons/eva/trash-2-outline';
import React, { useRef, useState, useEffect } from 'react';
import personIcon from '@iconify-icons/eva/person-add-fill';
import alertIcon from '@iconify-icons/eva/alert-triangle-fill';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import verticalIcon from '@iconify-icons/eva/more-vertical-fill';

import {
  Box,
  Chip,
  Menu,
  Fade,
  Table,
  Stack,
  Paper,
  Alert,
  alpha,
  Avatar,
  Button,
  Dialog,
  Popover,
  Tooltip,
  TableRow,
  Snackbar,
  MenuItem,
  useTheme,
  TableBody,
  TableCell,
  TableHead,
  TextField,
  InputBase,
  Typography,
  IconButton,
  DialogTitle,
  Autocomplete,
  DialogContent,
  DialogActions,
  TableContainer,
  TablePagination,
  CircularProgress
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';

import {
  setCounts,
  decrementUserCount,
  updateInvitesCount,
} from '../../../store/user-and-groups-slice';
import {
  allGroups,
  removeUser,
  inviteUsers,
  addUsersToGroups,
  getUserIdFromToken,
  getAllUsersWithGroups,
} from '../utils';

import type { SnackbarState } from '../types/organization-data';
import type { GroupUser, AppUserGroup, AddUserModalProps } from '../types/group-details';

interface AddUsersToGroupsModalProps {
  open: boolean;
  onClose: () => void;
  onUsersAdded: (message?: string) => void;
  allUsers: GroupUser[] | null;
  groups: AppUserGroup[];
}

// Get initials from full name
const getInitials = (fullName: string) =>
  fullName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase();

const Users = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [users, setUsers] = useState<GroupUser[]>([]);
  const [groups, setGroups] = useState<AppUserGroup[]>([]);
  const [page, setPage] = useState<number>(0);
  const [rowsPerPage, setRowsPerPage] = useState<number>(10);
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [isAddUserModalOpen, setIsAddUserModalOpen] = useState<boolean>(false);
  const [isAddUsersToGroupsModalOpen, setIsAddUsersToGroupsModalOpen] = useState<boolean>(false);
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const [selectedUser, setSelectedUser] = useState<GroupUser | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [userId, setUserId] = useState<string | null>(null);
  const [menuAnchorEl, setMenuAnchorEl] = useState<HTMLElement | null>(null);
  const [isConfirmDialogOpen, setIsConfirmDialogOpen] = useState<boolean>(false);
  const [snackbarState, setSnackbarState] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  const navigate = useNavigate();
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);
  const dispatch = useDispatch();
  const { isAdmin } = useAdmin();

  const handleSnackbarClose = () => {
    setSnackbarState({ ...snackbarState, open: false });
  };

  useEffect(() => {
    const fetchUsersAndGroups = async () => {
      setLoading(true);
      try {
        const orgId = await getUserIdFromToken();
        setUserId(orgId);
        const response = await getAllUsersWithGroups();
        const groupsData = await allGroups();
        const loggedInUsers = response.filter(
          (user) => user?.email !== null && user.fullName && user.hasLoggedIn === true
        );
        const pendingUsers = response.filter((user) => user.hasLoggedIn === false);

        dispatch(
          setCounts({
            usersCount: loggedInUsers.length,
            groupsCount: groupsData.length,
            invitesCount: pendingUsers.length,
          })
        );
        setUsers(loggedInUsers);
        setGroups(groupsData);
      } catch (error) {
        // setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
      } finally {
        setLoading(false);
      }
    };

    fetchUsersAndGroups();
    // eslint-disable-next-line
  }, []);

  const filteredUsers = users.filter(
    (user) =>
      (user?.fullName?.toLowerCase() || '').includes(searchTerm.toLowerCase()) ||
      (user?.email?.toLowerCase() || '').includes(searchTerm.toLowerCase())
  );

  const handleChangePage = (event: unknown, newPage: number) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const handleAddUser = () => {
    setIsAddUserModalOpen(true);
  };

  const handleCloseAddUserModal = () => {
    setIsAddUserModalOpen(false);
  };

  const handleAddUsersToGroups = () => {
    setIsAddUsersToGroupsModalOpen(true);
  };

  const handleCloseAddUsersToGroupsModal = () => {
    setIsAddUsersToGroupsModalOpen(false);
  };

  const handleDeleteUser = async (deleteUserId: string): Promise<void> => {
    try {
      await removeUser(deleteUserId);
      const updatedUsers = await getAllUsersWithGroups();

      const loggedInUsers = updatedUsers.filter((user) => user.email !== null && user.fullName);
      setUsers(loggedInUsers);
      dispatch(decrementUserCount());

      setSnackbarState({ open: true, message: 'User removed successfully', severity: 'success' });
    } catch (error) {
      // setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
    }
  };

  const handlePopoverOpen = (event: React.MouseEvent<HTMLElement>, user: GroupUser): void => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    setAnchorEl(event.currentTarget);
    setSelectedUser(user);
  };

  const handlePopoverClose = () => {
    timeoutRef.current = setTimeout(() => {
      setAnchorEl(null);
      setSelectedUser(null);
    }, 300);
  };

  const handleUsersAdded = async (message?: string) => {
    try {
      const updatedUsers = await getAllUsersWithGroups();

      const loggedInUsers = updatedUsers.filter((user) => user?.email !== '' && user?.fullName);
      setUsers(loggedInUsers);
      setSnackbarState({
        open: true,
        message: message || 'Users added to groups successfully',
        severity: message ? 'error' : 'success',
      });
    } catch (error) {
      // setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
    }
  };

  const handleUsersInvited = async (message?: string) => {
    try {
      const updatedUsers = await getAllUsersWithGroups();

      const loggedInUsers = updatedUsers.filter((user) => user.email !== null && user.fullName);
      setUsers(loggedInUsers);
      setSnackbarState({
        open: true,
        message: message || 'Users invited successfully',
        severity: message && message !== 'Invite sent successfully' ? 'error' : 'success',
      });
    } catch (error) {
      // setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
    }
  };

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, user: GroupUser) => {
    setMenuAnchorEl(event.currentTarget);
    setSelectedUser(user);
  };

  const handleMenuClose = () => {
    setMenuAnchorEl(null);
  };

  const handleRemoveUser = () => {
    if (selectedUser) {
      setIsConfirmDialogOpen(true);
    }
    handleMenuClose();
  };

  const handleConfirmRemoveUser = () => {
    if (selectedUser && selectedUser._id) {
      handleDeleteUser(selectedUser._id);
    }
    setIsConfirmDialogOpen(false);
  };

  const open = Boolean(anchorEl);

  // Function to get avatar color based on name
  const getAvatarColor = (name: string) => {
    const colors = [
      theme.palette.primary.main,
      theme.palette.info.main,
      theme.palette.success.main,
      theme.palette.warning.main,
      theme.palette.error.main,
    ];

    // Simple hash function using array methods instead of for loop with i++
    const hash = name.split('').reduce((acc, char) => char.charCodeAt(0) + (acc * 32 - acc), 0);

    return colors[Math.abs(hash) % colors.length];
  };

  if (loading) {
    return (
      <Box
        display="flex"
        flexDirection="column"
        justifyContent="center"
        alignItems="center"
        sx={{ height: 300 }}
      >
        <CircularProgress size={36} thickness={2.5} />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
          Loading users...
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      {/* Search and Action Buttons */}
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        sx={{ mb: 3 }}
      >
        <Paper
          elevation={0}
          sx={{
            display: 'flex',
            alignItems: 'center',
            width: { xs: '100%', sm: '40%' },
            p: 1,
            borderRadius: 2,
            bgcolor: isDark
              ? alpha(theme.palette.background.paper, 0.6)
              : alpha(theme.palette.grey[100], 0.7),
            border: `1px solid ${isDark ? alpha(theme.palette.divider, 0.1) : alpha(theme.palette.divider, 0.08)}`,
            '&:hover': {
              bgcolor: isDark
                ? alpha(theme.palette.background.paper, 0.8)
                : alpha(theme.palette.grey[100], 0.9),
            },
            transition: 'background-color 0.2s ease',
          }}
        >
          <Iconify
            icon={searchIcon}
            width={20}
            height={20}
            sx={{ color: 'text.disabled', mr: 1 }}
          />
          <InputBase
            placeholder="Search users by name or email"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            fullWidth
            sx={{ fontSize: '0.875rem' }}
          />
          {searchTerm && (
            <IconButton size="small" onClick={() => setSearchTerm('')} sx={{ p: 0.5 }}>
              <Iconify icon={closeIcon} width={16} height={16} />
            </IconButton>
          )}
        </Paper>

        <Stack direction="row" spacing={1.5}>
          <Button
            variant="outlined"
            color="primary"
            onClick={handleAddUsersToGroups}
            startIcon={<Iconify icon={accountGroupIcon} />}
            size="medium"
            sx={{
              borderRadius: 1.5,
              fontSize: '0.8125rem',
              height: 40,
            }}
          >
            Add to Group
          </Button>
          <Button
            variant="contained"
            color="primary"
            startIcon={<Iconify icon={personIcon} />}
            onClick={handleAddUser}
            size="medium"
            sx={{
              borderRadius: 1.5,
              fontSize: '0.8125rem',
              height: 38,
              fontWeight: 500,
              px: 2,
              boxShadow: 'none',
              '&:hover': {
                boxShadow: isDark ? '0 4px 10px 0 rgba(0,0,0,0.3)' : '0 4px 10px 0 rgba(0,0,0,0.1)',
              },
            }}
          >
            Invite User
          </Button>
        </Stack>
      </Stack>

      {/* Users Table */}
      <TableContainer
        component={Paper}
        elevation={0}
        sx={{
          borderRadius: 2,
          overflow: 'hidden',
          border: `1px solid ${isDark ? alpha(theme.palette.divider, 0.1) : alpha(theme.palette.divider, 0.08)}`,
          mb: 2,
          bgcolor: isDark
            ? alpha(theme.palette.background.paper, 0.6)
            : theme.palette.background.paper,
          backdropFilter: 'blur(8px)',
        }}
      >
        <Table sx={{ minWidth: 650 }} aria-label="users table">
          <TableHead>
            <TableRow
              sx={{
                bgcolor: isDark
                  ? alpha(theme.palette.background.paper, 0.8)
                  : alpha(theme.palette.grey[50], 0.8),
                '& th': {
                  borderBottom: `1px solid ${isDark ? alpha(theme.palette.divider, 0.1) : alpha(theme.palette.divider, 0.08)}`,
                },
              }}
            >
              {' '}
              <TableCell
                sx={{
                  fontWeight: 600,
                  py: 1.5,
                  fontSize: '0.75rem',
                  letterSpacing: '0.5px',
                  opacity: 0.8,
                }}
              >
                USER
              </TableCell>
              <TableCell
                sx={{
                  fontWeight: 600,
                  py: 1.5,
                  fontSize: '0.75rem',
                  letterSpacing: '0.5px',
                  opacity: 0.8,
                }}
              >
                GROUPS
              </TableCell>
              <TableCell
                align="right"
                sx={{
                  fontWeight: 600,
                  py: 1.5,
                  width: 80,
                  fontSize: '0.75rem',
                  letterSpacing: '0.5px',
                  opacity: 0.8,
                }}
              >
                ACTIONS
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {filteredUsers.length > 0 ? (
              filteredUsers
                .slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)
                .map((user) => (
                  <TableRow
                    key={user._id}
                    sx={{
                      '&:last-child td, &:last-child th': { border: 0 },
                      '&:hover': {
                        bgcolor: isDark
                          ? alpha(theme.palette.primary.dark, 0.05)
                          : alpha(theme.palette.primary.lighter, 0.05),
                      },
                      transition: 'background-color 0.2s ease',
                    }}
                  >
                  <TableCell component="th" scope="row" sx={{ py: 1.5 }}>
                      {user._id && (
                        <Stack direction="row" alignItems="center" spacing={2}>
                          <Tooltip title="View user details">
                            <Avatar
                              sx={{
                                bgcolor: getAvatarColor(user.fullName),
                                cursor: 'pointer',
                                transition: 'transform 0.2s ease',
                                '&:hover': { transform: 'scale(1.1)' },
                              }}
                              onMouseEnter={(e) => handlePopoverOpen(e, user)}
                              onMouseLeave={handlePopoverClose}
                            >
                              {getInitials(user.fullName)}
                            </Avatar>
                          </Tooltip>
                          <Box>
                            <Typography variant="subtitle2">
                              {user.fullName || 'Unnamed User'}
                            </Typography>
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              sx={{ fontSize: '0.75rem' }}
                            >
                              {user.email || 'No email'}
                            </Typography>
                          </Box>
                        </Stack>
                      )}
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" flexWrap="wrap" gap={0.75}>
                        {user.groups.length > 0 ? (
                          user.groups.map((group, index) => (
                            <Chip
                              key={index}
                              label={group.name}
                              size="small"
                              sx={{
                                height: 22,
                                fontSize: '0.75rem',
                                fontWeight: 500,
                                borderRadius: '4px',
                                // Clean, professional styling for both modes
                                bgcolor: (themeVal) =>
                                  themeVal.palette.mode !== 'dark'
                                    ? alpha(themeVal.palette.grey[800], 0.1)
                                    : alpha(themeVal.palette.grey[100], 0.8),
                                color: (themeVal) =>
                                  themeVal.palette.mode === 'dark'
                                    ? themeVal.palette.grey[100]
                                    : themeVal.palette.grey[800],
                                border: (themeVal) =>
                                  themeVal.palette.mode === 'dark'
                                    ? `1px solid ${alpha(themeVal.palette.grey[700], 0.5)}`
                                    : `1px solid ${alpha(themeVal.palette.grey[300], 1)}`,
                                '& .MuiChip-label': {
                                  px: 1,
                                  py: 0.25,
                                },
                                '&:hover': {
                                  bgcolor: (themeVal) =>
                                    themeVal.palette.mode !== 'dark'
                                      ? alpha(themeVal.palette.grey[700], 0.1)
                                      : alpha(themeVal.palette.grey[200], 0.1),
                                },
                              }}
                            />
                          ))
                        ) : (
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ fontSize: '0.75rem', fontStyle: 'italic' }}
                          >
                            No groups assigned
                          </Typography>
                        )}
                      </Stack>
                    </TableCell>
                    <TableCell align="right">
                      <IconButton
                        onClick={(e) => handleMenuOpen(e, user)}
                        size="small"
                        sx={{
                          color: theme.palette.text.secondary,
                          '&:hover': {
                            bgcolor: alpha(theme.palette.primary.main, 0.08),
                            color: theme.palette.primary.main,
                          },
                        }}
                      >
                        <Iconify icon={verticalIcon} width={20} height={20} />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))
            ) : (
              <TableRow>
                <TableCell colSpan={3} align="center" sx={{ py: 4 }}>
                  <Box sx={{ textAlign: 'center' }}>
                    <Iconify
                      icon={peopleIcon}
                      width={40}
                      height={40}
                      sx={{ color: 'text.secondary', mb: 1, opacity: 0.5 }}
                    />
                    <Typography variant="subtitle1" sx={{ mb: 0.5 }}>
                      No users found
                    </Typography>
                    <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                      {searchTerm
                        ? 'Try adjusting your search criteria'
                        : 'Invite users to get started'}
                    </Typography>
                  </Box>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Pagination */}
      {filteredUsers.length > 0 && (
        <TablePagination
          component={Paper}
          count={filteredUsers.length}
          page={page}
          onPageChange={handleChangePage}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={handleChangeRowsPerPage}
          rowsPerPageOptions={[5, 10, 25]}
          sx={{
            borderRadius: 2,
            boxShadow: 'none',
            border: `1px solid ${alpha(theme.palette.grey[500], 0.16)}`,
            '.MuiTablePagination-selectLabel, .MuiTablePagination-displayedRows': {
              fontSize: '0.875rem',
            },
          }}
        />
      )}

      {/* User Menu */}
      <Menu
        anchorEl={menuAnchorEl}
        open={Boolean(menuAnchorEl)}
        onClose={handleMenuClose}
        anchorOrigin={{
          vertical: 'bottom',
          horizontal: 'right',
        }}
        transformOrigin={{
          vertical: 'top',
          horizontal: 'right',
        }}
        PaperProps={{
          elevation: 1,
          sx: {
            borderRadius: 2,
            minWidth: 150,
            boxShadow:
              theme.customShadows?.z8 ||
              'rgb(145 158 171 / 24%) 0px 0px 2px 0px, rgb(145 158 171 / 24%) 0px 16px 32px -4px',
            '& .MuiMenuItem-root': {
              fontSize: '0.875rem',
              px: 2,
              py: 1,
            },
          },
        }}
      >
        <MenuItem
          onClick={() => {
            if (selectedUser?._id) {
              if (selectedUser._id === userId) {
                navigate('/account/company-settings/personal-profile');
              } else {
                navigate(`/account/company-settings/user-profile/${selectedUser._id}`);
              }
            }
            handleMenuClose();
          }}
        >
          <Iconify icon={editIcon} width={20} height={20} sx={{ mr: 1.5 }} />
          Edit Profile
        </MenuItem>
        <MenuItem
          disabled={!isAdmin}
          sx={{
            '&.Mui-disabled': {
              cursor: 'not-allowed',
              pointerEvents: 'auto',
              opacity: 0.6,
            },
            color: theme.palette.error.main,
          }}
          onClick={handleRemoveUser}
        >
          <Iconify icon={trashIcon} width={20} height={20} sx={{ mr: 1.5 }} />
          Remove User
        </MenuItem>
      </Menu>

      {/* User Popover */}
      <Popover
        id="mouse-over-popover"
        sx={{ pointerEvents: 'none' }}
        open={open}
        anchorEl={anchorEl}
        anchorOrigin={{
          vertical: 'bottom',
          horizontal: 'left',
        }}
        transformOrigin={{
          vertical: 'top',
          horizontal: 'left',
        }}
        onClose={handlePopoverClose}
        disableRestoreFocus
        PaperProps={{
          onMouseEnter: () => {
            if (timeoutRef.current) {
              clearTimeout(timeoutRef.current);
            }
          },
          onMouseLeave: handlePopoverClose,
          sx: {
            pointerEvents: 'auto',
            boxShadow:
              theme.customShadows?.z16 ||
              'rgb(145 158 171 / 24%) 0px 0px 2px 0px, rgb(145 158 171 / 24%) 0px 16px 32px -4px',
            borderRadius: 2,
            maxWidth: 320,
            p: 2.5,
          },
        }}
      >
        {selectedUser && selectedUser._id && (
          <Stack spacing={2}>
            <Stack direction="row" spacing={2} alignItems="center">
              <Avatar
                sx={{
                  width: 64,
                  height: 64,
                  bgcolor: getAvatarColor(selectedUser.fullName),
                  fontSize: '1.5rem',
                  fontWeight: 600,
                }}
              >
                {getInitials(selectedUser.fullName)}
              </Avatar>
              <Box>
                <Typography variant="h6" sx={{ mb: 0.5 }}>
                  {selectedUser.fullName || 'Unnamed User'}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {selectedUser.email || 'No email'}
                </Typography>
              </Box>
            </Stack>

            {selectedUser.groups && selectedUser.groups.length > 0 && (
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Groups
                </Typography>
                <Stack direction="row" flexWrap="wrap" gap={0.75}>
                  {selectedUser.groups.map((group, idx) => (
                    <Chip
                      key={idx}
                      label={group.name || 'Unnamed Group'}
                      size="small"
                      sx={{
                        height: 22,
                        fontSize: '0.75rem',
                        fontWeight: 500,
                        borderRadius: '4px',
                        // Clean, professional styling for both modes
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode !== 'dark'
                            ? alpha(themeVal.palette.grey[800], 0.1)
                            : alpha(themeVal.palette.grey[100], 0.8),
                        color: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? themeVal.palette.grey[100]
                            : themeVal.palette.grey[800],
                        border: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? `1px solid ${alpha(themeVal.palette.grey[700], 0.5)}`
                            : `1px solid ${alpha(themeVal.palette.grey[300], 1)}`,
                        '& .MuiChip-label': {
                          px: 1,
                          py: 0.25,
                        },
                        '&:hover': {
                          bgcolor: (themeVal) =>
                            themeVal.palette.mode !== 'dark'
                              ? alpha(themeVal.palette.grey[700], 0.1)
                              : alpha(themeVal.palette.grey[200], 0.1),
                        },
                      }}
                    />
                  ))}
                </Stack>
              </Box>
            )}

            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon={editIcon} />}
              onClick={() => {
                if (selectedUser._id) {
                  if (selectedUser._id === userId) {
                    navigate('/account/company-settings/personal-profile');
                  } else {
                    navigate(`/account/company-settings/user-profile/${selectedUser._id}`);
                  }
                }
              }}
              sx={{
                alignSelf: 'flex-start',
                borderRadius: 1,
                mt: 1,
              }}
            >
              Edit profile
            </Button>
          </Stack>
        )}
      </Popover>

      {/* Confirmation Dialog */}
      <Dialog
        open={isConfirmDialogOpen}
        onClose={() => setIsConfirmDialogOpen(false)}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: 2,
            padding: 1,
            maxWidth: 400,
          },
        }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Iconify
              icon={alertIcon}
              width={24}
              height={24}
              sx={{ color: theme.palette.warning.main }}
            />
            <Typography variant="h6">Confirm User Removal</Typography>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mt: 1 }}>
            Are you sure you want to remove {selectedUser?.fullName || 'this user'}? This action
            cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={() => setIsConfirmDialogOpen(false)}
            variant="outlined"
            sx={{ borderRadius: 1 }}
          >
            Cancel
          </Button>
          <Button
            onClick={handleConfirmRemoveUser}
            variant="contained"
            color="error"
            sx={{ borderRadius: 1 }}
          >
            Remove
          </Button>
        </DialogActions>
      </Dialog>

      {/* Snackbar */}
      <Snackbar
        open={snackbarState.open}
        autoHideDuration={6000}
        onClose={handleSnackbarClose}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleSnackbarClose}
          severity={snackbarState.severity}
          variant="filled"
          sx={{
            width: '100%',
            borderRadius: 1.5,
          }}
        >
          {snackbarState.message}
        </Alert>
      </Snackbar>

      {/* Modals */}
      <AddUserModal
        open={isAddUserModalOpen}
        onClose={handleCloseAddUserModal}
        groups={groups}
        onUsersAdded={handleUsersInvited}
      />

      <AddUsersToGroupsModal
        open={isAddUsersToGroupsModalOpen}
        onClose={handleCloseAddUsersToGroupsModal}
        onUsersAdded={handleUsersAdded}
        allUsers={users}
        groups={groups}
      />
    </Box>
  );
};

function AddUserModal({ open, onClose, groups, onUsersAdded }: AddUserModalProps) {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [emails, setEmails] = useState<string[]>([]);
  const [inputValue, setInputValue] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [selectedGroups, setSelectedGroups] = useState<AppUserGroup[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [snackbarState, setSnackbarState] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });
  const dispatch = useDispatch();

  // Function to validate email format
  const validateEmail = (email: string): boolean => {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(email);
  };

  const handleSnackbarClose = () => {
    setSnackbarState({ ...snackbarState, open: false });
  };

  const handleRemoveEmail = (emailToRemove: string) => {
    setEmails(emails.filter((email) => email !== emailToRemove));
  };

  // Modified to be a standalone function that can be called by button or keyboard
  const handleAddEmail = (): void => {
    if (inputValue.trim() === '') return;

    setError('');
    const emailToAdd = inputValue.trim();

    if (validateEmail(emailToAdd)) {
      // Add email if it's not already in the list
      if (!emails.includes(emailToAdd)) {
        setEmails([...emails, emailToAdd]);
        setInputValue('');
      } else {
        setError('This email has already been added');
        setTimeout(() => setError(''), 3000);
      }
    } else {
      setError('Please enter a valid email address');
      setTimeout(() => setError(''), 3000);
    }
  };

  // Keyboard handler for Enter key
  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAddEmail();
    }
  };

  const handleAddUsers = async (): Promise<void> => {
    try {
      if (emails.length === 0) {
        setError('Please add at least one email address');
        setTimeout(() => setError(''), 3000);
        return;
      }
      setIsLoading(true);

      const groupIds = selectedGroups.map((group) => group._id);

      // Attempt to invite users
      const result = await inviteUsers({ emails, groupIds });

      // Only clear data after successful API call
      dispatch(updateInvitesCount(emails.length));
      if (result?.message !== 'Invite sent successfully') {
        setSnackbarState({ open: true, message: result.message, severity: 'error' });
      } else {
        setSnackbarState({ open: true, message: 'Users added successfully', severity: 'success' });
      }

      // Clear emails after successful submission
      setEmails([]);
      setSelectedGroups([]);

      onUsersAdded(result?.message);
      onClose();
    } catch (err: any) {
      // Improved error handling
      let errorMessage = 'Failed to add users';

      // Extract more specific error message if available
      if (err.errorMessage) {
        errorMessage = err.errorMessage;
      } else if (err.message) {
        errorMessage = err.message;
      } else if (typeof err === 'string') {
        errorMessage = err;
      }

      // Display error but preserve the email list
      setSnackbarState({
        open: true,
        message: errorMessage,
        severity: 'error',
      });

      // Log error for debugging
      console.error('Error inviting users:', err);
    } finally {
      // Reset loading state whether the operation succeeded or failed
      setIsLoading(false);
    }
  };

  const emailChipStyle = {
    borderRadius: 0.75,
    height: 24,
    fontSize: '0.75rem',
    fontWeight: 500,
    bgcolor: isDark
      ? alpha(theme.palette.common.white, 0.85)
      : alpha(theme.palette.primary.main, 0.75),
    color: isDark
      ? alpha(theme.palette.primary.light, 0.1)
      : alpha(theme.palette.common.white, 0.9),
    border: isDark ? `1px solid ${alpha(theme.palette.primary.main, 0.4)}` : 'none',
    '& .MuiChip-deleteIcon': {
      color: isDark
        ? alpha(theme.palette.primary.dark, 0.7)
        : alpha(theme.palette.common.white, 0.7),
      '&:hover': {
        color: isDark ? theme.palette.primary.darker : theme.palette.primary.light,
      },
    },
  };

  const groupChipStyle = {
    borderRadius: 0.75,
    height: 24,
    fontSize: '0.75rem',
    fontWeight: 500,
    bgcolor: isDark
      ? alpha(theme.palette.info.main, 0.85)
      : alpha(theme.palette.common.white, 0.45),
    color: isDark ? alpha(theme.palette.info.light, 0.9) : theme.palette.primary.lighter,
    border: isDark ? `1px solid ${alpha(theme.palette.info.main, 0.4)}` : 'none',
    '& .MuiChip-deleteIcon': {
      color: isDark ? alpha(theme.palette.info.dark, 0.7) : alpha(theme.palette.common.white, 0.4),
      '&:hover': {
        color: isDark ? theme.palette.info.light : theme.palette.info.dark,
      },
    },
  };

  return (
    <Dialog
        open={open}
        onClose={onClose}
        maxWidth="sm"
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
              <Iconify
                icon={personIcon}
                width={18}
                height={18}
                sx={{ color: theme.palette.primary.main }}
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
              Invite Users
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
            <Iconify icon="mdi:close" width={18} height={18} />
          </IconButton>
        </DialogTitle>

        <DialogContent sx={{ px: 3, py: 2.5 }}>
          <Box sx={{ mt: 0.5, mb: 3 }}>
            {/* Email Input Container with Enhanced Scrolling */}
            <Box
              sx={{
                border: error
                  ? `1px solid ${theme.palette.error.main}`
                  : `1px solid ${alpha(theme.palette.divider, isDark ? 0.8 : 0.23)}`,
                borderRadius: 1,
                p: 1,
                mt: 4,
                mb: error ? 0.5 : 2,
                minHeight: '120px',
                maxHeight: '180px',
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
                bgcolor: isDark
                  ? alpha(theme.palette.background.paper, 0.6)
                  : theme.palette.background.paper,
                boxShadow: error
                  ? `0 0 0 2px ${alpha(theme.palette.error.main, isDark ? 0.8 : 0.14)}`
                  : `0 0 0 2px ${alpha(theme.palette.primary.main, isDark ? 0.4 : 0.14)}`,
                '&:focus-within': {
                  borderColor: error ? theme.palette.error.main : theme.palette.primary.main,
                  boxShadow: error
                    ? `0 0 0 2px ${alpha(theme.palette.error.main, isDark ? 0.8 : 0.14)}`
                    : `0 0 0 2px ${alpha(theme.palette.primary.main, isDark ? 0.8 : 0.14)}`,
                },
              }}
            >
              {/* Scrollable Email Chips Container */}
              <Box
                sx={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignContent: 'flex-start',
                  overflowY: 'auto',
                  flexGrow: 1,
                  mb: 1,
                  p: 0.5,
                  maxHeight: '130px',
                  '&::-webkit-scrollbar': {
                    width: '6px',
                  },
                  '&::-webkit-scrollbar-track': {
                    backgroundColor: 'transparent',
                  },
                  '&::-webkit-scrollbar-thumb': {
                    backgroundColor: isDark
                      ? alpha(theme.palette.common.white, 0.2)
                      : alpha(theme.palette.common.black, 0.2),
                    borderRadius: '6px',
                  },
                }}
              >
                {emails.map((email, index) => (
                  <Chip
                    key={index}
                    label={email}
                    onDelete={() => handleRemoveEmail(email)}
                    deleteIcon={<Iconify icon={closeIcon} width={14} />}
                    sx={emailChipStyle}
                  />
                ))}
              </Box>

              {/* Input Row (Fixed at Bottom) */}
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  borderTop:
                    emails.length > 0 ? `1px solid ${alpha(theme.palette.divider, 0.08)}` : 'none',
                  pt: emails.length > 0 ? 1 : 0,
                  mt: emails.length > 0 ? 0.5 : 0,
                }}
              >
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    flexGrow: 1,
                    gap: 0.5,
                  }}
                >
                  {emails.length === 0 && (
                    <Iconify
                      icon="mdi:email-outline"
                      width={18}
                      height={18}
                      sx={{ color: theme.palette.text.secondary, ml: 0.5 }}
                    />
                  )}
                  <TextField
                    value={inputValue}
                    onChange={(e) => setInputValue(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={emails.length === 0 ? 'name@example.com' : 'Add another email...'}
                    variant="standard"
                    error={!!error}
                    aria-invalid={!!error}
                    fullWidth
                    inputProps={{
                      'aria-label': 'Add email addresses',
                    }}
                    sx={{
                      flexGrow: 1,
                      '& .MuiInput-underline:before': { borderBottom: 'none' },
                      '& .MuiInput-underline:hover:before': { borderBottom: 'none' },
                      '& .MuiInput-underline:after': { borderBottom: 'none' },
                      '& .MuiInputBase-input': {
                        color: theme.palette.text.primary,
                        py: 0.5,
                        fontSize: '0.875rem',
                      },
                    }}
                  />
                </Box>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={handleAddEmail}
                  sx={{
                    minWidth: 'auto',
                    height: 32,
                    borderRadius: 1,
                    textTransform: 'none',
                    borderColor: alpha(theme.palette.primary.main, isDark ? 0.5 : 0.3),
                    color: theme.palette.primary.main,
                    px: 2,
                    '&:hover': {
                      backgroundColor: alpha(theme.palette.primary.main, 0.08),
                      borderColor: theme.palette.primary.main,
                    },
                  }}
                >
                  Add
                </Button>
              </Box>
            </Box>

            {/* Email Count Badge */}
            {emails.length > 0 && (
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  mb: error ? 0.5 : 2,
                  mt: error ? 0 : -1,
                  gap: 0.75,
                }}
              >
                <Box
                  sx={{
                    borderRadius: 5,
                    height: 20,
                    minWidth: 20,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    bgcolor: isDark
                      ? alpha(theme.palette.primary.main, 0.2)
                      : alpha(theme.palette.primary.light, 0.15),
                    px: 0.75,
                  }}
                >
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: '0.6875rem',
                      fontWeight: 600,
                      color: isDark
                        ? alpha(theme.palette.primary.light, 0.9)
                        : theme.palette.primary.main,
                    }}
                  >
                    {emails.length}
                  </Typography>
                </Box>
                <Typography
                  variant="caption"
                  sx={{
                    fontSize: '0.75rem',
                    color: theme.palette.text.secondary,
                  }}
                >
                  {emails.length === 1 ? 'email address added' : 'email addresses added'}
                </Typography>
              </Box>
            )}

            {/* Error message */}
            {error && (
              <Typography
                variant="caption"
                color="error"
                sx={{
                  ml: 1,
                  display: 'block',
                  mb: 1,
                  fontWeight: 500,
                }}
              >
                {error}
              </Typography>
            )}

            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                mb: 2,
                fontSize: '0.875rem',
                opacity: isDark ? 0.8 : 0.7,
              }}
            >
              Your team members will receive an email with instructions to access the system.
            </Typography>

            <Autocomplete
              multiple
              limitTags={3}
              options={groups}
              getOptionLabel={(option) => option.name}
              renderInput={(params) => (
                <TextField
                  {...params}
                  placeholder="Add to groups"
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      backgroundColor: isDark
                        ? alpha(theme.palette.background.paper, 0.6)
                        : theme.palette.background.paper,
                      '& fieldset': {
                        borderColor: alpha(theme.palette.divider, isDark ? 0.2 : 0.1),
                      },
                      '&:hover fieldset': {
                        borderColor: alpha(theme.palette.primary.main, 0.5),
                      },
                      '& .MuiOutlinedInput-input': {
                        padding: '10px 12px',
                      },
                    },
                  }}
                  InputProps={{
                    ...params.InputProps,
                    startAdornment: (
                      <>
                        <Box mr={1} display="flex" alignItems="center">
                          <Iconify
                            icon="mdi:folder-account"
                            width={18}
                            height={18}
                            sx={{ color: theme.palette.text.secondary }}
                          />
                        </Box>
                        {params.InputProps.startAdornment}
                      </>
                    ),
                  }}
                />
              )}
              onChange={(event, newValue) => setSelectedGroups(newValue)}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option.name}
                    {...getTagProps({ index })}
                    size="small"
                    sx={groupChipStyle}
                  />
                ))
              }
              renderOption={(props, option) => (
                <MenuItem
                  {...props}
                  sx={{
                    py: 1,
                    px: 1.5,
                    borderRadius: 1,
                    my: 0.25,
                    '&:hover': {
                      bgcolor: isDark
                        ? alpha(theme.palette.action.hover, 0.1)
                        : theme.palette.action.hover,
                    },
                    '&.Mui-selected': {
                      bgcolor: isDark
                        ? alpha(theme.palette.info.main, 0.2)
                        : alpha(theme.palette.info.light, 0.1),
                      '&:hover': {
                        bgcolor: isDark
                          ? alpha(theme.palette.info.main, 0.25)
                          : alpha(theme.palette.info.light, 0.15),
                      },
                    },
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Box
                      sx={{
                        width: 24,
                        height: 24,
                        borderRadius: '50%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: isDark
                          ? alpha(theme.palette.info.main, 0.3)
                          : alpha(theme.palette.info.light, 0.2),
                      }}
                    >
                      <Iconify
                        icon="mdi:account-group"
                        width={14}
                        height={14}
                        sx={{
                          color: isDark ? theme.palette.info.light : theme.palette.info.main,
                        }}
                      />
                    </Box>
                    <Typography
                      variant="body2"
                      sx={{
                        fontWeight: 500,
                        color: theme.palette.text.primary,
                      }}
                    >
                      {option.name}
                    </Typography>
                  </Stack>
                </MenuItem>
              )}
              PaperComponent={({ children }) => (
                <Paper
                  sx={{
                    maxHeight: 220,
                    overflow: 'auto',
                    mt: 0.5,
                    borderRadius: 1,
                    boxShadow: isDark
                      ? `0 4px 20px ${alpha(theme.palette.common.black, 0.3)}`
                      : `0 4px 20px ${alpha(theme.palette.common.black, 0.1)}`,
                    border: `1px solid ${alpha(theme.palette.divider, 0.05)}`,
                  }}
                >
                  {children}
                </Paper>
              )}
            />

            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                mt: 1.5,
                fontSize: '0.875rem',
                opacity: isDark ? 0.8 : 0.7,
              }}
            >
              New users will inherit the permissions given to the selected groups.
            </Typography>
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
            onClick={handleAddUsers}
            variant="contained"
            disableElevation
            disabled={emails.length === 0 || isLoading}
            startIcon={
              isLoading ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Iconify icon={emailIcon} width={18} height={18} />
              )
            }
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
            {isLoading ? 'Sending...' : 'Send Invites'}
          </Button>
        </DialogActions>
      </Dialog>
  );
}

function AddUsersToGroupsModal({
  open,
  onClose,
  onUsersAdded,
  allUsers,
  groups,
}: AddUsersToGroupsModalProps) {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [selectedUsers, setSelectedUsers] = useState<GroupUser[]>([]);
  const [selectedGroups, setSelectedGroups] = useState<AppUserGroup[]>([]);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [snackbarState, setSnackbarState] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  const handleSnackbarClose = () => {
    setSnackbarState({ ...snackbarState, open: false });
  };

  const handleAddUsersToGroups = async () => {
    if (selectedUsers.length === 0 || selectedGroups.length === 0) return;

    setIsSubmitting(true);
    try {
      const userIds = selectedUsers
        .filter((user): user is GroupUser => user._id !== null)
        .map((user) => user._id);
      const groupIds = selectedGroups.map((group) => group._id);

      await addUsersToGroups({ userIds, groupIds });
      setSnackbarState({
        open: true,
        message: 'Users added to groups successfully',
        severity: 'success',
      });

      onUsersAdded();
      onClose();
    } catch (error) {
      setSnackbarState({
        open: true,
        message: 'Failed to add users to groups',
        severity: 'error',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const userChipStyle = {
    borderRadius: 0.75,
    height: 24,
    fontSize: '0.75rem',
    fontWeight: 500,
    bgcolor: isDark
      ? alpha(theme.palette.common.white, 0.85)
      : alpha(theme.palette.primary.main, 0.75),
    color: isDark
      ? alpha(theme.palette.primary.light, 0.1)
      : alpha(theme.palette.common.white, 0.9),
    border: isDark ? `1px solid ${alpha(theme.palette.primary.main, 0.4)}` : 'none',
    '& .MuiChip-deleteIcon': {
      color: isDark
        ? alpha(theme.palette.primary.dark, 0.7)
        : alpha(theme.palette.common.white, 0.7),
      '&:hover': {
        color: isDark ? theme.palette.primary.darker : theme.palette.primary.light,
      },
    },
  };

  const groupChipStyle = {
    borderRadius: 0.75,
    height: 24,
    fontSize: '0.75rem',
    fontWeight: 500,
    bgcolor: isDark ? alpha(theme.palette.info.main, 0.85) : alpha(theme.palette.info.light, 0.45),
    color: isDark ? alpha(theme.palette.info.light, 0.9) : theme.palette.info.dark,
    border: isDark ? `1px solid ${alpha(theme.palette.info.main, 0.4)}` : 'none',
    '& .MuiChip-deleteIcon': {
      color: isDark ? alpha(theme.palette.info.dark, 0.7) : alpha(theme.palette.info.main, 0.7),
      '&:hover': {
        color: isDark ? theme.palette.info.light : theme.palette.info.dark,
      },
    },
  };

  return (
    <Dialog
        open={open}
        onClose={onClose}
        maxWidth="sm"
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
              <Iconify
                icon={accountGroupIcon}
                width={18}
                height={18}
                sx={{ color: theme.palette.primary.main }}
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
              Add Users to Groups
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
            <Iconify icon="mdi:close" width={18} height={18} />
          </IconButton>
        </DialogTitle>

        <DialogContent sx={{ px: 3, py: 2.5 }}>
          <Box sx={{ mt: 0.5, mb: 3 }}>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                mb: 2,
                fontWeight: 400,
                fontSize: '0.875rem',
              }}
            >
              Select users and groups to manage permissions
            </Typography>

            {allUsers && (
              <Autocomplete<GroupUser, true>
                multiple
                limitTags={3}
                options={allUsers.filter((user) => Boolean(user?._id && user?.fullName))}
                getOptionLabel={(option) => {
                  if (!option._id || !option.fullName) return 'Unknown User';
                  return option.fullName;
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    placeholder="Select users"
                    sx={{
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1,
                        backgroundColor: isDark
                          ? alpha(theme.palette.background.paper, 0.6)
                          : theme.palette.background.paper,
                        '& fieldset': {
                          borderColor: alpha(theme.palette.divider, isDark ? 0.2 : 0.1),
                        },
                        '&:hover fieldset': {
                          borderColor: alpha(theme.palette.primary.main, 0.5),
                        },
                        '& .MuiOutlinedInput-input': {
                          padding: '10px 12px',
                        },
                      },
                    }}
                    InputProps={{
                      ...params.InputProps,
                      startAdornment: (
                        <>
                          <Box mr={1} display="flex" alignItems="center">
                            <Iconify
                              icon="mdi:account-search"
                              width={18}
                              height={18}
                              sx={{ color: theme.palette.text.secondary }}
                            />
                          </Box>
                          {params.InputProps.startAdornment}
                        </>
                      ),
                    }}
                  />
                )}
                onChange={(_event, newValue) => setSelectedUsers(newValue)}
                sx={{ mb: 2 }}
                renderTags={(value, getTagProps) =>
                  value.map((option, index) => (
                    <Chip
                      label={option.fullName || 'Unnamed User'}
                      {...getTagProps({ index })}
                      size="small"
                      sx={userChipStyle}
                    />
                  ))
                }
                renderOption={(props, option) => (
                  <MenuItem
                    {...props}
                    sx={{
                      py: 1,
                      px: 1.5,
                      borderRadius: 1,
                      my: 0.25,
                      '&:hover': {
                        bgcolor: isDark
                          ? alpha(theme.palette.action.hover, 0.1)
                          : theme.palette.action.hover,
                      },
                      '&.Mui-selected': {
                        bgcolor: isDark
                          ? alpha(theme.palette.primary.main, 0.2)
                          : alpha(theme.palette.primary.light, 0.1),
                        '&:hover': {
                          bgcolor: isDark
                            ? alpha(theme.palette.primary.main, 0.25)
                            : alpha(theme.palette.primary.light, 0.15),
                        },
                      },
                    }}
                  >
                    <Stack direction="row" alignItems="center" spacing={1.5}>
                      <Avatar
                        sx={{
                          width: 24,
                          height: 24,
                          fontSize: '0.75rem',
                          bgcolor: isDark
                            ? alpha(theme.palette.primary.main, 0.3)
                            : alpha(theme.palette.primary.light, 0.2),
                          color: isDark ? theme.palette.primary.light : theme.palette.primary.main,
                        }}
                      >
                        {getInitials(option.fullName)}
                      </Avatar>
                      <Box>
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: 500,
                            color: theme.palette.text.primary,
                          }}
                        >
                          {option.fullName || 'Unnamed User'}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {option.email || 'No email'}
                        </Typography>
                      </Box>
                    </Stack>
                  </MenuItem>
                )}
                PaperComponent={({ children }) => (
                  <Paper
                    sx={{
                      maxHeight: 220,
                      overflow: 'auto',
                      mt: 0.5,
                      borderRadius: 1,
                      boxShadow: isDark
                        ? `0 4px 20px ${alpha(theme.palette.common.black, 0.3)}`
                        : `0 4px 20px ${alpha(theme.palette.common.black, 0.1)}`,
                      border: `1px solid ${alpha(theme.palette.divider, 0.05)}`,
                    }}
                  >
                    {children}
                  </Paper>
                )}
              />
            )}

            <Autocomplete
              multiple
              limitTags={3}
              options={groups}
              getOptionLabel={(option) => option.name}
              renderInput={(params) => (
                <TextField
                  {...params}
                  placeholder="Select groups"
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      backgroundColor: isDark
                        ? alpha(theme.palette.background.paper, 0.6)
                        : theme.palette.background.paper,
                      '& fieldset': {
                        borderColor: alpha(theme.palette.divider, isDark ? 0.2 : 0.1),
                      },
                      '&:hover fieldset': {
                        borderColor: alpha(theme.palette.primary.main, 0.5),
                      },
                      '& .MuiOutlinedInput-input': {
                        padding: '10px 12px',
                      },
                    },
                  }}
                  InputProps={{
                    ...params.InputProps,
                    startAdornment: (
                      <>
                        <Box mr={1} display="flex" alignItems="center">
                          <Iconify
                            icon="mdi:folder-account"
                            width={18}
                            height={18}
                            sx={{ color: theme.palette.text.secondary }}
                          />
                        </Box>
                        {params.InputProps.startAdornment}
                      </>
                    ),
                  }}
                />
              )}
              onChange={(event, newValue) => setSelectedGroups(newValue)}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option.name}
                    {...getTagProps({ index })}
                    size="small"
                    sx={groupChipStyle}
                  />
                ))
              }
              renderOption={(props, option) => (
                <MenuItem
                  {...props}
                  sx={{
                    py: 1,
                    px: 1.5,
                    borderRadius: 1,
                    my: 0.25,
                    '&:hover': {
                      bgcolor: isDark
                        ? alpha(theme.palette.action.hover, 0.1)
                        : theme.palette.action.hover,
                    },
                    '&.Mui-selected': {
                      bgcolor: isDark
                        ? alpha(theme.palette.info.main, 0.2)
                        : alpha(theme.palette.info.light, 0.1),
                      '&:hover': {
                        bgcolor: isDark
                          ? alpha(theme.palette.info.main, 0.25)
                          : alpha(theme.palette.info.light, 0.15),
                      },
                    },
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Box
                      sx={{
                        width: 24,
                        height: 24,
                        borderRadius: '50%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: isDark
                          ? alpha(theme.palette.info.main, 0.3)
                          : alpha(theme.palette.info.light, 0.2),
                      }}
                    >
                      <Iconify
                        icon="mdi:account-group"
                        width={14}
                        height={14}
                        sx={{
                          color: isDark ? theme.palette.info.light : theme.palette.info.main,
                        }}
                      />
                    </Box>
                    <Typography
                      variant="body2"
                      sx={{
                        fontWeight: 500,
                        color: theme.palette.text.primary,
                      }}
                    >
                      {option.name}
                    </Typography>
                  </Stack>
                </MenuItem>
              )}
              PaperComponent={({ children }) => (
                <Paper
                  sx={{
                    maxHeight: 220,
                    overflow: 'auto',
                    mt: 0.5,
                    borderRadius: 1,
                    boxShadow: isDark
                      ? `0 4px 20px ${alpha(theme.palette.common.black, 0.3)}`
                      : `0 4px 20px ${alpha(theme.palette.common.black, 0.1)}`,
                    border: `1px solid ${alpha(theme.palette.divider, 0.05)}`,
                  }}
                >
                  {children}
                </Paper>
              )}
            />
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
            onClick={handleAddUsersToGroups}
            variant="contained"
            disableElevation
            disabled={selectedUsers.length === 0 || selectedGroups.length === 0 || isSubmitting}
            startIcon={
              isSubmitting ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Iconify icon={peopleIcon} width={18} height={18} />
              )
            }
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
            {isSubmitting ? 'Adding...' : 'Add to Groups'}
          </Button>
        </DialogActions>
      </Dialog>
  );
}

export default Users;
