import type { Icon as IconifyIcon } from '@iconify/react';

import { useState, useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import emailIcon from '@iconify-icons/mdi/email-outline';
import { useNavigate, useLocation } from 'react-router-dom';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import accountMultipleIcon from '@iconify-icons/mdi/account-multiple';

import {
  Box,
  Tab,
  Tabs,
  Chip,
  Stack,
  Divider,
  useTheme,
  Typography,
  CircularProgress,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import Users from './users';
import Groups from './groups';
import Invites from './invites';
import { allblockedUsers, allGroups, getAllUsersWithGroups } from '../utils';
import { setCounts, setLoading } from '../../../store/user-and-groups-slice';

import type { GroupUser, AppUserGroup, AppUser } from '../types/group-details';
import type { CountsState } from '../../../store/user-and-groups-slice';
import BlockedUsers from './blocked-users';

interface RootState {
  counts: CountsState;
}

export default function UsersAndGroups() {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [tabValue, setTabValue] = useState<number>(0);

  const dispatch = useDispatch();
  const loading = useSelector((state: RootState) => state.counts.loading);
  const userCount = useSelector((state: RootState) => state.counts.usersCount);
  const groupCount = useSelector((state: RootState) => state.counts.groupsCount);
  const invitesCount = useSelector((state: RootState) => state.counts.invitesCount);
  const blockedUsersCount = useSelector((state: RootState) => state.counts.blockedUsersCount);
  const TAB_ROUTES = [
    { key: 'users', path: '/account/company-settings/users' },
    { key: 'groups', path: '/account/company-settings/groups' },
    { key: 'invites', path: '/account/company-settings/invites' },
    { key: 'blocked-users', path: '/account/company-settings/blocked-users' },
  ];

  useEffect(() => {
    const fetchCounts = async (): Promise<void> => {
      dispatch(setLoading(true));
      try {
        const response: GroupUser[] = await getAllUsersWithGroups();
        const groups: AppUserGroup[] = await allGroups();
        const blockedUsers: GroupUser[] = await allblockedUsers();
        const loggedInUsers = response.filter((user) => user.hasLoggedIn === true);
        const pendingUsers = response.filter((user) => user.hasLoggedIn === false);
        dispatch(
          setCounts({
            usersCount: loggedInUsers.length,
            groupsCount: groups.length,
            invitesCount: pendingUsers.length,
            blockedUsersCount: blockedUsers.length,
          })
        );
      } catch (error) {
        console.error('Error fetching counts:', error);
      } finally {
        dispatch(setLoading(false));
      }
    };

    fetchCounts();
  }, [dispatch]);

  useEffect(() => {

    // console.log('Current path:', tabValue);
    const index = TAB_ROUTES.findIndex(route =>
      location.pathname === route.path
    );

    if (index !== -1) {
      setTabValue(index);
    }
  }, [location.pathname]);

  const handleTabChange = (
    event: React.SyntheticEvent,

    newValue: number
  ) => {
    setTabValue(newValue);
    navigate(TAB_ROUTES[newValue].path);
  };

  if (loading) {
    return (
      <Box
        display="flex"
        flexDirection="column"
        justifyContent="center"
        alignItems="center"
        sx={{ height: 400, width: '100%' }}
      >
        <CircularProgress size={36} thickness={2.5} />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
          Loading data...
        </Typography>
      </Box>
    );
  }

  const TabItem = (
    label: string,
    count: number,
    icon: React.ComponentProps<typeof IconifyIcon>['icon'],
    isActive: boolean
  ) => (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        py: 0.75,
        opacity: isActive ? 1 : 0.7,
        transition: 'all 0.2s ease-in-out',
      }}
    >
      <Iconify
        icon={icon}
        width={20}
        height={20}
        sx={{
          color: isActive ? 'primary.main' : 'text.secondary',
        }}
      />
      <Typography
        variant="body2"
        sx={{
          fontWeight: isActive ? 600 : 400,
          color: isActive ? 'text.primary' : 'text.secondary',
        }}
      >
        {label}
      </Typography>
      {count > 0 && (
        <Chip
          size="small"
          label={count}
          color={isActive ? 'primary' : 'default'}
          variant={isActive ? 'filled' : 'outlined'}
          sx={{
            height: 20,
            minWidth: 20,
            fontSize: '0.75rem',
            fontWeight: 500,
            px: 0.5,
          }}
        />
      )}
    </Stack>
  );

  return (
    <Box sx={{ p: 3 }}>
      {/* Main Content Container */}
      {/* Tabs Section */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tabs
          value={tabValue}
          onChange={handleTabChange}
          aria-label="users and groups tabs"
          sx={{
            px: 3,
            minHeight: 56,
            '& .MuiTabs-indicator': {
              height: 3,
              borderTopLeftRadius: 3,
              borderTopRightRadius: 3,
            },
            '& .MuiTab-root': {
              minHeight: 56,
              fontWeight: 500,
              borderBottom: '3px solid transparent',
              transition: 'all 0.1s ease-in-out',
              '&:hover': {
                color: 'primary.main',
              },
            },
          }}
        >
          <Tab
            label={TabItem('Users', userCount, accountMultipleIcon, tabValue === 0)}
            disableRipple
            sx={{ textTransform: 'none' }}
          />
          <Tab
            label={TabItem('Groups', groupCount, accountGroupIcon, tabValue === 1)}
            disableRipple
            sx={{ textTransform: 'none' }}
          />
          <Tab
            label={TabItem('Invites', invitesCount, emailIcon, tabValue === 2)}
            disableRipple
            sx={{ textTransform: 'none' }}
          />
          <Tab
            label={TabItem('Blocked Users', blockedUsersCount, emailIcon, tabValue === 3)}
            disableRipple
            sx={{ textTransform: 'none' }}
          />
        </Tabs>
      </Box>

      <Divider />

      {/* Tab Content Section */}
      <Box sx={{ p: 3 }}>
        {tabValue === 0 && <Users />}
        {tabValue === 1 && <Groups />}
        {tabValue === 2 && <Invites />}
        {tabValue === 3 && <BlockedUsers />}

      </Box>
    </Box>
  );
}
