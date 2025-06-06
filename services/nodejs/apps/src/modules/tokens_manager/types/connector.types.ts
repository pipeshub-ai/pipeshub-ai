export enum GoogleWorkspaceApp {
  Drive = 'DRIVE',
  Gmail = 'GMAIL',
  Calendar = 'CALENDAR',
}

export interface GoogleWorkspaceApps {
  apps: GoogleWorkspaceApp[];
}

export const scopeToAppMap: { [key: string]: GoogleWorkspaceApp } = {
  'https://www.googleapis.com/auth/gmail.readonly': GoogleWorkspaceApp.Gmail,
  'https://www.googleapis.com/auth/calendar.readonly': GoogleWorkspaceApp.Calendar,
  'https://www.googleapis.com/auth/drive.readonly': GoogleWorkspaceApp.Drive,
};
