'use client';

/** Mirrors the server's upload rules for Send feedback and Contact support. */
export const ATTACHMENT_ACCEPT =
  'image/jpeg,image/png,image/webp,image/gif,application/pdf,text/plain,text/csv,.jpg,.jpeg,.png,.webp,.gif,.pdf,.txt,.log,.csv';
export const MAX_ATTACHMENTS = 5;
export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
// Everything goes out in one email, so the server refuses more in total.
export const MAX_ATTACHMENTS_TOTAL_BYTES = 8 * 1024 * 1024;
export const ALLOWED_ATTACHMENT_EXTENSIONS = new Set([
  'jpg',
  'jpeg',
  'png',
  'webp',
  'gif',
  'pdf',
  'txt',
  'log',
  'csv',
]);

export function fileExtension(name: string): string {
  const parts = name.split('.');
  return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : '';
}
