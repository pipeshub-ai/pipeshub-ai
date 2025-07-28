import { useDispatch } from 'react-redux';
import React, { useState, useEffect } from 'react';
import closeIcon from '@iconify-icons/eva/close-fill';
import searchIcon from '@iconify-icons/eva/search-fill';
import emailIcon from '@iconify-icons/eva/email-outline';
import clockIcon from '@iconify-icons/eva/clock-outline';
import trashIcon from '@iconify-icons/eva/trash-2-outline';
import alertIcon from '@iconify-icons/eva/alert-triangle-fill';

import {
  Box,
  Chip,
  Table,
  Paper,
  Alert,
  Stack,
  alpha,
  Button,
  Dialog,
  Tooltip,
  TableRow,
  Snackbar,
  useTheme,
  InputBase,
  TableBody,
  TableCell,
  TableHead,
  Typography,
  IconButton,
  DialogTitle,
  DialogContent,
  DialogActions,
  TableContainer,
  TablePagination,
  CircularProgress,
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';

import { removeUser, resendInvite, getAllUsersWithGroups } from '../utils';
import { setInviteCount, decrementInvitesCount } from '../../../store/user-and-groups-slice';

import type { GroupUser } from '../types/group-details';
import type { SnackbarState } from '../types/organization-data';

export default function Invites() {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [users, setUsers] = useState<GroupUser[]>([]);
  const [page, setPage] = useState<number>(0);
  const [rowsPerPage, setRowsPerPage] = useState<number>(10);
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isActionLoading, setIsActionLoading] = useState<boolean>(false);
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean;
    userId: string | null;
    email: string;
    action: 'resend' | 'remove';
  }>({
    open: false,
    userId: null,
    email: '',
    action: 'resend',
  });

  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'info',
  });

  const dispatch = useDispatch();
  const { isAdmin } = useAdmin();

  useEffect(() => {
    const fetchUsers = async (): Promise<void> => {
      setIsLoading(true);
      try {
        const response: GroupUser[] = await getAllUsersWithGroups();
        // Filter for pending invitations based on hasLoggedIn field
        const pendingUsers = response.filter((user) => user.hasLoggedIn === false);
        dispatch(setInviteCount(pendingUsers.length));
        setUsers(pendingUsers);
      } catch (error) {
        // setSnackbar({ open: true, message: error.errorMessage, severity: 'error' });
      } finally {
        setIsLoading(false);
      }
    };
    fetchUsers();
  }, [dispatch]);

  const filteredUsers = users.filter((user) =>
    user?.email.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleChangePage = (event: unknown, newPage: number): void => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>): void => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const handleResendInvite = async (userId: string, email: string): Promise<void> => {
    setConfirmDialog({
      open: true,
      userId,
      email,
      action: 'resend',
    });
  };

  const handleRemoveUser = (userId: string, email: string): Promise<void> => {
    setConfirmDialog({
      open: true,
      userId,
      email,
      action: 'remove',
    });
    return Promise.resolve();
  };

  const handleConfirmAction = async (): Promise<void> => {
    const { userId, action } = confirmDialog;

    if (!userId) return;
    setIsActionLoading(true);
    try {
      if (action === 'resend') {
        await resendInvite(userId);
        setSnackbar({ open: true, message: 'Invitation resent successfully', severity: 'success' });
      } else {
        await removeUser(userId);
        setUsers(users.filter((user) => user?._id !== userId));
        dispatch(decrementInvitesCount());
        setSnackbar({
          open: true,
          message: 'Invitation removed successfully',
          severity: 'success',
        });
      }
    } catch (error) {
      // setSnackbar({ open: true, message: error.errorMessage, severity: 'error' });
    } finally {
      setIsActionLoading(false);
      setConfirmDialog({ ...confirmDialog, open: false });
    }
  };

  const handleCloseSnackbar = () => {
    setSnackbar({ ...snackbar, open: false });
  };

  const handleCloseConfirm = () => {
    setConfirmDialog({ ...confirmDialog, open: false });
  };

  // Function to get a consistent color for email
  // Function to get a consistent color for email
  const getGroupColor = (name: string) => {
    const colors = [
      theme.palette.primary.main,
      theme.palette.info.main,
      theme.palette.warning.main,
    ];

    // Simple hash function for consistent color without bitwise operations
    let hash = 0;
    // Using forEach instead of for loop with i++
    Array.from(name).forEach((char) => {
      // Using multiplication instead of bit shift
      hash = name.charCodeAt(name.indexOf(char)) + (hash * 32 - hash);
    });

    return colors[Math.abs(hash) % colors.length];
  };

  if (isLoading) {
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
          Loading invitations...
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      {/* Search Input */}
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
            placeholder="Search by email address"
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
      </Stack>

      {/* Empty State */}
      {filteredUsers.length === 0 && !isLoading && (
        <Paper
          elevation={0}
          sx={{
            py: 5,
            textAlign: 'center',
            borderRadius: 2,
            border: `1px solid ${alpha(theme.palette.grey[500], 0.16)}`,
          }}
        >
          <Iconify
            icon={emailIcon}
            width={48}
            height={48}
            sx={{ color: 'text.disabled', opacity: 0.5 }}
          />
          <Typography variant="h6" sx={{ mt: 2 }}>
            No Pending Invitations
          </Typography>
          <Typography variant="body2" sx={{ color: 'text.secondary', mt: 1, mb: 3 }}>
            {searchTerm
              ? 'No invitations match your search'
              : 'All invitations have been accepted or there are no pending invites'}
          </Typography>
        </Paper>
      )}

      {/* Invitations Table */}
      {filteredUsers.length > 0 && (
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
          <Table sx={{ minWidth: 650 }} aria-label="pending invitations table">
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
                  EMAIL
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
              {filteredUsers
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
                      <Stack direction="row" alignItems="center" spacing={2}>
                        <Box
                          sx={{
                            width: 40,
                            height: 40,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            borderRadius: '50%',
                            bgcolor: alpha(theme.palette.warning.main, 0.1),
                          }}
                        >
                          <Iconify
                            icon={emailIcon}
                            width={20}
                            height={20}
                            sx={{ color: theme.palette.warning.main }}
                          />
                        </Box>
                        <Box>
                          <Typography variant="subtitle2">{user.email}</Typography>
                          <Stack direction="row" alignItems="center" spacing={0.5}>
                            <Iconify
                              icon={clockIcon}
                              width={14}
                              height={14}
                              sx={{ color: theme.palette.warning.main }}
                            />
                            <Typography variant="caption" color="text.secondary">
                              Pending invitation
                            </Typography>
                          </Stack>
                        </Box>
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" flexWrap="wrap" gap={0.75}>
                        {user.groups && user.groups.length > 0 ? (
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
                    <TableCell>
                      <Stack direction="row" spacing={1}>
                        <Tooltip title="Resend invitation email">
                          <Button
                            size="small"
                            variant="outlined"
                            color="primary"
                            startIcon={<Iconify icon={emailIcon} />}
                            onClick={() => handleResendInvite(user._id || '', user.email)}
                            disabled={!isAdmin}
                            sx={{
                              borderRadius: 1.5,
                              fontSize: '0.75rem',
                              '&.Mui-disabled': {
                                cursor: 'not-allowed',
                                pointerEvents: 'auto',
                                opacity: 0.6,
                              },
                            }}
                          >
                            Resend
                          </Button>
                        </Tooltip>
                        <Tooltip title="Remove invitation">
                          <Button
                            size="small"
                            variant="outlined"
                            color="error"
                            startIcon={<Iconify icon={trashIcon} />}
                            onClick={() => handleRemoveUser(user._id || '', user.email)}
                            disabled={!isAdmin}
                            sx={{
                              borderRadius: 1.5,
                              fontSize: '0.75rem',
                              '&.Mui-disabled': {
                                cursor: 'not-allowed',
                                pointerEvents: 'auto',
                                opacity: 0.6,
                              },
                            }}
                          >
                            Remove
                          </Button>
                        </Tooltip>
                      </Stack>
                    </TableCell>
                  </TableRow>
                ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* Pagination */}
      {filteredUsers.length > 0 && (
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
      )}

      {/* Confirmation Dialog */}
      <Dialog
        open={confirmDialog.open}
        onClose={handleCloseConfirm}
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
              icon={confirmDialog.action === 'resend' ? emailIcon : alertIcon}
              width={24}
              height={24}
              sx={{
                color:
                  confirmDialog.action === 'resend'
                    ? theme.palette.primary.main
                    : theme.palette.warning.main,
              }}
            />
            <Typography variant="h6">
              {confirmDialog.action === 'resend' ? 'Resend Invitation' : 'Remove Invitation'}
            </Typography>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mt: 1 }}>
            {confirmDialog.action === 'resend'
              ? `Are you sure you want to resend the invitation to ${confirmDialog.email}?`
              : `Are you sure you want to remove the invitation for ${confirmDialog.email}? This action cannot be undone.`}
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={handleCloseConfirm} variant="outlined" sx={{ borderRadius: 1 }}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirmAction}
            variant="contained"
            color={confirmDialog.action === 'resend' ? 'primary' : 'error'}
            sx={{ borderRadius: 1 }}
            disabled={isActionLoading}
            startIcon={isActionLoading ? <CircularProgress size={16} color="inherit" /> : null}
          >
            {isActionLoading
              ? confirmDialog.action === 'resend'
                ? 'Resending...'
                : 'Removing...'
              : confirmDialog.action === 'resend'
                ? 'Resend'
                : 'Remove'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Snackbar */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{
            width: '100%',
            borderRadius: 1.5,
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}
