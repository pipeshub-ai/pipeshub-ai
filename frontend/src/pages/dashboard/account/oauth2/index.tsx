import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import { OAuth2ListView } from 'src/sections/accountdetails/oauth2/oauth2-list-view';

const metadata = {
  title: `OAuth 2.0 Applications | Dashboard - ${CONFIG.appName}`,
  description:
    'Manage OAuth 2.0 applications that can access your organization via Pipeshub. Create, configure, and revoke third-party integrations.',
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
        <OAuth2ListView />
      </Box>
    </>
  );
}
