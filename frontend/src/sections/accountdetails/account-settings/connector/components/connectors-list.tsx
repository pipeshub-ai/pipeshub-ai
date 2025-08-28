import type { Icon as IconifyIcon } from '@iconify/react';

import googleIcon from '@iconify-icons/mdi/google';
import { ConnectorId, ConnectorIdToNameMap } from 'src/sections/accountdetails/types/connector';

export interface ConnectorConfig {
  id: string;
  icon?: React.ComponentProps<typeof IconifyIcon>['icon'];
  src?: string;
  title: string;
  description: string;
  color: string;
  // New fields for better configuration
  apiEndpoint?: string;
  configType?: 'individual' | 'business' | 'both';
  supportedServices?: string[];
  scopes?: string[];
  requiresOAuth?: boolean;
  requiresCredentials?: boolean;
}

export interface ConfigStatus {
  [connectorId: string]: boolean;
}

export const GOOGLE_WORKSPACE_SCOPE = [
  'email openid',
  'https://www.googleapis.com/auth/drive.readonly',
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/gmail.send',
  'https://www.googleapis.com/auth/gmail.compose',
  'https://www.googleapis.com/auth/gmail.modify',
  'https://www.googleapis.com/auth/calendar.readonly',
  'https://www.googleapis.com/auth/calendar.calendars',
  'https://www.googleapis.com/auth/calendar.events.owned',
  'https://www.googleapis.com/auth/calendar.events.readonly',
  'https://www.googleapis.com/auth/calendar.events.owned.readonly',
  "https://www.googleapis.com/auth/drive.readonly",
  "https://www.googleapis.com/auth/drive.file",
  "https://www.googleapis.com/auth/drive",
  "https://www.googleapis.com/auth/drive.metadata.readonly",
  "https://www.googleapis.com/auth/drive.metadata",
].join(' ');

// Define available connectors with enhanced configuration
export const CONNECTORS_LIST: ConnectorConfig[] = [
  {
    id: ConnectorId.GOOGLE_WORKSPACE,
    title: ConnectorIdToNameMap[ConnectorId.GOOGLE_WORKSPACE],
    icon: googleIcon,
    src: '/assets/icons/connectors/google.svg',
    description:
      'Integrate with Google Workspace for calendar, gmail, spreadsheets, drive and document content semantic search',
    color: '#4285F4',
    apiEndpoint: '/api/v1/connectors/config',
    configType: 'both',
    supportedServices: ['DRIVE', 'GMAIL'],
    scopes: GOOGLE_WORKSPACE_SCOPE.split(' '),
    requiresOAuth: true,
    requiresCredentials: false,
  },
  {
    id: ConnectorId.ONEDRIVE,
    title: ConnectorIdToNameMap[ConnectorId.ONEDRIVE],
    src: '/assets/icons/connectors/onedrive.svg',
    description: 'Integrate with OneDrive for file content semantic search',
    color: '#0078D4',
    apiEndpoint: '/api/v1/connectors/config',
    configType: 'business',
    supportedServices: ['ONEDRIVE'],
    requiresOAuth: true,
    requiresCredentials: true,
  },
  // {
  //   id: ConnectorId.SHAREPOINT,
  //   title: ConnectorIdToNameMap[ConnectorId.SHAREPOINT],
  //   src: '/assets/icons/connectors/sharepoint.svg',
  //   description: 'Integrate with SharePoint Online for file content semantic search',
  //   color: '#0078D4',
  //   apiEndpoint: '/api/v1/connectors/config',
  //   configType: 'business',
  //   supportedServices: ['SHAREPOINT'],
  //   requiresOAuth: true,
  //   requiresCredentials: true,
  // },
  // {
  //   id: ConnectorId.ATLASSIAN,
  //   title: ConnectorIdToNameMap[ConnectorId.ATLASSIAN],
  //   src: '/assets/icons/connectors/atlassian.svg',
  //   description: 'Integrate with Atlassian for Confluence and Jira',
  //   color: '#0052CC',
  //   apiEndpoint: '/api/v1/connectors/config',
  //   configType: 'business',
  //   supportedServices: ['CONFLUENCE', 'JIRA'],
  //   requiresOAuth: true,
  //   requiresCredentials: true,
  // },
];

// Helper function to get connector by ID
export const getConnectorById = (id: string): ConnectorConfig | undefined => 
  CONNECTORS_LIST.find(connector => connector.id === id);

// Helper function to get connector title
export const getConnectorTitle = (id: string): string => {
  const connector = getConnectorById(id);
  return connector?.title || id;
};
