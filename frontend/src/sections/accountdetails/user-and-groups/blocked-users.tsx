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
import folderAccountIcon from '@iconify-icons/mdi/folder-account';
import accountSeachIcon from '@iconify-icons/mdi/account-search';

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
    CircularProgress,
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';
import { useUserEmails } from 'src/hooks/use-user-emails';
import unblockIcon from '@iconify-icons/mdi/account-check-outline';


import {
    setCounts,
    decrementUserCount,
    updateInvitesCount,
    decrementBlockedUserCount,
} from '../../../store/user-and-groups-slice';
import {
    allGroups,
    removeUser,
    inviteUsers,
    addUsersToGroups,
    getUserIdFromToken,
    getAllUsersWithGroups,
    allblockedUsers,
    unblockUser,
} from '../utils';

import type { SnackbarState } from '../types/organization-data';
import type { GroupUser, AppUserGroup, AddUserModalProps } from '../types/group-details';

// interface AddUsersToGroupsModalProps {
//     open: boolean;
//     onClose: () => void;
//     onUsersAdded: (message?: string) => void;
//     allUsers: GroupUser[] | null;
//     groups: AppUserGroup[];
// }

// Get initials from full name
const getInitials = (fullName: string) =>
    fullName
        ?.split(' ')
        ?.map((n) => n[0])
        ?.join('')
        ?.toUpperCase();

const BlockedUsers = () => {
    const theme = useTheme();
    const isDark = theme.palette.mode === 'dark';
    const [users, setUsers] = useState<GroupUser[]>([]);
    const [groups, setGroups] = useState<AppUserGroup[]>([]);
    const [page, setPage] = useState<number>(0);
    const [rowsPerPage, setRowsPerPage] = useState<number>(10);
    const [searchTerm, setSearchTerm] = useState<string>('');
    // const [isAddUserModalOpen, setIsAddUserModalOpen] = useState<boolean>(false);
    // const [isAddUsersToGroupsModalOpen, setIsAddUsersToGroupsModalOpen] = useState<boolean>(false);
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

    // const navigate = useNavigate();
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);
    const dispatch = useDispatch();
    const { isAdmin } = useAdmin();
    const { userEmails, emailLoading, fetchUserEmail } = useUserEmails({
        onError: () => {
            setSnackbarState({
                open: true,
                message: 'Failed to fetch email',
                severity: 'error',
            });
        },
    });

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
                const blockedUsers = await allblockedUsers();

                const loggedInUsers = blockedUsers.filter(
                    (user) => user?.email !== null && user.fullName && user.hasLoggedIn === true
                );
                const allloggedInUsers = response.filter(
                    (user) => user?.email !== null && user.fullName && user.hasLoggedIn === true
                );
                const pendingUsers = response.filter((user) => user.hasLoggedIn === false);

                dispatch(
                    setCounts({
                        usersCount: allloggedInUsers.length,
                        groupsCount: groupsData.length,
                        invitesCount: pendingUsers.length,
                        blockedUsersCount: blockedUsers.length,
                    })
                );
                setUsers(blockedUsers);
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

    const handleUnblockUser = async (unblockUserId: string): Promise<void> => {
        try {
            await unblockUser(unblockUserId);


            const updatedUsers = await allblockedUsers();

            const loggedInUsers = updatedUsers.filter((user) => user.email !== null && user.fullName);
            setUsers(loggedInUsers);
            dispatch(decrementBlockedUserCount());

            setSnackbarState({ open: true, message: 'User unblocked successfully', severity: 'success' });
        } catch (error) {
            setSnackbarState({ open: true, message: error.errorMessage, severity: 'error' });
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



    const handleMenuClose = () => {
        setMenuAnchorEl(null);
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
        const hash = name?.split('').reduce((acc, char) => char?.charCodeAt(0) + (acc * 32 - acc), 0);

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
                        placeholder="Search users by name"
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
                            {/* <TableCell
                                sx={{
                                    fontWeight: 600,
                                    py: 1.5,
                                    fontSize: '0.75rem',
                                    letterSpacing: '0.5px',
                                    opacity: 0.8,
                                }}
                            >
                                GROUPS
                            </TableCell> */}
                            <TableCell
                                align="center"
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
                                                        {userEmails[user._id] ? (
                                                            <Typography
                                                                variant="body2"
                                                                color="text.secondary"
                                                                sx={{ fontSize: '0.75rem' }}
                                                            >
                                                                {userEmails[user._id]}
                                                            </Typography>
                                                        ) : (
                                                            <Button
                                                                size="small"
                                                                variant="text"
                                                                onClick={() => fetchUserEmail(user._id)}
                                                                disabled={emailLoading[user._id]}
                                                                sx={{
                                                                    fontSize: '0.75rem',
                                                                    minWidth: 'auto',
                                                                    p: 0.5,
                                                                    textTransform: 'none',
                                                                    color: 'text.secondary',
                                                                    '&:hover': {
                                                                        bgcolor: 'transparent',
                                                                    },
                                                                }}
                                                            >
                                                                {emailLoading[user._id] ? (
                                                                    <Stack direction="row" alignItems="center" spacing={0.5}>
                                                                        <CircularProgress size={12} />
                                                                        <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                                                            Loading...
                                                                        </Typography>
                                                                    </Stack>
                                                                ) : (
                                                                    'Show Email'
                                                                )}
                                                            </Button>
                                                        )}
                                                    </Box>
                                                </Stack>
                                            )}
                                        </TableCell>

                                        <TableCell align="right">
                                            <Button
                                                size="small"
                                                variant="outlined"
                                                color="success"
                                                startIcon={<Iconify icon={unblockIcon} />}
                                                onClick={async () => {
                                                    if (!user._id) return;
                                                    setSelectedUser(user);

                                                    setIsConfirmDialogOpen(true);
                                                    // handleUnblockUser(user._id);
                                                }
                                                }
                                                disabled={!isAdmin || emailLoading[user._id]}
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
                                                Unblock
                                            </Button>

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
                        <Typography variant="h6">Confirm User Unblock</Typography>
                    </Stack>
                </DialogTitle>
                <DialogContent>
                    <Typography variant="body2" sx={{ mt: 1 }}>
                        Are you sure you want to unblock {selectedUser?.fullName || 'this user'}?
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
                        onClick={() => {
                            handleUnblockUser(selectedUser?._id || '');
                            setIsConfirmDialogOpen(false);
                        }}
                        variant="contained"
                        color="success"
                        sx={{ borderRadius: 1 }}
                    >
                        Unblock
                    </Button>
                </DialogActions>
            </Dialog>

            {/* Snackbar */}
            {/* <Snackbar
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
            </Snackbar> */}

            {/* Modals */}

        </Box>
    );
};



export default BlockedUsers;
