import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ConversationTurnDeps } from '../../../../src/modules/enterprise_search/services/conversation/turn/turn-deps'
import { FakeSSEResponse, settle } from '../controller/chat-test-harness'
import { AGENT_KEY } from './conversation-routes'
import { ACTORS, ActorName, Seed, seed } from './conversation-world'
import { realGuards } from './guarded-chat'
import { turnDeps } from './turn-deps'

export type Kind = 'chat' | 'agent'
export type Mode = 'stream' | 'plain'

const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never

export interface TurnWorldOptions {
  deps?: Partial<ConversationTurnDeps>
  /** The author of the seeded questions (legacy rows have none and read as the owner's). */
  questionAuthor?: Types.ObjectId
}

export interface Turn {
  req: any
  res: FakeSSEResponse
  /** What the chain passed to `next(err)`: a guard denial or a handler error before the stream opened. */
  error?: Error & { statusCode?: number; code?: string; publicDetails?: Record<string, unknown> }
  /** The handler ran (the guards let the request through). */
  reached: boolean
}

/** A seeded world with the default guards and the turn handlers, run exactly as the routes mount them. */
export function turnWorld(over: Record<string, unknown> = {}, options: TurnWorldOptions = {}) {
  const s: Seed = seed(over, options.questionAuthor)
  const guards = realGuards()
  const deps = turnDeps(options.deps)
  /** Run leases taken by turns; the default guards take none. */
  const handles: unknown[] = []

  type Handler = (r: unknown, s: unknown, n: (e?: unknown) => void) => Promise<void>
  const userOf = (who: ActorName) => ({ userId: String(ACTORS[who].userId), orgId: String(ACTORS[who].orgId), email: `${who}@example.com` })
  const requestOf = (who: ActorName, params: Record<string, unknown>, body: Record<string, unknown>, requestId: string): any => {
    const req: any = {
      user: userOf(who),
      params,
      body,
      query: {},
      headers: { authorization: `Bearer token-${who}` },
      context: { requestId },
      on: () => req,
    }
    return req
  }
  const run = (req: any, res: FakeSSEResponse, chain: Array<(r: any, s: any, n: (e?: unknown) => void) => unknown>, handler: Handler): Promise<Turn> => {
    const turn: Turn = { req, res, reached: false }
    return new Promise<Turn>((resolve) => {
      const fail = (error: unknown): void => {
        turn.error = error as Turn['error']
        resolve(turn)
      }
      const step = (i: number): void => {
        if (i === chain.length) {
          turn.reached = true
          handler(req, res, (e?: unknown) => {
            if (e !== undefined) fail(e)
          }).then(
            () => resolve(turn),
            (e: unknown) => fail(e),
          )
          return
        }
        void chain[i]!(req, res, (e?: unknown) => (e !== undefined ? fail(e) : step(i + 1)))
      }
      step(0)
    })
  }

  const start = (who: ActorName, kind: Kind = 'chat', mode: Mode = 'stream', body: Record<string, unknown> = {}): Promise<Turn> => {
    const req = requestOf(who, { conversationId: s.ids[kind], messageId: s.messageIds[kind], ...(kind === 'agent' && { agentKey: AGENT_KEY }) }, { query: 'More detail', ...body }, 'req-turn')
    const handler = (
      mode === 'stream'
        ? kind === 'chat'
          ? controller.addMessageStream(appConfig, deps)
          : controller.addMessageStreamToAgentConversation(appConfig, deps)
        : kind === 'chat'
          ? controller.addMessage(appConfig, deps)
          : controller.addMessageToAgentConversation(appConfig, deps)
    ) as unknown as Handler
    return run(req, new FakeSSEResponse(), [guards.authorize('send', kind) as never, guards.runLease(kind) as never], handler)
  }

  /** A first send: `caller()` and the create handler, exactly as the route mounts them. The conversation does not exist yet. */
  const startCreate = (who: ActorName, kind: Kind = 'chat', mode: Mode = 'stream', body: Record<string, unknown> = {}): Promise<Turn> => {
    const req = requestOf(who, kind === 'agent' ? { agentKey: AGENT_KEY } : {}, { query: 'First question', ...body }, 'req-create')
    const res = new FakeSSEResponse()
    // Recorded among the store's writes so a test can see which came first, the SSE head or the session.
    const writeHead = res.writeHead.bind(res)
    res.writeHead = (...args) => {
      s.store.writes.push('writeHead')
      return writeHead(...args)
    }
    const handler = (
      mode === 'stream'
        ? kind === 'chat'
          ? controller.streamChat(appConfig, deps)
          : controller.streamAgentConversation(appConfig, deps)
        : kind === 'chat'
          ? controller.createConversation(appConfig, deps)
          : controller.createAgentConversation(appConfig, deps)
    ) as unknown as Handler
    return run(req, res, [guards.caller() as never], handler)
  }

  /** Regenerates the answer `messageId` (default: the seeded one): `authorize('regenerate')`, `runLease()` and the handler. */
  const startRegenerate = (who: ActorName, kind: Kind = 'chat', body: Record<string, unknown> = {}, messageId: string = s.messageIds[kind]): Promise<Turn> => {
    const req = requestOf(who, { conversationId: s.ids[kind], messageId, ...(kind === 'agent' && { agentKey: AGENT_KEY }) }, { chatMode: 'quick', ...body }, 'req-regen')
    const handler = (kind === 'chat' ? controller.regenerateAnswers(appConfig, deps) : controller.regenerateAgentAnswers(appConfig, deps)) as unknown as Handler
    return run(req, new FakeSSEResponse(), [guards.authorize('regenerate', kind) as never, guards.runLease(kind, { op: 'regenerate' }) as never], handler)
  }

  const seededIds = new Set(s.store.sessions.map((x) => String(x._id)))
  /** Conversations a first send created, oldest first. */
  const created = (): Array<Record<string, any>> => s.store.sessions.filter((x) => !seededIds.has(String(x._id))).map((x) => x.toObject() as Record<string, any>)
  const rowsOf = (sessionId: unknown): Array<Record<string, any>> => s.store.messagesOf(sessionId) as Array<Record<string, any>>
  const session = (kind: Kind = 'chat') => s.store.session(s.ids[kind])!.toObject() as Record<string, any>
  const rows = (kind: Kind = 'chat') => s.store.messagesOf(s.ids[kind]) as Array<Record<string, any>>
  /** Answers the streaming turn: one text delta, then the final answer, then the end of the stream. */
  const answerStream = async (text = 'The answer.', extra: Record<string, unknown> = {}): Promise<void> => {
    s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: text })
    s.ai.send('RUN_FINISHED', { runId: 'run-root', result: { answer: text, citations: [], confidence: 'High', ...extra } })
    s.ai.finish()
    await settle()
  }
  return { s, guards, handles, deps, start, startCreate, startRegenerate, created, rowsOf, session, rows, answerStream }
}

export type TurnWorld = ReturnType<typeof turnWorld>
