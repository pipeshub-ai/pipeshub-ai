// Mirrors isValidEmail in the Node users controller, so an address the panel
// accepts is one the server accepts. The local part refuses quoting and list
// punctuation: a spreadsheet formula such as =HYPERLINK("http://x","a@b.co")
// split at its comma must not turn into a valid-looking tag. Built with the
// constructor because tsconfig targets ES5, which has no `u` regex literal.
const EMAIL_REGEX = new RegExp(
  '^[^\\s@"(),:;<>[\\]\\\\`]+@(?:[\\p{L}\\p{N}-]+\\.)+[\\p{L}\\p{N}-]{2,}$',
  'u',
);

// RFC 5321 caps a forward path at 254 characters.
export const MAX_EMAIL_LENGTH = 254;

export const INVITE_IMPORT_EXTENSIONS = ['csv', 'xlsx', 'xls'] as const;

export function validateEmail(value: string): string | null {
  if (value.length > MAX_EMAIL_LENGTH || !EMAIL_REGEX.test(value)) {
    return 'Invalid email address';
  }
  return null;
}

export function isSupportedInviteImportFile(fileName: string): boolean {
  const dot = fileName.lastIndexOf('.');
  if (dot < 0) return false;
  const ext = fileName.slice(dot + 1).toLowerCase();
  return (INVITE_IMPORT_EXTENSIONS as readonly string[]).includes(ext);
}

/** A failure whose message is already written for the person importing. */
export class InviteImportError extends Error {}

export interface ImportCandidates {
  /** Cells that look like an address, in file order, capped at `limit + 1`. */
  found: string[];
  /** Cells with an `@` that are too long to be an address; left out of `found`. */
  tooLong: number;
}

export function collectImportCandidates(
  rows: ReadonlyArray<ReadonlyArray<unknown>>,
  limit: number,
): ImportCandidates {
  const found: string[] = [];
  let tooLong = 0;
  for (const row of rows) {
    for (const cell of row) {
      if (cell == null) continue;
      const value = String(cell).trim();
      if (!value.includes('@')) continue;
      if (value.length > MAX_EMAIL_LENGTH) {
        tooLong += 1;
      } else if (found.length <= limit) {
        found.push(value);
      }
    }
  }
  return { found, tooLong };
}

export interface InviteOutcome {
  /** Addresses an invitation was sent to (new, re-invited or restored). */
  sent: number;
  /** Addresses skipped because they already belong to an active member. */
  alreadyMembers: number;
}

/**
 * Reads the counts from POST /users/bulk/invite. An older server returns no
 * counts, in which case every address sent is assumed invited.
 */
export function inviteOutcome(
  requested: number,
  counts: {
    invited?: number;
    reinvited?: number;
    restored?: number;
    alreadyActive?: number;
  },
): InviteOutcome {
  const parts = [counts.invited, counts.reinvited, counts.restored];
  if (parts.every((n) => typeof n !== 'number')) {
    return { sent: requested, alreadyMembers: 0 };
  }
  const sent = parts.reduce<number>(
    (sum, n) => sum + (typeof n === 'number' ? n : 0),
    0,
  );
  return {
    sent,
    alreadyMembers:
      typeof counts.alreadyActive === 'number' ? counts.alreadyActive : 0,
  };
}
