import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import PlatformSettings from 'src/sections/accountdetails/account-settings/platform/platform-settings';

// ----------------------------------------------------------------------

const metadata = { title: `Platform Settings  - ${CONFIG.appName}` };

export default function PlatformSettingsPage() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <PlatformSettings />
      </Box>
    </>
  );
}


