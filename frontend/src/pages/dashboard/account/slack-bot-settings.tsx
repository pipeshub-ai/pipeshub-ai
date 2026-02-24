import { Helmet } from 'react-helmet-async';

import { Box } from '@mui/material';

import { CONFIG } from 'src/config-global';

import Sidebar from 'src/sections/accountdetails/Sidebar';
import SlackBotSettings from 'src/sections/accountdetails/account-settings/slack-bot/slack-bot-settings';

const metadata = { title: `Slack Bot Configurations  - ${CONFIG.appName}` };

export default function SlackBotSettingsPage() {
  return (
    <>
      <Helmet>
        <title> {metadata.title}</title>
      </Helmet>
      <Box sx={{ display: 'flex', flexGrow: 1, overflow: 'hidden', zIndex: 0 }}>
        <Sidebar />
        <SlackBotSettings />
      </Box>
    </>
  );
}
