import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import { OAuth2AppDetailView } from 'src/sections/accountdetails/oauth2/oauth2-app-detail-view';

const metadata = { title: `OAuth 2.0 App - ${CONFIG.appName}` };

export default function Page() {
  return (
    <>
      <Helmet>
        <title>{metadata.title}</title>
      </Helmet>
      <Box sx={{ flexGrow: 1, overflow: 'hidden', minHeight: '100%' }}>
        <OAuth2AppDetailView />
      </Box>
    </>
  );
}
