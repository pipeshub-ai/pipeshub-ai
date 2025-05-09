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
  CircularProgress,
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';

import {
  setCounts,
  decrementUserCount,
  updateInvitesCount,
} from '../../../store/userAndGroupsSlice';
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
  onUsersAdded: () => void;
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
            groupsCount: groups.length,
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

  const handleUsersAdded = async () => {
    try {
      const updatedUsers = await getAllUsersWithGroups();

      const loggedInUsers = updatedUsers.filter((user) => user?.email !== '' && user?.fullName);
      setUsers(loggedInUsers);
      setSnackbarState({
        open: true,
        message: 'Users added to groups successfully',
        severity: 'success',
      });
    } catch (error) {
      // setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
    }
  };

  const handleUsersInvited = async () => {
    try {
      const updatedUsers = await getAllUsersWithGroups();

      const loggedInUsers = updatedUsers.filter((user) => user.email !== null && user.fullName);
      setUsers(loggedInUsers);
      setSnackbarState({
        open: true,
        message: 'Users invited successfully',
        severity: 'success',
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
            px: 2,
            py: 0.5,
            borderRadius: 1.5,
            bgcolor: alpha(theme.palette.grey[500], 0.08),
            border: `1px solid ${alpha(theme.palette.grey[500], 0.16)}`,
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
              height: 40,
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
          border: `1px solid ${alpha(theme.palette.grey[500], 0.16)}`,
          mb: 2,
          overflow: 'hidden',
        }}
      >
        <Table sx={{ minWidth: 650 }} aria-label="users table">
          <TableHead>
            <TableRow sx={{ backgroundColor: alpha(theme.palette.primary.main, 0.04) }}>
              <TableCell sx={{ fontWeight: 600, py: 2 }}>USER</TableCell>
              <TableCell sx={{ fontWeight: 600, py: 2 }}>GROUPS</TableCell>
              <TableCell align="right" sx={{ fontWeight: 600, py: 2, width: 80 }}>
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
                      '&:hover': { bgcolor: alpha(theme.palette.primary.main, 0.02) },
                      transition: 'background-color 0.2s ease',
                    }}
                  >
                    <TableCell component="th" scope="row">
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
                                fontSize: '0.75rem',
                                fontWeight: 500,
                                bgcolor: alpha(theme.palette.primary.main, 0.08),
                                color: theme.palette.primary.main,
                                height: 24,
                                borderRadius: '6px',
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
                        fontSize: '0.75rem',
                        bgcolor: alpha(theme.palette.primary.main, 0.08),
                        color: theme.palette.primary.main,
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
      await inviteUsers({ emails, groupIds });

      // Only clear data after successful API call
      dispatch(updateInvitesCount(emails.length));
      setSnackbarState({ open: true, message: 'Users added successfully', severity: 'success' });

      // Clear emails after successful submission
      setEmails([]);
      setSelectedGroups([]);

      onUsersAdded();
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

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          padding: 1,
        },
      }}
    >
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Iconify
            icon={personIcon}
            width={24}
            height={24}
            sx={{ color: theme.palette.primary.main }}
          />
          <Typography variant="h6">Invite users</Typography>
        </Stack>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ mt: 2 }}>
          <Box
            sx={{
              border: error
                ? `1px solid ${theme.palette.error.main}`
                : '1px solid rgba(0, 0, 0, 0.23)',
              borderRadius: 1,
              p: 1,
              mb: error ? 0.5 : 2,
              minHeight: '100px',
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'flex-start',
              gap: 1,
              '&:focus-within': {
                borderColor: error ? theme.palette.error.main : theme.palette.primary.main,
                boxShadow: error
                  ? `0 0 0 2px ${theme.palette.error.main}14`
                  : `0 0 0 2px ${theme.palette.primary.main}14`,
              },
            }}
          >
            {/* Email chips */}
            {emails.map((email, index) => (
              <Chip
                key={index}
                label={email}
                onDelete={() => handleRemoveEmail(email)}
                deleteIcon={<Iconify icon={closeIcon} />}
                sx={{
                  m: 0.5,
                  bgcolor: theme.palette.primary.main,
                  color: theme.palette.primary.contrastText,
                  '& .MuiChip-deleteIcon': {
                    color: theme.palette.primary.contrastText,
                    '&:hover': {
                      color: theme.palette.primary.light,
                    },
                  },
                }}
              />
            ))}

            {/* Input field and Add button in a row */}
            <Box sx={{ display: 'flex', flexGrow: 1, alignItems: 'center', gap: 1 }}>
              <TextField
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={emails.length === 0 ? 'name@example.com' : ''}
                variant="standard"
                error={!!error}
                aria-invalid={!!error}
                inputProps={{
                  'aria-label': 'Add email addresses',
                }}
                sx={{
                  flexGrow: 1,
                  m: 0.5,
                  '& .MuiInput-underline:before': { borderBottom: 'none' },
                  '& .MuiInput-underline:hover:before': { borderBottom: 'none' },
                  '& .MuiInput-underline:after': { borderBottom: 'none' },
                }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={handleAddEmail}
                sx={{
                  minWidth: 'auto',
                  height: 32,
                  borderRadius: 1,
                  ml: 1,
                }}
              >
                Add
              </Button>
            </Box>
          </Box>

          {/* Error message */}
          {error && (
            <Typography variant="caption" color="error" sx={{ ml: 1, display: 'block', mb: 1 }}>
              {error}
            </Typography>
          )}

          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Your team members will receive an email with instructions to access the system.
          </Typography>
          <Autocomplete
            multiple
            options={groups}
            getOptionLabel={(option) => option.name}
            renderInput={(params) => <TextField {...params} label="Add to groups" />}
            onChange={(event, newValue) => setSelectedGroups(newValue)}
            sx={{
              '& .MuiOutlinedInput-root': {
                p: 1,
              },
            }}
          />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            New users will inherit the permissions given to the selected groups.
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2, pt: 1 }}>
        <Button onClick={onClose} variant="outlined" sx={{ borderRadius: 1 }}>
          Cancel
        </Button>
        <Button
          onClick={handleAddUsers}
          variant="contained"
          color="primary"
          startIcon={
            isLoading ? (
              <CircularProgress size={16} color="inherit" />
            ) : (
              <Iconify icon={emailIcon} />
            )
          }
          sx={{ borderRadius: 1 }}
          disabled={emails.length === 0 || isLoading}
        >
          {isLoading ? 'Sending...' : 'Send Invites'}
        </Button>
      </DialogActions>
      <Snackbar
        open={snackbarState.open}
        autoHideDuration={6000}
        onClose={handleSnackbarClose}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleSnackbarClose}
          severity={snackbarState.severity}
          sx={{ width: '100%' }}
        >
          {snackbarState.message}
        </Alert>
      </Snackbar>
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
  const [selectedUsers, setSelectedUsers] = useState<GroupUser[]>([]);
  const [selectedGroups, setSelectedGroups] = useState<AppUserGroup[]>([]);
  const [snackbarState, setSnackbarState] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  const handleSnackbarClose = () => {
    setSnackbarState({ ...snackbarState, open: false });
  };

  const handleAddUsersToGroups = async () => {
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
      // setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          padding: 1,
        },
      }}
    >
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Iconify
            icon={accountGroupIcon}
            width={24}
            height={24}
            sx={{ color: theme.palette.primary.main }}
          />
          <Typography variant="h6">Add Users to Groups</Typography>
        </Stack>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ mt: 2 }}>
          {allUsers && (
            <Autocomplete<GroupUser, true>
              multiple
              options={allUsers.filter((user) => Boolean(user?._id && user?.fullName))}
              getOptionLabel={(option) => {
                if (!option._id || !option.fullName) return 'Unknown User';
                return option.fullName;
              }}
              renderInput={(params) => <TextField {...params} label="Select Users" />}
              onChange={(_event, newValue) => setSelectedUsers(newValue)}
              sx={{ mb: 2 }}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option.fullName || 'Unnamed User'}
                    {...getTagProps({ index })}
                    sx={{
                      bgcolor: alpha(theme.palette.primary.main, 0.08),
                      color: theme.palette.primary.main,
                    }}
                  />
                ))
              }
              renderOption={(props, option) => (
                <li {...props}>
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Avatar
                      sx={{
                        width: 24,
                        height: 24,
                        fontSize: '0.75rem',
                        bgcolor: alpha(theme.palette.primary.main, 0.08),
                        color: theme.palette.primary.main,
                      }}
                    >
                      {getInitials(option.fullName)}
                    </Avatar>
                    <Box>
                      <Typography variant="body2">{option.fullName || 'Unnamed User'}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {option.email || 'No email'}
                      </Typography>
                    </Box>
                  </Stack>
                </li>
              )}
            />
          )}
          <Autocomplete
            multiple
            options={groups}
            getOptionLabel={(option) => option.name}
            renderInput={(params) => <TextField {...params} label="Select Groups" />}
            onChange={(event, newValue) => setSelectedGroups(newValue)}
            renderTags={(value, getTagProps) =>
              value.map((option, index) => (
                <Chip
                  label={option.name}
                  {...getTagProps({ index })}
                  sx={{
                    bgcolor: alpha(theme.palette.info.main, 0.08),
                    color: theme.palette.info.main,
                  }}
                />
              ))
            }
          />
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2, pt: 1 }}>
        <Button onClick={onClose} variant="outlined" sx={{ borderRadius: 1 }}>
          Cancel
        </Button>
        <Button
          onClick={handleAddUsersToGroups}
          variant="contained"
          color="primary"
          startIcon={<Iconify icon={peopleIcon} />}
          sx={{ borderRadius: 1 }}
        >
          Add to Groups
        </Button>
      </DialogActions>
      <Snackbar
        open={snackbarState.open}
        autoHideDuration={6000}
        onClose={handleSnackbarClose}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleSnackbarClose}
          severity={snackbarState.severity}
          sx={{ width: '100%' }}
        >
          {snackbarState.message}
        </Alert>
      </Snackbar>
    </Dialog>
  );
}

export default Users;
