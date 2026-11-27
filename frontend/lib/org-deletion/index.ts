/** Deleting the organization as the browser sees it: the name an admin types, and the server's refusal of a wrong one. */

export const ORG_DELETE_NAME_MISMATCH = 'org_delete_name_mismatch';

type ErrorLike = {
  response?: { status?: number; data?: unknown };
  originalError?: { response?: { status?: number; data?: unknown } };
};

const responseOf = (error: unknown) => {
  const e = error as ErrorLike | null;
  return e?.response ?? e?.originalError?.response;
};

/** The server's reason code on a refused call (`error.reasonCode`). */
export function reasonCodeOf(error: unknown): string | null {
  const data = responseOf(error)?.data as { error?: { reasonCode?: unknown } } | undefined;
  return typeof data?.error?.reasonCode === 'string' ? data.error.reasonCode : null;
}

/** What an admin types to delete the org; the server asks for the same (org.controller orgConfirmationName). */
export function orgConfirmationName(org: {
  registeredName?: string | null;
  shortName?: string | null;
  contactEmail?: string | null;
}): string {
  return (
    org.registeredName?.trim() || org.shortName?.trim() || org.contactEmail?.trim() || ''
  );
}
