/** The server's reason code when a deleted account's shared chats have no admin to move to (CLEANUP-G2). */
export const OWNER_HANDOVER_NO_ADMIN = 'owner_handover_no_admin';

/** Whether a rejected delete was refused because no other active admin can take over the account's shared chats. */
export function isOwnerHandoverRefusal(error: unknown): boolean {
  const reasonCode = (
    error as { originalError?: { response?: { data?: { error?: { reasonCode?: unknown } } } } } | null
  )?.originalError?.response?.data?.error?.reasonCode;
  return reasonCode === OWNER_HANDOVER_NO_ADMIN;
}
