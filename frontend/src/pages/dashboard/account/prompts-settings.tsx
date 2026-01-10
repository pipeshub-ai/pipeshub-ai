import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import PromptsSettings from 'src/sections/accountdetails/account-settings/prompts/prompts-settings';

// ----------------------------------------------------------------------

const metadata = { title: `Prompts Settings - ${CONFIG.appName}` };

export default function PromptsSettingsPage() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <PromptsSettings />
      </Box>
    </>
  );
}

