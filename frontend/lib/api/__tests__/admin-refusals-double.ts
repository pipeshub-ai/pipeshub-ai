import { afterEach, beforeEach } from 'vitest';
import { setAdminRefusals, type AdminRefusals } from '@/lib/api/admin-refusals';

/** The code of the run refusal these tests name as one only an admin can lift. */
export const NOT_ALLOWED = 'request_not_allowed';

/** The server's words for it, written for the person refused. */
export const NOT_ALLOWED_TEXT = 'This request is not allowed right now. Ask your admin.';

/** A call refused that way, as the API client's interceptor rejects it. */
export const NOT_ALLOWED_CALL = { type: 'UNKNOWN', statusCode: 403, code: 'REQUEST_NOT_ALLOWED', message: NOT_ALLOWED_TEXT };

/**
 * Refusals only an admin can lift, as a deployment might name them: runs that end with `request_not_allowed`, and calls
 * refused with 403 and the code `REQUEST_NOT_ALLOWED`.
 */
export const testAdminRefusals: AdminRefusals = {
  isRunCode: (code) => code === NOT_ALLOWED,
  isRefusedCall: ({ statusCode, code }) => statusCode === 403 && code === 'REQUEST_NOT_ALLOWED',
};

/** Names the test refusals for every test of the calling suite, and restores the default after each. */
export function withTestAdminRefusals(): void {
  beforeEach(() => setAdminRefusals(testAdminRefusals));
  afterEach(() => setAdminRefusals(undefined));
}

/** Every test of the calling suite runs with the default refusals: none. */
export function withDefaultAdminRefusals(): void {
  beforeEach(() => setAdminRefusals(undefined));
}
