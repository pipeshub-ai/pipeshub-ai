import { ConversationOperation } from '../../../../src/modules/enterprise_search/services/conversation/domain/types'

export type RouteKind = 'chat' | 'agent'
export type Method = 'get' | 'post' | 'put' | 'patch' | 'delete'

export const USER_B = '6abf35278cf29be1a243f001'
export const RUN_ID = '3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c'
export const AGENT_KEY = 'agent-1'

export interface ConversationRoute {
  /** One route table row. */
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

/** Every route with `:conversationId` : the single source for the route-table, matrix and parity suites. */
export const CONVERSATION_ROUTES: readonly ConversationRoute[] = [
  chat('C1', 'post', '/:conversationId/messages', 'send', { body: TURN }),
  chat('C2', 'post', '/internal/:conversationId/messages', 'send', { body: TURN, internal: true }),
  chat('C3', 'post', '/:conversationId/messages/stream', 'send', { body: { query: 'More detail', chatMode: 'internal_search' } }),
  chat('C4', 'post', '/internal/:conversationId/messages/stream', 'send', { body: TURN, internal: true }),
  chat('C5', 'get', '/:conversationId', 'read'),
  chat('C6', 'delete', '/:conversationId', 'delete'),
  chat('C7', 'post', '/:conversationId/share', 'manageSharing', { body: { userIds: ['6abf35278cf29be1a243f001'] } }),
  chat('C8', 'post', '/:conversationId/unshare', 'manageSharing', { body: { userIds: ['6abf35278cf29be1a243f001'] } }),
  chat('C9', 'put', '/:conversationId/project', 'linkProject', { body: { projectId: null } }),
  chat('C10', 'patch', '/:conversationId/project-visibility', 'linkProject', { body: { visibility: 'private' } }),
  chat('C11', 'post', '/:conversationId/message/:messageId/regenerate', 'regenerate', { body: { chatMode: 'quick' } }),
  chat('C12', 'post', '/:conversationId/cancel', 'cancel', { body: { runId: RUN_ID } }),
  chat('C13', 'patch', '/:conversationId/title', 'rename', { body: { title: 'Renamed' } }),
  chat('C14', 'post', '/:conversationId/message/:messageId/feedback', 'feedback', { body: { isHelpful: true } }),
  chat('C15', 'patch', '/:conversationId/archive', 'archiveSelf'),
  chat('C16', 'patch', '/:conversationId/unarchive', 'archiveSelf'),
  chat('C17', 'patch', '/:conversationId/pin', 'archiveSelf'),
  chat('C18', 'patch', '/:conversationId/unpin', 'archiveSelf'),
  chat('C19', 'post', '/:conversationId/message/:messageId/fork', 'fork'),
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
  agent('A14', 'post', '/:conversationId/message/:messageId/fork', 'fork'),
  agent('A15', 'post', '/:conversationId/share', 'manageSharing', { body: { userIds: ['6abf35278cf29be1a243f001'] } }),
  agent('A16', 'post', '/:conversationId/unshare', 'manageSharing', { body: { userIds: ['6abf35278cf29be1a243f001'] } }),
]

export const routeKey = (r: ConversationRoute): string => `${r.method.toUpperCase()} ${r.path}`

export function urlOf(r: ConversationRoute, ids: { conversationId: string; messageId?: string; agentKey?: string; principalId?: string }): string {
  return r.path
    .replace(':conversationId', ids.conversationId)
    .replace(':messageId', ids.messageId ?? '')
    .replace(':agentKey', ids.agentKey ?? AGENT_KEY)
    .replace(':principalId', ids.principalId ?? USER_B)
}
