export const APP_TYPES = {
  DRIVE: 'drive',
  GMAIL: 'gmail',
  ONEDRIVE: 'onedrive',
  SHAREPOINT_ONLINE: 'sharepointOnline',
  BOOKSTACK: 'bookstack',
  CONFLUENCE: 'confluence',
  JIRA: 'jira',
  SLACK: 'slack',
  DROPBOX: 'dropbox',
  OUTLOOK: 'outlook',
  SERVICENOW: 'servicenow',
  WEB: 'web',
  LOCAL: 'local',
} as const;

export type AppType = (typeof APP_TYPES)[keyof typeof APP_TYPES];
