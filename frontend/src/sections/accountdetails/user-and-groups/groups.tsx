import { z as zod } from 'zod';
import { useForm } from 'react-hook-form';
import { useDispatch } from 'react-redux';
import { useNavigate } from 'react-router-dom';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import React, { useState, useEffect } from 'react';
import infoIcon from '@iconify-icons/eva/info-fill';
import plusIcon from '@iconify-icons/eva/plus-fill';
import { zodResolver } from '@hookform/resolvers/zod';
import closeIcon from '@iconify-icons/eva/close-fill';
import searchIcon from '@iconify-icons/eva/search-fill';
import trashIcon from '@iconify-icons/eva/trash-2-outline';
import alertIcon from '@iconify-icons/eva/alert-triangle-fill';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import verticalIcon from '@iconify-icons/eva/more-vertical-fill';

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
  Tooltip,
  TableRow,
  Snackbar,
  MenuItem,
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
import { Form, Field } from 'src/components/hook-form';

import { allGroups, createGroup, deleteGroup } from '../utils';
import { decrementGroupCount, incrementGroupCount } from '../../../store/user-and-groups-slice';

import type { AppUserGroup, SnackbarState } from '../types/group-details';

const CreateGroupSchema = zod.object({
  name: zod.string().min(1, { message: 'Group name is required!' }),
  type: zod.string().default('custom'),
});

type CreateGroupFormData = zod.infer<typeof CreateGroupSchema>;

