import { getBrandConfig } from '../../libs/branding';

/** Where support requests and feedback go: SUPPORT_EMAIL, else the brand's support address. */
export function getSupportEmail(): string {
  const configured = process.env.SUPPORT_EMAIL?.trim() ?? '';
  return configured !== '' ? configured : getBrandConfig().supportEmail;
}

export const SUPPORT_LIMITS = {
  requestsPerHour: 5,
  /** How long a request waits for the mail server; a slower send carries on and the answer is "pending". */
  mailDeadlineMs: 15_000,
  minDescription: 10,
  maxDescription: 5000,
  maxRecentErrors: 5,
  maxErrorPath: 200,
  maxAttachments: 5,
  maxAttachmentBytes: 5 * 1024 * 1024,
  /** Nothing is stored, so every accepted file has to fit in one email (servers often refuse ~10 MB once encoded). */
  maxAttachmentsTotalBytes: 8 * 1024 * 1024,
} as const;

/** What the upload accepts: images, PDF, plain text, logs and CSV. */
export const SUPPORT_ATTACHMENT_EXTENSIONS = [
  'jpg',
  'jpeg',
  'png',
  'webp',
  'gif',
  'pdf',
  'txt',
  'log',
  'csv',
] as const;

export const SUPPORT_ATTACHMENT_MIME_BY_EXTENSION: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  gif: 'image/gif',
  pdf: 'application/pdf',
  txt: 'text/plain',
  log: 'text/plain',
  csv: 'text/csv',
};
