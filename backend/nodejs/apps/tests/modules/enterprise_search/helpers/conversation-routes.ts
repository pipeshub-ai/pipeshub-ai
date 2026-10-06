import { ConversationOperation } from '../../../../src/modules/enterprise_search/services/collaboration/domain/types'

export type RouteKind = 'chat' | 'agent'
export type Method = 'get' | 'post' | 'put' | 'patch' | 'delete'

export const USER_B = '6abf35278cf29be1a243f001'
export const RUN_ID = '3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c'
export const AGENT_KEY = 'agent-1'

export interface ConversationRoute {
  /** PH-04 §3 row. */
  id: string
  kind: RouteKind
  method: Method
  /** Path inside its router. */
  path: string
  op: ConversationOperation
  internal?: true
  /** A body that passes the route's validator, so a request reaches the guard. */
  body?: Record<string, unknown>
}

const chat = (id: string, method: Method, path: string, op: ConversationOperation, extra: Partial<ConversationRoute> = {}): ConversationRoute =>
  ({ id, kind: 'chat', method, path, op, ...extra })
const agent = (id: string, method: Method, path: string, op: ConversationOperation, extra: Partial<ConversationRoute> = {}): ConversationRoute =>
  ({ id, kind: 'agent', method, path: `/:agentKey/conversations${path}`, op, ...extra })

const TURN = { query: 'More detail', chatMode: 'quick' }

/** Every route with `:conversationId` (PH-04 §3, C1-C16 and A1-A13): the single source for the route-table, matrix and parity suites. */
export const CONVERSATION_ROUTES: readonly ConversationRoute[] = [
  chat('C1', 'post', '/:conversationId/messages', 'send', { body: TURN }),
  chat('C2', 'post', '/internal/:conversationId/messages', 'send', { body: TURN, internal: true }),
  chat('C3', 'post', '/:conversationId/messages/stream', 'send', { body: { query: 'More detail', chatMode: 'internal_search' } }),
  chat('C4', 'post', '/internal/:conversationId/messages/stream', 'send', { body: TURN, internal: true }),
  chat('C5', 'get', '/:conversationId', 'read'),
  chat('C6', 'delete', '/:conversationId', 'delete'),
  chat('C7', 'post', '/:conversationId/share', 'manageCollaborators', { body: { userIds: ['6abf35278cf29be1a243f001'] } }),
  chat('C8', 'post', '/:conversationId/unshare', 'manageCollaborators', { body: { userIds: ['6abf35278cf29be1a243f001'] } }),
  chat('C9', 'put', '/:conversationId/project', 'linkProject', { body: { projectId: null } }),
  chat('C10', 'patch', '/:conversationId/project-visibility', 'linkProject', { body: { visibility: 'private' } }),
  chat('C11', 'post', '/:conversationId/message/:messageId/regenerate', 'regenerate', { body: { chatMode: 'quick' } }),
  chat('C12', 'post', '/:conversationId/cancel', 'cancel', { body: { runId: RUN_ID } }),
  chat('C13', 'patch', '/:conversationId/title', 'rename', { body: { title: 'Renamed' } }),
  chat('C14', 'post', '/:conversationId/message/:messageId/feedback', 'feedback', { body: { isHelpful: true } }),
  chat('C15', 'patch', '/:conversationId/archive', 'archiveSelf'),
  chat('C16', 'patch', '/:conversationId/unarchive', 'archiveSelf'),
  agent('A1', 'post', '/:conversationId/messages', 'send', { body: TURN }),
  agent('A2', 'post', '/:conversationId/messages/stream', 'send', { body: TURN }),
  agent('A3', 'post', '/internal/:conversationId/messages/stream', 'send', { body: TURN, internal: true }),
  agent('A4', 'post', '/:conversationId/message/:messageId/regenerate', 'regenerate', { body: { chatMode: 'quick' } }),
  agent('A5', 'post', '/:conversationId/cancel', 'cancel', { body: { runId: RUN_ID } }),
  agent('A6', 'post', '/:conversationId/message/:messageId/feedback', 'feedback', { body: { isHelpful: true } }),
  agent('A7', 'get', '/:conversationId', 'read'),
  agent('A8', 'delete', '/:conversationId', 'delete'),
  agent('A9', 'patch', '/:conversationId/title', 'rename', { body: { title: 'Renamed' } }),
  agent('A10', 'put', '/:conversationId/project', 'linkProject', { body: { projectId: null } }),
  agent('A11', 'patch', '/:conversationId/project-visibility', 'linkProject', { body: { visibility: 'private' } }),
  agent('A12', 'post', '/:conversationId/archive', 'archiveSelf'),
  agent('A13', 'post', '/:conversationId/unarchive', 'archiveSelf'),
]