export default function Groups() {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [groups, setGroups] = useState<AppUserGroup[]>([]);
  const [page, setPage] = useState<number>(0);
  const [rowsPerPage, setRowsPerPage] = useState<number>(10);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  const [menuAnchorEl, setMenuAnchorEl] = useState<null | HTMLElement>(null);
  const [selectedGroup, setSelectedGroup] = useState<AppUserGroup | null>(null);
  const [isConfirmDialogOpen, setIsConfirmDialogOpen] = useState<boolean>(false);
  const [snackbarState, setSnackbarState] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { isAdmin } = useAdmin();

  const handleSnackbarClose = () => {
    setSnackbarState({ ...snackbarState, open: false });
  };

  const methods = useForm<CreateGroupFormData>({
    resolver: zodResolver(CreateGroupSchema),
    defaultValues: {
      name: '',
      type: 'custom',
    },
  });

  const {
    handleSubmit,
    formState: { isSubmitting },
    reset,
  } = methods;

  useEffect(() => {
    const loadGroups = async () => {
      setLoading(true);
      try {
        const data = await allGroups();
        // Sort groups alphabetically by name for better user experience
        const sortedGroups = [...data].sort((a, b) => a.name.localeCompare(b.name));
        setGroups(sortedGroups);
      } catch (error) {
        // setSnackbarState({
        //   open: true,
        //   message: error.errorMessage || 'Error loading groups',
        //   severity: 'error',
        // });
      } finally {
        setLoading(false);
      }
    };

    loadGroups();
  }, []);

  const filteredGroups = groups.filter((group) =>
    group.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleChangePage = (
    event: React.MouseEvent<HTMLButtonElement> | null,
    newPage: number
  ): void => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>): void => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const handleCreateGroup = (): void => {
    setIsCreateModalOpen(true);
  };

  const handleCloseCreateModal = (): void => {
    setIsCreateModalOpen(false);
    reset();
    setErrorMsg('');
  };

  const handleDeleteGroup = async (groupId: string): Promise<void> => {
    try {
      await deleteGroup(groupId);
      setGroups((prevGroups) => prevGroups.filter((group) => group._id !== groupId));
      dispatch(decrementGroupCount());
      setSnackbarState({ open: true, message: 'Group deleted successfully', severity: 'success' });
    } catch (error) {
      // setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
    }
  };

  const onSubmit = async (data: CreateGroupFormData): Promise<void> => {
    try {
      await createGroup(data);
      // Fetch updated groups and maintain sort order
      const updatedGroups = await allGroups();
      const sortedGroups = [...updatedGroups].sort((a, b) => a.name.localeCompare(b.name));
      setGroups(sortedGroups);
      dispatch(incrementGroupCount());
      handleCloseCreateModal();
      setSnackbarState({ open: true, message: 'Group created successfully', severity: 'success' });
    } catch (error) {
      setErrorMsg(error.errorMessage || 'Failed to create group. Please try again.');
      // setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
    }
  };

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, group: AppUserGroup): void => {
    setMenuAnchorEl(event.currentTarget);
    setSelectedGroup(group);
  };

  const handleMenuClose = (): void => {
    setMenuAnchorEl(null);
  };

  const handleRemoveGroup = (): void => {
    if (selectedGroup) {
      setIsConfirmDialogOpen(true);
    }
    handleMenuClose();
  };

  const handleConfirmRemoveGroup = (): void => {
    if (selectedGroup) {
      handleDeleteGroup(selectedGroup._id);
    }
    setIsConfirmDialogOpen(false);
  };

  const handleViewGroup = (): void => {
    if (selectedGroup) {
      navigate(`/account/company-settings/groups/${selectedGroup._id}`);
    }
    handleMenuClose();
  };

  // Function to get a consistent color for groups
  const getGroupColor = (name: string) => {
    const colors = [
      theme.palette.primary.main,
      theme.palette.info.main,
      theme.palette.success.main,
      theme.palette.warning.main,
    ];

    // Simple hash function using reduce instead of for loop with i++
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
          Loading groups...
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
            placeholder="Search by group name"
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

        <Button
          variant='outlined'
          color="primary"
          onClick={handleCreateGroup}
          startIcon={<Iconify icon={accountGroupIcon} width={18} height={18} />}
          size="medium"
          sx={{
            borderRadius: 1.5,
            fontSize: '0.8125rem',
            height: 38,
            fontWeight: 500,
            px: 2,
            border: isDark ? `1px solid ${alpha(theme.palette.primary.main, 0.5)}` : 'none',
            bgcolor: isDark ? 'transparent' : alpha(theme.palette.primary.lighter, 0.1),
            '&:hover': {
              bgcolor: isDark
                ? alpha(theme.palette.primary.main, 0.1)
                : alpha(theme.palette.primary.lighter, 0.2),
            },
          }}
        >
          Create Group
        </Button>
      </Stack>

      {/* Groups Table */}
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
        <Table sx={{ minWidth: 650 }} aria-label="groups table">
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
                GROUP
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
                MEMBERS
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
            {filteredGroups.length > 0 ? (
              filteredGroups
                .slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)
                .map((group) => (
                  <TableRow
                    key={group._id}
                    sx={{
                      '&:last-child td, &:last-child th': { border: 0 },
                      '&:hover': {
                        bgcolor: isDark
                          ? alpha(theme.palette.primary.dark, 0.05)
                          : alpha(theme.palette.primary.lighter, 0.05),
                      },
                      transition: 'background-color 0.2s ease',
                    }}
                    onClick={() => navigate(`/account/company-settings/groups/${group._id}`)}
                  >
                  <TableCell component="th" scope="row" sx={{ py: 1.5 }}>
                      <Stack direction="row" alignItems="center" spacing={2}>
                        <Avatar
                          sx={{
                            bgcolor: getGroupColor(group.name),
                            transition: 'transform 0.2s ease',
                            '&:hover': { transform: 'scale(1.1)' },
                          }}
                        >
                          <Iconify icon={accountGroupIcon} width={22} height={22} />
                        </Avatar>
                        <Box>
                          <Typography variant="subtitle2">{group.name}</Typography>
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ fontSize: '0.75rem' }}
                          >
                            {group.type === 'custom' ? 'Custom' : group.type}
                          </Typography>
                        </Box>
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" alignItems="center" spacing={1}>
                        <Chip
                          label={group.users.length}
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
                        <Typography variant="body2">
                          {group.users.length === 1 ? 'user' : 'users'}
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                      <Tooltip title="Group options">
                        <IconButton
                          onClick={(e) => {
                            e.stopPropagation();
                            handleMenuOpen(e, group);
                          }}
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
                <TableCell colSpan={3} align="center" sx={{ py: 4 }}>
                  <Box sx={{ textAlign: 'center' }}>
                    <Iconify
                      icon={accountGroupIcon}
                      width={40}
                      height={40}
                      sx={{ color: 'text.secondary', mb: 1, opacity: 0.5 }}
                    />
                    <Typography variant="subtitle1" sx={{ mb: 0.5 }}>
                      No groups found
                    </Typography>
                    <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                      {searchTerm
                        ? 'Try adjusting your search criteria'
                        : 'Create a group to get started'}
                    </Typography>
                  </Box>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Pagination - Implemented in frontend since backend sends all groups at once */}
      {filteredGroups.length > 0 && (
        <TablePagination
          component={Paper}
          count={filteredGroups.length}
          page={page}
          onPageChange={handleChangePage}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={handleChangeRowsPerPage}
          rowsPerPageOptions={[5, 10, 25, { label: 'All', value: filteredGroups.length }]}
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

      {/* Group Options Menu */}
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
        <MenuItem onClick={handleViewGroup}>
          <Iconify icon={eyeIcon} width={20} height={20} sx={{ mr: 1.5 }} />
          View Group
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
          onClick={handleRemoveGroup}
        >
          <Iconify icon={trashIcon} width={20} height={20} sx={{ mr: 1.5 }} />
          Remove Group
        </MenuItem>
      </Menu>

      {/* Create Group Dialog */}
      <Dialog
        open={isCreateModalOpen}
        onClose={handleCloseCreateModal}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: 2,
            maxWidth: 480,
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
              <Iconify icon={accountGroupIcon} width={24} height={24} />
            </Avatar>
            <Box>
              <Typography variant="h6">Create New Group</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                Create a group to organize users with similar permissions
              </Typography>
            </Box>
          </Stack>
        </Box>

        <Form methods={methods} onSubmit={handleSubmit(onSubmit)}>
          <DialogContent sx={{ pt: 3, px: 3 }}>
            {!!errorMsg && (
              <Alert
                severity="error"
                sx={{
                  mb: 3,
                  borderRadius: 1,
                }}
                onClose={() => setErrorMsg('')}
              >
                {errorMsg}
              </Alert>
            )}

            <Field.Text
              name="name"
              label="Group Name"
              fullWidth
              placeholder="Enter a descriptive name for your group"
              InputLabelProps={{ shrink: true }}
              helperText="Group names must be unique"
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1.5,
                },
                mb: 2,
              }}
            />

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
                  After creating the group, you can add users and set permissions.
                </Typography>
              </Stack>
            </Box>
          </DialogContent>

          <DialogActions sx={{ px: 3, py: 2, bgcolor: alpha(theme.palette.grey[500], 0.04) }}>
            <Button
              onClick={handleCloseCreateModal}
              variant="outlined"
              sx={{
                borderRadius: 1.5,
                px: 2,
              }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="contained"
              color="primary"
              disabled={isSubmitting}
              startIcon={
                isSubmitting ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <Iconify icon={plusIcon} />
                )
              }
              sx={{
                borderRadius: 1.5,
                px: 2,
              }}
            >
              {isSubmitting ? 'Creating...' : 'Create Group'}
            </Button>
          </DialogActions>
        </Form>
      </Dialog>

      {/* Confirm Delete Dialog */}
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
            <Typography variant="h6">Confirm Group Removal</Typography>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mt: 1 }}>
            Are you sure you want to remove <strong>{selectedGroup?.name}</strong>? This will also
            remove all users from this group.
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
            onClick={handleConfirmRemoveGroup}
            variant="contained"
            color="error"
            sx={{ borderRadius: 1 }}
          >
            Remove
          </Button>
        </DialogActions>
      </Dialog>

      {/* Snackbar Alert */}
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
