import { Types } from 'mongoose'
import { setConversationContext } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { toAccessView } from '../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy'

const OBJECT_ID_HEX = /^[0-9a-f]{24}$/i

/**
 * Stands in for the route guard in handler-level tests: grants the request's
 * user owner access to `params.conversationId`. A request without a user or a
 * valid id is left alone, so the handler still fails as it would unguarded.
 */
export function withOwnerGrant<T>(req: T): T {
  const r = req as { params?: Record<string, string>; user?: { userId?: unknown; orgId?: unknown } }
  const id = r.params?.conversationId
  if (!id || !OBJECT_ID_HEX.test(id) || !r.user?.userId || !r.user.orgId) return req
  const caller = { userId: String(r.user.userId), orgId: String(r.user.orgId), teamIds: [] as string[] }
  const session = {
    _id: new Types.ObjectId(id),
    orgId: new Types.ObjectId(caller.orgId.padEnd(24, '0').slice(0, 24)),
    userId: new Types.ObjectId(caller.userId.padEnd(24, '0').slice(0, 24)),
    sharedWith: [],
  }
  setConversationContext(req as never, {
    caller,
    grant: {
      session: session as never,
      role: 'owner',
      via: [{ type: 'owner', ref: caller.userId, role: 'owner' }],
      caller,
      view: toAccessView('owner', session as never, caller, { collab: false }),
    },
  })
  return req
}