/** The PH-06 collaboration routes, mounted on both routers; kept apart from the PH-04 table, whose suites assume its 29 rows. */
export const COLLABORATION_ROUTES: readonly ConversationRoute[] = [
  chat('C17', 'get', '/:conversationId/collaborators', 'read'),
  chat('C18', 'put', '/:conversationId/collaborators', 'invite', {
    body: { collaborators: [{ principalType: 'user', principalId: USER_B, accessLevel: 'read' }] },
  }),
  chat('C19', 'delete', '/:conversationId/collaborators/:principalId', 'manageCollaborators'),
  chat('C20', 'patch', '/:conversationId/collaboration-settings', 'settings', { body: { editorsCanInvite: true } }),
  chat('C21', 'post', '/:conversationId/transfer-ownership', 'transfer', { body: { newOwnerUserId: USER_B } }),
  chat('C22', 'post', '/:conversationId/leave', 'leave'),
  chat('C23', 'get', '/:conversationId/feed', 'read'),
  chat('C24', 'get', '/:conversationId/readiness', 'read'),
  agent('A14', 'get', '/:conversationId/collaborators', 'read'),
  agent('A15', 'put', '/:conversationId/collaborators', 'invite', {
    body: { collaborators: [{ principalType: 'user', principalId: USER_B, accessLevel: 'read' }] },
  }),
  agent('A16', 'delete', '/:conversationId/collaborators/:principalId', 'manageCollaborators'),
  agent('A17', 'patch', '/:conversationId/collaboration-settings', 'settings', { body: { editorsCanInvite: true } }),
  agent('A18', 'post', '/:conversationId/transfer-ownership', 'transfer', { body: { newOwnerUserId: USER_B } }),
  agent('A19', 'post', '/:conversationId/leave', 'leave'),
  agent('A20', 'get', '/:conversationId/feed', 'read'),
  agent('A21', 'get', '/:conversationId/readiness', 'read'),
]

/** The PH-10 mentionables and notes routes, mounted on both routers behind the mentions flag. */
export const MENTION_ROUTES: readonly ConversationRoute[] = [
  chat('C25', 'get', '/:conversationId/mentionables', 'read'),
  chat('C26', 'post', '/:conversationId/notes', 'send', { body: { query: 'FYI', mentions: [{ type: 'user', id: USER_B }], clientMessageId: 'n1' } }),
  agent('A22', 'get', '/:conversationId/mentionables', 'read'),
  agent('A23', 'post', '/:conversationId/notes', 'send', { body: { query: 'FYI', mentions: [{ type: 'user', id: USER_B }], clientMessageId: 'n1' } }),
]

/** Scope each collaboration route demands of an OAuth token (51 section 4). */
export const COLLABORATION_SCOPES: Record<string, string> = {
  read: 'conversation:read',
  invite: 'conversation:share',
  manageCollaborators: 'conversation:share',
  settings: 'conversation:share',
  transfer: 'conversation:share',
  leave: 'conversation:write',
}

export const routeKey = (r: ConversationRoute): string => `${r.method.toUpperCase()} ${r.path}`

export function urlOf(r: ConversationRoute, ids: { conversationId: string; messageId?: string; agentKey?: string; principalId?: string }): string {
  return r.path
    .replace(':conversationId', ids.conversationId)
    .replace(':messageId', ids.messageId ?? '')
    .replace(':agentKey', ids.agentKey ?? AGENT_KEY)
    .replace(':principalId', ids.principalId ?? USER_B)
}
