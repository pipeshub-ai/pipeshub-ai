import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import { OAuth2NewAppView } from 'src/sections/accountdetails/oauth2/oauth2-new-app-view';

const metadata = {
  title: `New OAuth 2.0 Application | Dashboard - ${CONFIG.appName}`,
  description:
    'Register a new OAuth 2.0 application to integrate third-party services with your Pipeshub organization.',
};

export default function Page() {
  return (
    <>
      <Helmet>
        <title>{metadata.title}</title>
        <meta name="description" content={metadata.description} />
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <OAuth2NewAppView />
      </Box>
    </>
  );
}
