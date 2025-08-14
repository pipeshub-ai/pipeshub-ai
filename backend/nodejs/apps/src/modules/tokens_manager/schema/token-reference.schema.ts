export enum ServiceType {
  ONEDRIVE = 'ONEDRIVE',
  GOOGLE_DRIVE = 'GOOGLE_DRIVE',
  CONFLUENCE = 'CONFLUENCE',
  JIRA = 'JIRA',
}

export enum TokenEventType {
  TOKEN_CREATED = 'TOKEN_CREATED',
  TOKEN_REFRESHED = 'TOKEN_REFRESHED',
  TOKEN_REVOKED = 'TOKEN_REVOKED',
  TOKEN_EXPIRED = 'TOKEN_EXPIRED',
}

export interface ITokenEvent {
  eventId: string;
  eventType: TokenEventType;
  tokenReferenceId: string;
  serviceType: ServiceType;
  accountId: string;
  timestamp: Number;
}
