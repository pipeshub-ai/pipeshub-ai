import type { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types'
import { buildSessionFilter } from '../../../../src/libs/repositories/conversation/mongo'
import { buildSessionQuery } from '../../../../src/modules/enterprise_search/utils/utils'

/**
 * The Mongo filters the conversation lists used to be built from a request, as the
 * repository now builds them from a typed query. Lets the filter-shape tests keep
 * asserting on the filter itself.
 */
type Ids = ReadonlyArray<{ toString(): string }> | undefined
const strings = (ids: Ids): string[] | undefined => ids?.map(String)

export const buildFilter = (
  req: unknown,
  orgId: string,
  userId: string,
  id?: string,
  owned = true,
  shared = true,
  contentMatchIds?: Ids,
  accessibleProjectIds?: Ids,
): Record<string, any> => {
  const query = buildSessionQuery(req as AuthenticatedUserRequest, {
    orgId,
    userId,
    scope: { kind: 'chat' },
    id,
    owned,
    shared,
    contentMatchIds: strings(contentMatchIds),
    accessibleProjectIds: strings(accessibleProjectIds),
    archived: 'no',
    sort: {},
    skip: 0,
    limit: 1,
  })
  // The scope is applied by the caller in the old shape.
  const filter: Record<string, unknown> = { ...buildSessionFilter(query) }
  delete filter.sessionType
  return filter
}

export const buildAgentConversationFilter = (
  req: unknown,
  orgId: string,
  userId: string,
  agentKey: string,
  conversationId?: string,
  contentMatchIds?: Ids,
  accessibleProjectIds?: Ids,
  sharedAccess?: 'read' | 'write',
): Record<string, any> =>
  buildSessionFilter(
    buildSessionQuery(
      req as AuthenticatedUserRequest,
      {
        orgId,
        userId,
        scope: { kind: 'agent', agentKey },
        id: conversationId,
        contentMatchIds: strings(contentMatchIds),
        accessibleProjectIds: strings(accessibleProjectIds),
        sharedAccess,
        archived: 'any',
        sort: {},
        skip: 0,
        limit: 1,
      },
      { formatSpecifiers: true },
    ),
  )
