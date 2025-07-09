import type { Icon as IconifyIcon } from '@iconify/react';
import googleIcon from '@iconify-icons/mdi/google';
import slackIcon from '@iconify-icons/mdi/slack';

export interface ConnectorConfig {
  id: string;
  icon: React.ComponentProps<typeof IconifyIcon>['icon'];
  title: string;
  description: string;
  color: string;
}

export interface ConfigStatus {
  googleWorkspace: boolean;
  slack: boolean;
}

export const GOOGLE_WORKSPACE_SCOPE = [
  'email openid',
  'https://www.googleapis.com/auth/drive.readonly',
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/calendar.readonly',
].join(' ');

// Define available connectors
export const CONNECTORS_LIST: ConnectorConfig[] = [
  {
    id: 'googleWorkspace',
    icon: googleIcon,
    title: 'Google Workspace',
    description:
      'Integrate with Google Workspace for calendar, gmail, spreadsheets, drive and document sharing',
    color: '#4285F4',
  },
  {
    id: 'slack',
    icon: slackIcon,
    title: 'Slack',
    description:
      'Integrate with Slack for messages, channels, and team communication',
    color: '#ECB22E',
  },
];