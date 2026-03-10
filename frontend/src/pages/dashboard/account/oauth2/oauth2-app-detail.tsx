import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import { OAuth2AppDetailView } from 'src/sections/accountdetails/oauth2/oauth2-app-detail-view';

const metadata = {
  title: `OAuth 2.0 App Settings | Dashboard - ${CONFIG.appName}`,
  description:
    'Manage your OAuth 2.0 application settings, permissions, scopes, and client credentials on Pipeshub.',
};

export default function Page() {
  return (
    <>
      <Helmet>
        <title>{metadata.title}</title>
        <meta name="description" content={metadata.description} />
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <OAuth2AppDetailView />
      </Box>
    </>
  );
}
