import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import AtlassianConnector from 'src/sections/accountdetails/account-settings/connector/atlassian';

// ----------------------------------------------------------------------

const metadata = { title: `Atlassian Connector` };

export default function Page() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <AtlassianConnector />
      </Box>
    </>
  );
}
