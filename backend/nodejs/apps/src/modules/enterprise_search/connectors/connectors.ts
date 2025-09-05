export const APP_TYPES = {
  DRIVE: 'drive',
  GMAIL: 'gmail',
  ONEDRIVE: 'onedrive',
  SHAREPOINT_ONLINE: 'sharepointOnline',
  LOCAL: 'local',
} as const;

export type AppType = (typeof APP_TYPES)[keyof typeof APP_TYPES];
