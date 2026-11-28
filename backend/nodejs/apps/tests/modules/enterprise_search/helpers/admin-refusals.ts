import {
  AdminRefusals,
  AIRequestRefusedError,
  useAdminRefusals,
} from '../../../../src/modules/enterprise_search/utils/ai-stream'

/** The chat's code for the refusal these tests name as one only an admin can lift. */
export const NOT_ALLOWED = 'request_not_allowed'

/** The AI service's words for it, written for the person refused. */
export const NOT_ALLOWED_TEXT = 'This request is not allowed right now. Ask your admin.'

/** The AI service's answer to a request it refuses that way, sent with status 403. */
export const notAllowed = { detail: { code: 'REQUEST_NOT_ALLOWED', message: NOT_ALLOWED_TEXT } }

/** Said for such a refusal that carries no words of its own. */
export const NOT_ALLOWED_FALLBACK = 'Ask your admin to allow this request.'

/**
 * Refusals only an admin can lift, as a deployment might name them: a 403 whose `detail` has the code
 * `REQUEST_NOT_ALLOWED`. Its words stay; without any, a fixed line.
 */
export const testAdminRefusals: AdminRefusals = {
  fromResponse(status, data) {
    const detail = (data as { detail?: { code?: unknown; message?: unknown } | null } | null)?.detail
    if (status !== 403 || detail?.code !== 'REQUEST_NOT_ALLOWED') return undefined
    const message = typeof detail.message === 'string' && detail.message ? detail.message : NOT_ALLOWED_FALLBACK
    return new AIRequestRefusedError(NOT_ALLOWED, message, 403)
  },
  isCode: (code) => code === NOT_ALLOWED,
}

/** Names the test refusals for every test of the calling suite, and restores the default after each. */
export function withTestAdminRefusals(): void {
  beforeEach(() => useAdminRefusals(testAdminRefusals))
  afterEach(() => useAdminRefusals(undefined))
}
