export const APP_TYPES = {
  DRIVE: 'drive',
  GMAIL: 'gmail',
  ONEDRIVE: 'onedrive',
  SHAREPOINT_ONLINE: 'sharepointOnline',
  BOOKSTACK: 'bookstack',
  CONFLUENCE: 'confluence',
  JIRA: 'jira',
  LINEAR: 'linear',
  SLACK: 'slack',
  DROPBOX: 'dropbox',
  OUTLOOK: 'outlook',
  SERVICENOW: 'servicenow',
  WEB: 'web',
  RSS: 'rss',
  LOCAL: 'local',
  NOTION: 'notion',
} as const;

export type AppType = (typeof APP_TYPES)[keyof typeof APP_TYPES];
