'use client';

/**
 * One sentence for a chat attachment that failed to upload. The server's
 * attachment errors already start by naming the file ("Couldn't read
 * report.docx. …", "report.docx is empty. …"), so those are shown as-is;
 * anything else gets the file named once in front of it.
 */
export function attachmentErrorMessage(fileName: string, error: unknown): string {
  const reason =
    error instanceof Error || (error && typeof error === 'object' && 'message' in error)
      ? String((error as { message?: unknown }).message ?? '').trim()
      : '';
  if (reason.startsWith(`Couldn't read ${fileName}. `) || reason.startsWith(`${fileName} `)) {
    return reason;
  }
  return reason
    ? `Couldn't attach ${fileName}. ${reason}`
    : `Couldn't attach ${fileName}. Please try again.`;
}
