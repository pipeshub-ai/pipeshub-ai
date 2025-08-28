import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import OneDriveConnector from 'src/sections/accountdetails/account-settings/connector/onedrive';

// ----------------------------------------------------------------------

const metadata = { title: `OneDrive Connector` };

export default function Page() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <OneDriveConnector />
      </Box>
    </>
  );
}
