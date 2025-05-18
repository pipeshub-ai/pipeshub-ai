import editIcon from '@iconify-icons/eva/edit-fill';
import infoIcon from '@iconify-icons/eva/info-fill';
import closeIcon from '@iconify-icons/eva/close-fill';
import peopleIcon from '@iconify-icons/eva/people-fill';
import searchIcon from '@iconify-icons/eva/search-fill';
import React, { useRef, useState, useEffect } from 'react';
import personIcon from '@iconify-icons/eva/person-add-fill';
import arrowBackIcon from '@iconify-icons/eva/arrow-back-fill';
import alertIcon from '@iconify-icons/eva/alert-triangle-fill';
import rightIcon from '@iconify-icons/eva/chevron-right-outline';
import verticalIcon from '@iconify-icons/eva/more-vertical-fill';
import personRemoveIcon from '@iconify-icons/eva/person-remove-fill';
import { useNavigate, useLocation, Link as RouterLink } from 'react-router-dom';

import {
  Box,
  Menu,
  Chip,
  Table,
  Paper,
  Alert,
  Stack,
  alpha,
  Button,
  Avatar,
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
  Typography,
  IconButton,
  Breadcrumbs,
  DialogTitle,
  Autocomplete,
  DialogContent,
  DialogActions,
  TableContainer,
  TablePagination,
  Link as MuiLink,
  CircularProgress,
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';

import {
  fetchAllUsers,
  addUsersToGroups,
  fetchGroupDetails,
  getUserIdFromToken,
  removeUserFromGroup,
  getAllUsersWithGroups,
} from '../utils';

import type { SnackbarState } from '../types/organization-data';
import type {
  AppUser,
  GroupUser,
  AppUserGroup,
  EditGroupModalProps,
  AddUsersToGroupsModalProps,
} from '../types/group-details';

export default function GroupDetails() {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const navigate = useNavigate();
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  const [group, setGroup] = useState<AppUserGroup | null>(null);
  const [groupUsers, setGroupUsers] = useState<AppUser[]>([]);
  const [page, setPage] = useState<number>(0);
  const [rowsPerPage, setRowsPerPage] = useState<number>(10);
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const [selectedUser, setSelectedUser] = useState<AppUser | null>(null);
  const [isEditModalOpen, setIsEditModalOpen] = useState<boolean>(false);
  const [isAddUsersModalOpen, setIsAddUsersModalOpen] = useState<boolean>(false);
  const [newUsers, setNewUsers] = useState<GroupUser[] | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [menuAnchorEl, setMenuAnchorEl] = useState<HTMLElement | null>(null);
  const [isConfirmDialogOpen, setIsConfirmDialogOpen] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [snackbarState, setSnackbarState] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });
  const { isAdmin } = useAdmin();

  const location = useLocation();
  const pathSegments = location?.pathname?.split('/') || [];
  const groupId = pathSegments.length > 0 ? pathSegments[pathSegments.length - 1] : null;

  const handleSnackbarClose = (): void => {
    setSnackbarState({ ...snackbarState, open: false });
  };

  const filterGroupUsers = (users: AppUser[], groupUserIds: string[]): AppUser[] =>
    users.filter((user: AppUser) => groupUserIds.includes(user._id));

  // Filter users based on search term with null checks
  const filteredUsers = groupUsers.filter(
    (user) =>
      (user?.fullName?.toLowerCase() || '').includes(searchTerm.toLowerCase()) ||
      (user?.email?.toLowerCase() || '').includes(searchTerm.toLowerCase())
  );

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);
      try {
        const orgId = await getUserIdFromToken();
        setUserId(orgId);
        const groupData = await fetchGroupDetails(groupId);
        const allUsers = await fetchAllUsers();

        const loggedInUsers = allUsers.filter((user) => user?.email !== null);
        const filteredUsersList = filterGroupUsers(loggedInUsers, groupData.users);
        const response = await getAllUsersWithGroups();
        setNewUsers(response);
        setGroup(groupData);
        setGroupUsers(filteredUsersList);
      } catch (error) {
        // setSnackbarState({
        //   open: true,
        //   message: error.errorMessage || 'Error fetching group data',
        //   severity: 'error',
        // });
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, [groupId]);

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, user: AppUser) => {
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
    if (selectedUser) {
      handleDeleteUser(selectedUser._id);
    }
    setIsConfirmDialogOpen(false);
  };

  const handleChangePage = (
    event: React.MouseEvent<HTMLButtonElement, MouseEvent> | null,
    newPage: number
  ) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const handleEditGroup = () => {
    setIsEditModalOpen(true);
  };

  const handleCloseEditModal = () => {
    setIsEditModalOpen(false);
  };

  const handleAddUsers = () => {
    setIsAddUsersModalOpen(true);
  };

  const handleCloseAddUsers = () => {
    setIsAddUsersModalOpen(false);
  };

  const handlePopoverOpen = (event: React.MouseEvent<HTMLElement>, user: AppUser) => {
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
    // Refresh user data after adding users
    try {
      const groupData = await fetchGroupDetails(groupId);
      const allUsers = await fetchAllUsers();
      const loggedInUsers = allUsers.filter((user) => user?.email !== null);
      const filteredUsersList = filterGroupUsers(loggedInUsers, groupData.users);
      setGroupUsers(filteredUsersList);
    } catch (error) {
      // setSnackbarState({
      //   open: true,
      //   message: 'Error refreshing user data, please reload',
      //   severity: 'error',
      // });
    }
  };

  const handleDeleteUser = async (deleteUserId: string) => {
    try {
      await removeUserFromGroup(deleteUserId, groupId);

      setGroupUsers((prevGroupUsers) => prevGroupUsers.filter((user) => user._id !== deleteUserId));
      setSnackbarState({
        open: true,
        message: 'User removed from group successfully',
        severity: 'success',
      });
    } catch (error) {
      // setSnackbarState({
      //   open: true,
      //   message: error.errorMessage || 'Error removing user',
      //   severity: 'error',
      // });
    }
  };

  const open = Boolean(anchorEl);

  // Function to get avatar color based on name with null check
  const getAvatarColor = (name: string | null | undefined) => {
    const colors = [
      theme.palette.primary.main,
      theme.palette.info.main,
      theme.palette.success.main,
      theme.palette.warning.main,
      theme.palette.error.main,
    ];

    // Default color if name is null or undefined
    if (!name) return colors[0];

    // Simple hash function using array methods instead of for loop with i++
    const hash = name.split('').reduce((acc, char) => char.charCodeAt(0) + (acc * 32 - acc), 0);

    return colors[Math.abs(hash) % colors.length];
  };

  // Get initials from full name with null check
  const getInitials = (fullName: string | null | undefined) => {
    if (!fullName) return '?';

    return fullName
      .split(' ')
      .map((n) => n[0])
      .join('')
      .toUpperCase();
  };

  if (isLoading) {
    return (
      <Box
        display="flex"
        flexDirection="column"
        justifyContent="center"
        alignItems="center"
        sx={{ height: 400, width: '100%' }}
      >
        <CircularProgress size={40} thickness={2.5} />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
          Loading group details...
        </Typography>
      </Box>
    );
  }

  if (!group) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <Typography variant="h6" color="text.secondary">
          Group not found
        </Typography>
        <Button
          variant="outlined"
          startIcon={<Iconify icon={arrowBackIcon} />}
          onClick={() => navigate('/account/company-settings/groups')}
          sx={{ mt: 2 }}
        >
          Back to Groups
        </Button>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 5 }}>
      {/* Header with Breadcrumbs */}
      <Stack spacing={3} sx={{ mb: 4 }}>
        <Breadcrumbs
          separator={<Iconify icon={rightIcon} width={16} height={16} />}
          aria-label="breadcrumb"
        >
          <MuiLink
            component={RouterLink}
            to="/account/company-settings/groups"
            underline="hover"
            sx={{
              display: 'flex',
              alignItems: 'center',
              color: 'text.secondary',
              '&:hover': { color: 'text.primary' },
            }}
          >
            <Iconify icon={peopleIcon} width={18} height={18} sx={{ mr: 0.5 }} />
            Groups
          </MuiLink>
          <Typography color="text.primary" sx={{ display: 'flex', alignItems: 'center' }}>
            {group.name}
          </Typography>
        </Breadcrumbs>

        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={2}
          justifyContent="space-between"
          alignItems={{ xs: 'flex-start', sm: 'center' }}
        >
          <Stack direction="row" alignItems="center" spacing={2}>
            <Avatar
              sx={{
                bgcolor: alpha(theme.palette.primary.main, 0.1),
                color: theme.palette.primary.main,
                width: 48,
                height: 48,
              }}
            >
              <Iconify icon={peopleIcon} width={24} height={24} />
            </Avatar>
            <Box>
              <Typography variant="h4">{group.name}</Typography>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 0.5 }}>
                <Chip
                  size="small"
                  label={`${groupUsers.length} ${groupUsers.length === 1 ? 'Member' : 'Members'}`}
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
                {group.type && (
                  <Chip
                    size="small"
                    label={group.type === 'custom' ? 'Custom Group' : group.type}
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
                )}
              </Stack>
            </Box>
          </Stack>

          <Stack direction="row" spacing={1.5}>
            <Button
              variant="outlined"
              startIcon={<Iconify icon={personIcon} />}
              onClick={handleAddUsers}
              sx={{ borderRadius: 1.5 }}
            >
              Add Users
            </Button>
            <Button
              variant="contained"
              startIcon={<Iconify icon={editIcon} />}
              onClick={handleEditGroup}
              sx={{ borderRadius: 1.5 }}
            >
              Edit Group
            </Button>
          </Stack>
        </Stack>
      </Stack>

      {/* Search Field */}
      {groupUsers.length > 0 && (
        <Paper
          elevation={0}
          sx={{
            display: 'flex',
            alignItems: 'center',
            width: { xs: '100%', sm: '40%' },
            px: 2,
            py: 0.5,
            mb: 3,
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
          <TextField
            placeholder="Search members"
            variant="standard"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            fullWidth
            InputProps={{
              disableUnderline: true,
            }}
            sx={{
              '& .MuiInputBase-input': {
                py: 1,
                fontSize: '0.875rem',
              },
            }}
          />
          {searchTerm && (
            <IconButton size="small" onClick={() => setSearchTerm('')} sx={{ p: 0.5 }}>
              <Iconify icon={closeIcon} width={16} height={16} />
            </IconButton>
          )}
        </Paper>
      )}

      {/* Empty State */}
      {groupUsers.length === 0 && (
        <Paper
          elevation={0}
          sx={{
            p: 5,
            textAlign: 'center',
            borderRadius: 2,
            border: `1px solid ${alpha(theme.palette.grey[500], 0.16)}`,
          }}
        >
          <Iconify
            icon={peopleIcon}
            width={48}
            height={48}
            sx={{ color: 'text.disabled', opacity: 0.5 }}
          />
          <Typography variant="h6" sx={{ mt: 2 }}>
            No Users in this Group
          </Typography>
          <Typography variant="body2" sx={{ color: 'text.secondary', mt: 1, mb: 3 }}>
            Add users to this group to get started
          </Typography>
          <Button
            variant="contained"
            startIcon={<Iconify icon={personIcon} />}
            onClick={handleAddUsers}
            sx={{ borderRadius: 1.5 }}
          >
            Add Users
          </Button>
        </Paper>
      )}

      {/* Users Table */}
      {groupUsers.length > 0 && (
        <>
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
            <Table sx={{ minWidth: 650 }} aria-label="group members table">
              <TableHead>
                <TableRow sx={{ backgroundColor: alpha(theme.palette.primary.main, 0.04) }}>
                  <TableCell sx={{ fontWeight: 600, py: 2 }}>USER</TableCell>
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
                                {user.fullName || 'Invited User'}
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
                        </TableCell>
                        <TableCell align="right">
                          <Tooltip title="User options">
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
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={2} align="center" sx={{ py: 4 }}>
                      <Box sx={{ textAlign: 'center' }}>
                        <Iconify
                          icon={searchIcon}
                          width={40}
                          height={40}
                          sx={{ color: 'text.secondary', mb: 1, opacity: 0.5 }}
                        />
                        <Typography variant="subtitle1" sx={{ mb: 0.5 }}>
                          No users found
                        </Typography>
                        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                          Try adjusting your search criteria
                        </Typography>
                      </Box>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>

          {/* Pagination */}
          <TablePagination
            component={Paper}
            count={filteredUsers.length}
            page={page}
            onPageChange={handleChangePage}
            rowsPerPage={rowsPerPage}
            onRowsPerPageChange={handleChangeRowsPerPage}
            rowsPerPageOptions={[5, 10, 25, { label: 'All', value: filteredUsers.length }]}
            sx={{
              borderRadius: 2,
              boxShadow: 'none',
              border: `1px solid ${alpha(theme.palette.grey[500], 0.16)}`,
              '.MuiTablePagination-selectLabel, .MuiTablePagination-displayedRows': {
                fontSize: '0.875rem',
              },
            }}
          />
        </>
      )}

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
            p: 2.5,
            maxWidth: 320,
          },
        }}
      >
        {selectedUser && (
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
                  {selectedUser.fullName || 'Invited User'}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {selectedUser.email || 'No email'}
                </Typography>
              </Box>
            </Stack>

            {selectedUser.designation && (
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                {selectedUser.designation}
              </Typography>
            )}

            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon={editIcon} />}
              onClick={() => {
                if (selectedUser._id === userId) {
                  navigate('/account/company-settings/personal-profile');
                } else {
                  navigate(`/account/company-settings/user-profile/${selectedUser._id}`);
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

      {/* User Options Menu */}
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
          <Iconify icon={personRemoveIcon} width={20} height={20} sx={{ mr: 1.5 }} />
          Remove from Group
        </MenuItem>
      </Menu>

      {/* Add Users Modal */}
      <AddUsersToGroupsModal
        open={isAddUsersModalOpen}
        onClose={handleCloseAddUsers}
        onUsersAdded={handleUsersAdded}
        allUsers={newUsers}
        group={groupId}
        theme={theme}
      />

      {/* Edit Group Modal */}
      <EditGroupModal
        open={isEditModalOpen}
        onClose={handleCloseEditModal}
        groupId={groupId}
        groupName={group.name}
        theme={theme}
      />

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
            <Typography variant="h6">Remove User from Group</Typography>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mt: 1 }}>
            Are you sure you want to remove <strong>{selectedUser?.fullName || 'this user'}</strong>{' '}
            from the <strong>{group.name}</strong> group?
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
    </Box>
  );
}

