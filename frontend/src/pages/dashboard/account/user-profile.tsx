import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import UserProfile from 'src/sections/accountdetails/user-profile';
import { UserProvider } from 'src/context/UserContext';

// ----------------------------------------------------------------------

const metadata = { title: `User Profile | Dashboard - ${CONFIG.appName}` };

export default function Page() {
  return (
    <>
    <UserProvider>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>

      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <UserProfile />
      </Box>
      </UserProvider>
    </>
  );
}
