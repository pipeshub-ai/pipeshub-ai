/** Drives each streaming chat handler against the in-memory store and fake AI service. */
import * as controllerModule from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

export type Controller = typeof controllerModule
export type StreamHandler = (req: never, res: never) => Promise<unknown>
type SessionDoc = InstanceType<typeof ChatSession>

export const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never
export const ORG = oid()
export const OWNER = oid()
export const AGENT_KEY = 'agent-1'
export const RUN = 'run-root'

export interface Prepared {
  params: Record<string, string>
  body: Record<string, unknown>
  existing?: SessionDoc
  replacedMessageId?: string
}

export interface Flow {
  name: string
  handler: (c: Controller) => StreamHandler
  prepare: (store: InMemoryChatStore) => Prepared
  aiPath: string
  agent: boolean
  regenerate: boolean
}

export const seedConversation = (store: InMemoryChatStore, agent: boolean): { session: SessionDoc; botId: string } => {
  const session = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Earlier chat',
    sessionType: agent ? 'agent' : 'chat',
    ...(agent ? { agentKey: AGENT_KEY, conversationSource: 'agent_chat' } : {}),
  })
  store.addMessage(session, { messageType: 'user_query', content: 'What changed in the release?' })
  const bot = store.addMessage(session, { messageType: 'bot_response', content: 'An older answer.' })
  store.writes.length = 0
  return { session, botId: String(bot._id) }
}

export const flows: Flow[] = [
  {
    name: 'streamChat',
    handler: (c) => c.streamChat(appConfig) as StreamHandler,
    prepare: () => ({ params: {}, body: { query: 'What changed in the release?' } }),
    aiPath: '/api/v1/chat/stream',
    agent: false,
    regenerate: false,
  },
  {
    name: 'addMessageStream',
    handler: (c) => c.addMessageStream(appConfig) as StreamHandler,
    prepare: (store) => {
      const { session } = seedConversation(store, false)
      return { params: { conversationId: String(session._id) }, body: { query: 'And the one before?' }, existing: session }
    },
    aiPath: '/api/v1/chat/stream',
    agent: false,
    regenerate: false,
  },
  {
    name: 'regenerateAnswers',
    handler: (c) => c.regenerateAnswers(appConfig) as StreamHandler,
    prepare: (store) => {
      const { session, botId } = seedConversation(store, false)
      return { params: { conversationId: String(session._id), messageId: botId }, body: {}, existing: session, replacedMessageId: botId }
    },
    aiPath: '/api/v1/chat/stream',
    agent: false,
    regenerate: true,
  },
  {
    name: 'streamAgentConversation',
    handler: (c) => c.streamAgentConversation(appConfig) as StreamHandler,
    prepare: () => ({ params: { agentKey: AGENT_KEY }, body: { query: 'Summarise the roadmap' } }),
    aiPath: `/api/v1/agent/${AGENT_KEY}/chat/stream`,
    agent: true,
    regenerate: false,
  },
  {
    name: 'addMessageStreamToAgentConversation',
    handler: (c) => c.addMessageStreamToAgentConversation(appConfig) as StreamHandler,
    prepare: (store) => {
      const { session } = seedConversation(store, true)
      return { params: { conversationId: String(session._id), agentKey: AGENT_KEY }, body: { query: 'More detail please' }, existing: session }
    },
    aiPath: `/api/v1/agent/${AGENT_KEY}/chat/stream`,
    agent: true,
    regenerate: false,
  },
  {
    name: 'regenerateAgentAnswers',
    handler: (c) => c.regenerateAgentAnswers(appConfig) as StreamHandler,
    prepare: (store) => {
      const { session, botId } = seedConversation(store, true)
      return {
        params: { conversationId: String(session._id), agentKey: AGENT_KEY, messageId: botId },
        body: {},
        existing: session,
        replacedMessageId: botId,
      }
    },
    aiPath: `/api/v1/agent/${AGENT_KEY}/chat/stream`,
    agent: true,
    regenerate: true,
  },
]

export interface Run {
  store: InMemoryChatStore
  ai: FakeAIBackend
  res: FakeSSEResponse
  prepared: Prepared
  conversation: () => SessionDoc
  messages: () => Array<Record<string, unknown>>
  answer: () => Record<string, unknown> | undefined
}

/** Starts one streaming request against the real controller and returns handles to drive and inspect it. */
export async function startStream(flow: Flow, c: Controller = controllerModule, before?: (ai: FakeAIBackend) => void): Promise<Run> {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()
  before?.(ai)
  const prepared = flow.prepare(store)
  const res = new FakeSSEResponse()
  const req = {
    headers: { authorization: 'Bearer token' },
    params: prepared.params,
    body: prepared.body,
    query: {},
    user: { userId: OWNER, orgId: ORG, email: 'owner@example.com' },
    context: { requestId: 'req-stream' },
  }
  await flow.handler(c)(req as never, res as never)
  await settle()
  const conversation = (): SessionDoc => {
    const doc = prepared.existing ?? store.sessions[0]
    if (!doc) throw new Error('no conversation was created')
    return doc
  }
  const messages = (): Array<Record<string, unknown>> => store.messagesOf(conversation()._id)
  const answer = (): Record<string, unknown> | undefined =>
    prepared.replacedMessageId
      ? messages().find((m) => String(m._id) === prepared.replacedMessageId)
      : messages()
          .filter((m) => m.messageType === 'bot_response' || m.messageType === 'error')
          .at(-1)
  return { store, ai, res, prepared, conversation, messages, answer }
}

export const delta = (text: string, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  runId: RUN,
  messageId: 'm1',
  delta: text,
  ...extra,
})

export const finalAnswer = (answer: string): Record<string, unknown> => ({
  runId: RUN,
  result: { answer, citations: [], confidence: 'High' },
})


