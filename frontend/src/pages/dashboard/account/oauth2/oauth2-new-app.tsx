import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import { OAuth2NewAppView } from 'src/sections/accountdetails/oauth2/oauth2-new-app-view';

const metadata = { title: `New OAuth 2.0 App - ${CONFIG.appName}` };

export default function Page() {
  return (
    <>
      <Helmet>
        <title>{metadata.title}</title>
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <OAuth2NewAppView />
      </Box>
    </>
  );
}