function EditGroupModal({
  open,
  onClose,
  groupId,
  groupName,
  theme,
}: EditGroupModalProps & { theme: any }) {
  const [snackbarState, setSnackbarState] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  const handleSnackbarClose = () => {
    setSnackbarState({ ...snackbarState, open: false });
  };

  const handleSave = async () => {
    try {
      setSnackbarState({
        open: true,
        message: 'Group updated successfully',
        severity: 'success',
      });
      onClose();
    } catch (error) {
      // setSnackbarState({
      //   open: true,
      //   message: error.errorMessage || 'Error updating group',
      //   severity: 'error',
      // });
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(1px)',
          backgroundColor: alpha(theme.palette.common.black, 0.3),
        },
      }}
      PaperProps={{
        sx: {
          borderRadius: 2,
          p: 0,
        },
      }}
    >
      <Box
        sx={{
          pt: 3,
          px: 3,
          pb: 1,
          borderBottom: `1px solid ${alpha(theme.palette.grey[500], 0.12)}`,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={2}>
          <Avatar
            sx={{
              width: 48,
              height: 48,
              bgcolor: alpha(theme.palette.primary.main, 0.08),
              color: theme.palette.primary.main,
            }}
          >
            <Iconify icon={editIcon} width={24} height={24} />
          </Avatar>
          <Box>
            <Typography variant="h6">Edit Group</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Update settings for {groupName}
            </Typography>
          </Box>
        </Stack>
      </Box>

      <DialogContent sx={{ pt: 3, px: 3 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Group editing functionality is coming soon. You will be able to update group settings,
          permissions and more.
        </Typography>

        <Box
          sx={{
            mt: 2,
            p: 2,
            bgcolor: alpha(theme.palette.info.main, 0.04),
            borderRadius: 2,
            border: `1px solid ${alpha(theme.palette.info.main, 0.12)}`,
          }}
        >
          <Stack direction="row" spacing={2} alignItems="center">
            <Iconify
              icon={infoIcon}
              width={20}
              height={20}
              sx={{ color: theme.palette.info.main }}
            />
            <Typography variant="body2" color="text.secondary">
              This feature is currently under development.
            </Typography>
          </Stack>
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 2, bgcolor: alpha(theme.palette.grey[500], 0.04) }}>
        <Button
          onClick={onClose}
          variant="outlined"
          sx={{
            borderRadius: 1.5,
            px: 2,
          }}
        >
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          color="primary"
          sx={{
            borderRadius: 1.5,
            px: 2,
          }}
        >
          Save Changes
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
          variant="filled"
          sx={{ width: '100%', borderRadius: 1.5 }}
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
  group,
  theme,
}: AddUsersToGroupsModalProps & { theme: any }) {
  const [selectedUsers, setSelectedUsers] = useState<GroupUser[]>([]);
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
      if (!group) {
        throw new Error('Group ID is required');
      }

      if (selectedUsers.length === 0) {
        setSnackbarState({
          open: true,
          message: 'Please select at least one user',
          severity: 'error',
        });
        return;
      }

      const userIds = selectedUsers.filter((user) => user._id !== null).map((user) => user._id);

      await addUsersToGroups({ userIds, groupIds: [group] });
      setSnackbarState({
        open: true,
        message: `${userIds.length} ${userIds.length === 1 ? 'user' : 'users'} added to group successfully`,
        severity: 'success',
      });
      onUsersAdded();
      onClose();
    } catch (error) {
      // setSnackbarState({
      //   open: true,
      //   message: error.errorMessage || 'Error adding users to group',
      //   severity: 'error',
      // });
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(1px)',
          backgroundColor: alpha(theme.palette.common.black, 0.3),
        },
      }}
      PaperProps={{
        sx: {
          borderRadius: 2,
          p: 0,
        },
      }}
    >
      <Box
        sx={{
          pt: 3,
          px: 3,
          pb: 1,
          borderBottom: `1px solid ${alpha(theme.palette.grey[500], 0.12)}`,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={2}>
          <Avatar
            sx={{
              width: 48,
              height: 48,
              bgcolor: alpha(theme.palette.primary.main, 0.08),
              color: theme.palette.primary.main,
            }}
          >
            <Iconify icon={personIcon} width={24} height={24} />
          </Avatar>
          <Box>
            <Typography variant="h6">Add Users to Group</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Select users to add to this group
            </Typography>
          </Box>
        </Stack>
      </Box>

      <DialogContent sx={{ pt: 3, px: 3 }}>
        {allUsers ? (
          <Autocomplete<GroupUser, true>
            multiple
            options={allUsers.filter((user) => user._id !== null)}
            getOptionLabel={(option: GroupUser) => option?.fullName ?? 'Invited User'}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Select Users"
                placeholder="Search by name or email"
                InputLabelProps={{ shrink: true }}
              />
            )}
            onChange={(_event, newValue: GroupUser[]) => setSelectedUsers(newValue)}
            renderTags={(value, getTagProps) =>
              value.map((option, index) => (
                <Chip
                  label={option.fullName || 'Invited User'}
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
                      width: 28,
                      height: 28,
                      fontSize: '0.75rem',
                      bgcolor: alpha(theme.palette.primary.main, 0.08),
                      color: theme.palette.primary.main,
                    }}
                  >
                    {option.fullName?.charAt(0) || '?'}
                  </Avatar>
                  <Box>
                    <Typography variant="body2">{option.fullName || 'Invited User'}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {option.email || 'No email'}
                    </Typography>
                  </Box>
                </Stack>
              </li>
            )}
            sx={{
              '& .MuiOutlinedInput-root': {
                '& fieldset': {
                  borderColor: alpha(theme.palette.grey[500], 0.32),
                },
                '&:hover fieldset': {
                  borderColor: theme.palette.primary.main,
                },
              },
            }}
          />
        ) : (
          <Box sx={{ display: 'flex', justifyContent: 'center', my: 2 }}>
            <CircularProgress size={32} />
          </Box>
        )}

        <Box
          sx={{
            mt: 3,
            p: 2,
            bgcolor: alpha(theme.palette.info.main, 0.04),
            borderRadius: 2,
            border: `1px solid ${alpha(theme.palette.info.main, 0.12)}`,
          }}
        >
          <Stack direction="row" spacing={2} alignItems="center">
            <Iconify
              icon={infoIcon}
              width={20}
              height={20}
              sx={{ color: theme.palette.info.main }}
            />
            <Typography variant="body2" color="text.secondary">
              Users added to this group will inherit all permissions associated with it.
            </Typography>
          </Stack>
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 2, bgcolor: alpha(theme.palette.grey[500], 0.04) }}>
        <Button
          onClick={onClose}
          variant="outlined"
          sx={{
            borderRadius: 1.5,
            px: 2,
          }}
        >
          Cancel
        </Button>
        <Button
          onClick={handleAddUsersToGroups}
          variant="contained"
          color="primary"
          startIcon={<Iconify icon={personIcon} />}
          sx={{
            borderRadius: 1.5,
            px: 2,
          }}
        >
          Add to Group
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
          variant="filled"
          sx={{ width: '100%', borderRadius: 1.5 }}
        >
          {snackbarState.message}
        </Alert>
      </Snackbar>
    </Dialog>
  );
}
