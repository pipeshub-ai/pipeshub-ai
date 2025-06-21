export const NOTIFICATION_EVENTS = {
  FILE_UPLOAD_STATUS: 'FILE_UPLOAD_STATUS',
} as const;

export type NotificationEvent = (typeof NOTIFICATION_EVENTS)[keyof typeof NOTIFICATION_EVENTS];

