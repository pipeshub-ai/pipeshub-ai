'use client';

// Same keys the server accepts; labels and hints live in the locale files under `support.areas`.
export const SUPPORT_AREA_KEYS = ['connectors', 'chat', 'agents', 'files', 'access', 'people', 'other'] as const;
export type SupportAreaKey = (typeof SUPPORT_AREA_KEYS)[number];

export interface SupportRow {
  label: string;
  value: string;
}

export interface SupportContext {
  smtpConfigured: boolean;
  supportEmail: string;
  /** Empty when the workspace has no name. */
  workspaceName: string;
  details: SupportRow[];
}

export type SupportOutcome = 'sent' | 'pending' | 'failed' | 'no_smtp';

export interface SupportRequestResult {
  reference: string;
  outcome: SupportOutcome;
  supportEmail: string;
}

export interface SupportDraft {
  area: SupportAreaKey | null;
  description: string;
  files: File[];
  includeErrors: boolean;
  /** One per request, so a retried send returns the first result instead of a second email. */
  requestKey: string;
}
