import { Types } from 'mongoose'
import { InMemoryChatStore, oid } from '../controller/chat-test-harness'
import { AGENT_KEY } from './conversation-routes'
import { buildRouters, Env, ORG } from './conversation-world'
import { CollaborationWorldOptions } from './collaboration-world'
import { headerAuth, serve, Served, TestUser } from './serve-routers'

export type Person = 'A' | 'B' | 'C' | 'D' | 'E'
export type Kind = 'chat' | 'agent'

/** A owns; B, C, D are colleagues in the org; E belongs to another org. */
export const PEOPLE: Record<Person, Types.ObjectId> = { A: oid(), B: oid(), C: oid(), D: oid(), E: oid() }
export const OTHER_ORG = oid()
const NAMES: Record<Person, string> = { A: 'Alice', B: 'Bob', C: 'Carol', D: 'Dan', E: 'Eve' }

export const id = (who: Person): string => String(PEOPLE[who])

export const asUser = (who: Person, scopes?: string[]): TestUser => ({
  userId: id(who),
  orgId: String(who === 'E' ? OTHER_ORG : ORG),
  ...(scopes && { scopes }),
})

export const TEAM_SALES = '11111111-1111-4111-8111-111111111111'
export const TEAM_OPS = '22222222-2222-4222-8222-222222222222'
export const TEAM_GONE = '33333333-3333-4333-8333-333333333333'
export const ORG_WIDE = `all_${String(ORG)}`

export const defaultUsers: NonNullable<CollaborationWorldOptions['users']> = Object.fromEntries(
  (['A', 'B', 'C', 'D'] as Person[]).map((who) => [id(who), { displayName: NAMES[who] }]),
)

export interface Fixture {
  env: Env
  http: Served
  store: InMemoryChatStore
  /** The chat session ids by kind. */
  ids: Record<Kind, string>
  path(kind: Kind, suffix?: string, conversationId?: string): string
  close(): Promise<void>
}

export interface FixtureOptions {
  collab?: boolean
  session?: Record<string, unknown>
  collaboration?: Partial<Omit<CollaborationWorldOptions, 'collab' | 'orgId'>>
}

/** Both routers over HTTP, the in-memory store, and one chat and one agent chat owned by A. */
export async function startFixture(options: FixtureOptions = {}): Promise<Fixture> {
  const store = new InMemoryChatStore()
  store.install()
  const env = buildRouters({
    collab: options.collab ?? true,
    stop: false,
    authenticate: headerAuth,
    collaboration: {
      users: defaultUsers,
      teams: { [TEAM_SALES]: { name: 'Sales' }, [TEAM_OPS]: { name: 'Ops' } },
      teamsOf: (userId) => (userId === id('A') || userId === id('B') ? [TEAM_SALES] : []),
      ...options.collaboration,
    },
  })
  const http = await serve(env)
  const make = (kind: Kind): string =>
    String(
      store.addSession({
        orgId: ORG,
        userId: PEOPLE.A,
        initiator: PEOPLE.A,
        title: `${kind} chat`,
        sessionType: kind,
        ...(kind === 'agent' && { agentKey: AGENT_KEY, conversationSource: 'agent_chat' }),
        ...options.session,
      })._id,
    )
  const ids = { chat: make('chat'), agent: make('agent') }
  store.writes.length = 0
  return {
    env,
    http,
    store,
    ids,
    path: (kind, suffix = '', conversationId = ids[kind]) =>
      kind === 'chat' ? `/api/v1/conversations/${conversationId}${suffix}` : `/api/v1/agents/${AGENT_KEY}/conversations/${conversationId}${suffix}`,
    close: () => http.close(),
  }
}

export const rowsOf = (f: Fixture, kind: Kind): Array<{ userId?: unknown; teamId?: string; accessLevel: string }> =>
  (f.store.session(f.ids[kind])!.toObject().sharedWith ?? []) as never

export const user = (who: Person, accessLevel: 'read' | 'write') => ({ principalType: 'user' as const, principalId: id(who), accessLevel })
export const team = (teamId: string, accessLevel: 'read' | 'write') => ({ principalType: 'team' as const, principalId: teamId, accessLevel })
