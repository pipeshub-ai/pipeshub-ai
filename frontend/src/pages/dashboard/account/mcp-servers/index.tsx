import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import McpServersSettings from 'src/sections/accountdetails/account-settings/mcp-servers/mcp-servers-settings';

const metadata = { title: `MCP Servers - ${CONFIG.appName}` };

export default function McpServersPage() {
  return (
    <>
      <Helmet>
        <title>{metadata.title}</title>
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <McpServersSettings />
      </Box>
    </>
  );
}
