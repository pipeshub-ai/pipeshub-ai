import 'reflect-metadata'
import { pathToFileURL } from 'url'
import { expect } from 'chai'
import type { z } from 'zod'
import {
  agentAddMessageParamsSchema,
  agentStreamCreateSchema,
  addMessageStreamParamsSchema,
  slackAddMessageStreamSchema,
  slackStreamCreateSchema,
  enterpriseSearchStreamCreateSchema,
} from '../../src/modules/enterprise_search/validators/es_validators'
import {
  chatTurnFieldRole,
  chatTurnInput,
} from '../../src/modules/agent-harness/transport/chat-turn-route'

/**
 * R33JGAPS-FU-1: every `/mcp` chat turn ran on today's loop because the bundled `@pipeshub-ai/mcp` SDK sends a field
 * (`keywordSearchBudget`) the durable route did not take. These cases drive the SDK's own `pipeshub_chat` tool, take
 * the body it puts on the wire, validate it as the chat stream route does, and hand it to the durable route's
 * `chatTurnInput`: a new SDK field the durable route does not serve fails here, not silently in production.
 */

// eslint-disable-next-line @typescript-eslint/no-implied-eval -- the constant source keeps import() out of ts-node's CommonJS rewrite
const importESM = new Function('specifier', 'return import(specifier)') as (specifier: string) => Promise<unknown>
const esm = async <T>(path: string): Promise<T> =>
  (await importESM(pathToFileURL(require.resolve(`@pipeshub-ai/mcp/esm/${path}`)).href)) as T

interface ChatTool {
  tool(client: unknown, args: Record<string, unknown>, ctx: { signal?: AbortSignal }): Promise<unknown>
}

interface Sent {
  path: string
  body: Record<string, unknown>
}

const CONVERSATION = '65f1c0a1b2c3d4e5f6a7b8c9'
const AGENT = 'agent-key-1'

/** The SDK's request for one call, captured at its HTTP client; the stream answers 503, so nothing else is sent. */
async function sdkRequest(args: Record<string, unknown>, actions = true): Promise<Sent> {
  const { PipeshubCore } = await esm<{ PipeshubCore: new (o: Record<string, unknown>) => unknown }>('core.js')
  const { HTTPClient } = await esm<{ HTTPClient: new (o: Record<string, unknown>) => unknown }>('lib/http.js')
  const { createChatTool } = await esm<{ createChatTool(o: { actions: boolean }): ChatTool }>(
    'mcp-server/tools/pipeshubChat.js',
  )
  const sent: Sent[] = []
  const fetcher = async (request: Request): Promise<Response> => {
    sent.push({ path: new URL(request.url).pathname, body: (await request.json()) as Record<string, unknown> })
    return new Response('unavailable', { status: 503 })
  }
  const client = new PipeshubCore({
    security: { bearerAuth: 'token' },
    serverURL: 'http://pipeshub.invalid/api/v1',
    httpClient: new HTTPClient({ fetcher }),
  })
  await createChatTool({ actions }).tool(client, args, {})
  expect(sent, 'the SDK sent one chat request').to.have.length(1)
  return sent[0]!
}

/** The chat stream route the SDK's path reaches, as `es.routes.ts` validates it. */
function routeFor(path: string): { schema: z.ZodObject; params: Record<string, string>; kind: 'chat' | 'agent' } {
  const agentFollowUp = path.match(/\/agents\/([^/]+)\/conversations\/([^/]+)\/messages\/stream$/)
  if (agentFollowUp) {
    return {
      schema: agentAddMessageParamsSchema,
      params: { agentKey: agentFollowUp[1]!, conversationId: agentFollowUp[2]! },
      kind: 'agent',
    }
  }
  const agentNew = path.match(/\/agents\/([^/]+)\/conversations\/stream$/)
  if (agentNew) return { schema: agentStreamCreateSchema, params: { agentKey: agentNew[1]! }, kind: 'agent' }
  const followUp = path.match(/\/conversations\/([^/]+)\/messages\/stream$/)
  if (followUp) return { schema: addMessageStreamParamsSchema, params: { conversationId: followUp[1]! }, kind: 'chat' }
  if (/\/conversations\/stream$/.test(path)) return { schema: enterpriseSearchStreamCreateSchema, params: {}, kind: 'chat' }
  throw new Error(`no chat stream route for ${path}`)
}

const CALLS: Array<[string, Record<string, unknown>, boolean]> = [
  ['a new chat', { query: 'What is our leave policy?' }, true],
  ['a follow-up', { query: 'And for contractors?', conversationId: CONVERSATION }, true],
  ['a new chat with an agent', { query: 'Summarise the incident', agentId: AGENT }, true],
  ['a follow-up with an agent', { query: 'Who owns it?', agentId: AGENT, conversationId: CONVERSATION }, true],
  ['a chat whose token may not act', { query: 'What is our leave policy?' }, false],
]

describe('contract: the bundled @pipeshub-ai/mcp chat request is a durable turn (R33JGAPS-FU-1)', () => {
  for (const [name, args, actions] of CALLS) {
    it(`${name}: the route takes the SDK's body and the durable route serves every field of it`, async () => {
      const { path, body } = await sdkRequest(args, actions)
      const route = routeFor(path)
      const parsed = route.schema.parse({ body, params: route.params }) as { body: Record<string, unknown> }
      const unserved = Object.keys(parsed.body).filter((key) => {
        const role = chatTurnFieldRole(key)
        return role === undefined || role === 'legacy'
      })
      expect(unserved, 'fields the SDK sends that would keep the turn on legacy').to.deep.equal([])
      const input = chatTurnInput(parsed.body, route.kind)
      expect(input, 'chatTurnInput serves the SDK body').to.not.equal(null)
      expect(input!.keywordSearchBudget, "the SDK's search budget reaches the run").to.equal(
        parsed.body['keywordSearchBudget'],
      )
    })
  }
})

describe('contract: every field a chat stream route accepts has a durable role (R33JGAPS-FU-1)', () => {
  const routes: Array<[string, z.ZodObject]> = [
    ['POST /conversations/stream', enterpriseSearchStreamCreateSchema],
    ['POST /conversations/:id/messages/stream', addMessageStreamParamsSchema],
    ['POST /agents/:key/conversations/stream', agentStreamCreateSchema],
    ['POST /agents/:key/conversations/:id/messages/stream', agentAddMessageParamsSchema],
    ['POST /conversations/internal/stream (Slack)', slackStreamCreateSchema],
    ['POST /conversations/internal/:id/messages/stream (Slack)', slackAddMessageStreamSchema],
  ]
  for (const [route, schema] of routes) {
    it(`${route}: a run input, served, routing, or legacy-only with a reason`, () => {
      const body = (schema.shape as unknown as { body: z.ZodObject }).body
      const unknown = Object.keys(body.shape).filter((key) => chatTurnFieldRole(key) === undefined)
      expect(unknown, 'fields the durable route does not know (add a role, or a LEGACY_ONLY_FIELDS reason)').to.deep.equal([])
    })
  }
})
