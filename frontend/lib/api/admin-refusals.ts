/**
 * Refusals only an admin can lift: the server turned the request down for a reason the person cannot change, and the
 * same request is turned down again until an admin acts. The app shows the server's words for one, which say whom to
 * ask, offers no retry, and sends nothing queued behind it. None by default; `setAdminRefusals` names them.
 */
export interface AdminRefusals {
  /** Whether a chat run's error code (a stream's `RUN_ERROR`, a stored failed row) names one. */
  isRunCode(code: string | undefined): boolean;
  /** Whether a refused call is one, by its status and its code. */
  isRefusedCall(error: { statusCode?: number; code?: string }): boolean;
}

export const noAdminRefusals: AdminRefusals = {
  isRunCode: () => false,
  isRefusedCall: () => false,
};

let current: AdminRefusals = noAdminRefusals;

export function adminRefusals(): AdminRefusals {
  return current;
}

/** Names the refusals only an admin can lift; `undefined` restores the default, which has none. */
export function setAdminRefusals(next: AdminRefusals | undefined): void {
  current = next ?? noAdminRefusals;
}
