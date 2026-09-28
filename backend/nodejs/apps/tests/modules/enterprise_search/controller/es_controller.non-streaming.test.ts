import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import jwt from 'jsonwebtoken'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { HttpError } from '../../../../src/libs/errors/http.errors'
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../../src/libs/errors/backend-error'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { CONVERSATION_ID_HEADER } from '../../../../src/modules/enterprise_search/utils/non-streaming-chat'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import {
  FakeAIBackend,
  FakeSSEResponse,
  InMemoryChatStore,
  fakeReplicaSetSession,
  oid,
} from './chat-test-harness'
import { AGENT_KEY, ORG, OWNER, appConfig, seedConversation } from './streaming-flows'

/**
 * The four non-streaming chat routes, run end to end against the in-memory
 * store and a fake AI backend: everything between the HTTP request and those
 * two edges is the real controller, persistence and error mapping.
 */

type Handler = (req: never, res: never, next: never) => Promise<unknown>
type SessionDoc = ReturnType<InMemoryChatStore['addSession']>

interface Flow {
  name: string
  handler: () => Handler
  agent: boolean
  create: boolean
  aiPath: string
  seed: (store: InMemoryChatStore) => { params: Record<string, string>; existing?: SessionDoc }
}

const flows: Flow[] = [
  {
    name: 'createConversation',
    handler: () => controller.createConversation(appConfig) as Handler,
    agent: false,
    create: true,
    aiPath: '/api/v1/chat',
    seed: () => ({ params: {} }),
  },
  {
    name: 'addMessage',
    handler: () => controller.addMessage(appConfig) as Handler,
    agent: false,
    create: false,
    aiPath: '/api/v1/chat',
    seed: (store) => {
      const { session } = seedConversation(store, false)
      return { params: { conversationId: String(session._id) }, existing: session }
    },
  },
  {
    name: 'createAgentConversation',
    handler: () => controller.createAgentConversation(appConfig) as Handler,
    agent: true,
    create: true,
    aiPath: `/api/v1/agent/${AGENT_KEY}/chat`,
    seed: () => ({ params: { agentKey: AGENT_KEY } }),
  },
  {
    name: 'addMessageToAgentConversation',
    handler: () => controller.addMessageToAgentConversation(appConfig) as Handler,
    agent: true,
    create: false,
    aiPath: `/api/v1/agent/${AGENT_KEY}/chat`,
    seed: (store) => {
      const { session } = seedConversation(store, true)
      return { params: { conversationId: String(session._id), agentKey: AGENT_KEY }, existing: session }
    },
  },
]

const exactPath = (path: string): RegExp => new RegExp(`${path.replace(/[/]/g, '\\/')}$`)

const answer = (text: string, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  answer: text,
  citations: [],
  confidence: 'High',
  ...extra,
})

interface Run {
  store: InMemoryChatStore
  ai: FakeAIBackend
  res: FakeSSEResponse
  next: sinon.SinonStub
  conversation: () => SessionDoc
  messages: () => Array<Record<string, unknown>>
  error: () => HttpError | undefined
}

async function run(
  flow: Flow,
  setup: (ai: FakeAIBackend, store: InMemoryChatStore) => void,
  overrides: { body?: Record<string, unknown>; req?: Record<string, unknown> } = {},
): Promise<Run> {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()
  const { params, existing } = flow.seed(store)
  setup(ai, store)
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = {
    headers: { authorization: 'Bearer user-token' },
    params,
    body: { query: 'What changed in the release?', ...overrides.body },
    query: {},
    user: { userId: OWNER, orgId: ORG, email: 'owner@example.com' },
    context: { requestId: 'req-non-stream' },
    ...overrides.req,
  }
  await flow.handler()(req as never, res as never, next as never)
  const conversation = (): SessionDoc => {
    const doc = existing ?? store.sessions[0]
    if (!doc) throw new Error('no conversation was created')
    return doc
  }
  return {
    store,
    ai,
    res,
    next,
    conversation,
    messages: () => store.messagesOf(conversation()._id),
    error: () => next.firstCall?.args[0] as HttpError | undefined,
  }
}

describe('es_controller non-streaming chat routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  for (const flow of flows) {
    describe(flow.name, () => {
      it('persists the question and the answer and returns the conversation', async () => {
        const r = await run(flow, (ai) => ai.reply(exactPath(flow.aiPath), 200, answer('Release 2.1 shipped SSO.')))

        expect(r.next.called, String(r.error()?.message)).to.be.false
        expect(r.res.statusCode).to.equal(flow.create ? 201 : 200)
        const body = r.res.jsonBody as { conversation: { _id: unknown; messages: Array<{ content: string }> } }
        expect(String(body.conversation._id)).to.equal(String(r.conversation()._id))
        expect(body.conversation.messages.at(-1)?.content).to.equal('Release 2.1 shipped SSO.')

        const saved = r.messages()
        expect(saved.at(-2)).to.include({ messageType: 'user_query', content: 'What changed in the release?' })
        expect(saved.at(-1)).to.include({ messageType: 'bot_response', content: 'Release 2.1 shipped SSO.' })
        expect(r.conversation().status).to.equal('Complete')
        expect(r.res.headers[CONVERSATION_ID_HEADER.toLowerCase()]).to.equal(String(r.conversation()._id))
      })

      it('calls the non-streaming AI route once, with the conversation and its history', async () => {
        const r = await run(flow, (ai) => ai.reply(exactPath(flow.aiPath), 200, answer('ok')))

        expect(r.ai.calls).to.have.length(1)
        const call = r.ai.calls[0]!
        expect(call.url).to.equal(`http://ai.test${flow.aiPath}`)
        expect(call.method).to.equal('POST')
        expect(call.body.conversationId).to.equal(String(r.conversation()._id))
        expect(call.body.query).to.equal('What changed in the release?')
        if (flow.create) {
          expect(call.body.previousConversations).to.deep.equal([])
          expect(call.body).to.have.property('recordIds')
        } else {
          // The earlier turn is history; the new question is only `query`.
          expect(call.body.previousConversations).to.deep.equal([
            { content: 'What changed in the release?', role: 'user_query' },
            { content: 'An older answer.', role: 'bot_response' },
          ])
          expect(call.body).not.to.have.property('recordIds')
        }
      })

      it('saves citations and counts them', async () => {
        const citation = {
          content: 'SSO landed in 2.1',
          chunkIndex: 0,
          citationType: 'vectordb|document',
          metadata: { recordId: 'r1', recordName: 'Release notes', mimeType: 'text/markdown', origin: 'UPLOAD' },
        }
        const r = await run(flow, (ai) => ai.reply(exactPath(flow.aiPath), 200, answer('SSO [1]', { citations: [citation] })))

        expect(r.store.writes.filter((w) => w === 'citation.save')).to.have.length(1)
        const bot = r.messages().at(-1) as { citations: unknown[] }
        expect(bot.citations).to.have.length(1)
        if (!flow.create) expect((r.res.jsonBody as { recordsUsed: number }).recordsUsed).to.equal(1)
      })

      it('keeps a 4xx from the AI backend, with its message, and records the failure', async () => {
        const r = await run(flow, (ai) =>
          ai.reply(exactPath(flow.aiPath), 424, { status: 'error', code: 'llm_not_configured', message: 'Set up a model first.' }),
        )

        expect(r.error()).to.be.instanceOf(HttpError)
        expect(r.error()?.statusCode).to.equal(424)
        expect(r.error()?.message).to.equal('Set up a model first.')
        expect(r.conversation().status).to.equal('Failed')
        expect(r.conversation().failReason).to.equal('Set up a model first.')
        expect(r.messages().at(-1)).to.include({ messageType: 'error', content: 'Set up a model first.' })
        expect(r.conversation().conversationErrors?.at(-1)?.errorType).to.equal('llm_not_configured')
        expect(r.res.headers[CONVERSATION_ID_HEADER.toLowerCase()]).to.equal(String(r.conversation()._id))
      })

      it('never shows upstream text for a 5xx', async () => {
        const r = await run(flow, (ai) =>
          ai.reply(exactPath(flow.aiPath), 500, { message: 'Traceback: neo4j at 10.0.0.7 refused' }),
        )

        expect(r.error()?.statusCode).to.equal(500)
        expect(r.error()?.message).to.equal(CHAT_ERROR_MESSAGES.failed)
        expect(r.conversation().failReason).to.equal(CHAT_ERROR_MESSAGES.failed)
      })

      it('does not retry an unreachable AI backend, and says the service is unavailable', async () => {
        const refused = Object.assign(new TypeError('fetch failed'), { cause: { code: 'ECONNREFUSED' } })
        const r = await run(flow, (ai) => ai.failNetwork(exactPath(flow.aiPath), refused))

        expect(r.ai.calls, 'an LLM run is not idempotent').to.have.length(1)
        expect(r.error()?.message).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
        expect(r.conversation().status).to.equal('Failed')
        expect(r.conversation().failReason).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      })

      it('treats a 200 without an answer as a failed turn', async () => {
        const r = await run(flow, (ai) => ai.reply(exactPath(flow.aiPath), 200, { citations: [] }))

        expect(r.error()?.statusCode).to.equal(500)
        expect(r.conversation().status).to.equal('Failed')
        expect(r.conversation().conversationErrors?.at(-1)?.errorType).to.equal('no_response')
      })

      it('saves a classified failure answer as an error message on a failed conversation', async () => {
        const r = await run(flow, (ai) =>
          ai.reply(
            exactPath(flow.aiPath),
            200,
            answer('The model provider blocked this answer.', { answerMatchType: 'Error', errorCode: 'content_filter' }),
          ),
        )

        expect(r.next.called).to.be.false
        expect(r.messages().at(-1)).to.include({ messageType: 'error', content: 'The model provider blocked this answer.' })
        expect(r.conversation().status).to.equal('Failed')
      })

      it('keeps a cancelled run as stopped', async () => {
        const r = await run(flow, (ai) => ai.reply(exactPath(flow.aiPath), 200, { answer: '', citations: [], status: 'stopped' }))

        expect(r.next.called).to.be.false
        expect(r.conversation().status).to.equal('Stopped')
      })

      it('rejects a script in the query before writing anything', async () => {
        const r = await run(flow, () => undefined, { body: { query: '<script>alert(1)</script>' } })

        expect(r.error()?.statusCode).to.equal(400)
        expect(r.store.writes).to.deep.equal([])
        expect(r.ai.calls).to.have.length(0)
      })

      it('on a replica set, commits the question before the AI call and saves the answer without the ended session', async () => {
        const previous = process.env.REPLICA_SET_AVAILABLE
        process.env.REPLICA_SET_AVAILABLE = 'true'
        const session = fakeReplicaSetSession()
        const endSession = sinon.spy(session, 'endSession')
        try {
          const r = await run(flow, (ai) => {
            sinon.stub(mongoose, 'startSession').resolves(session as never)
            ai.reply(exactPath(flow.aiPath), 200, answer('Saved after commit.'))
          })

          expect(r.next.called, String(r.error()?.message)).to.be.false
          // The fake session refuses any write after it ended, so a clean
          // finish also proves the answer was saved without it.
          expect(endSession.calledBefore(globalThis.fetch as unknown as sinon.SinonStub), 'no transaction is held across the LLM call').to.be.true
          expect(r.messages().at(-1)).to.include({ content: 'Saved after commit.' })
          expect(r.conversation().status).to.equal('Complete')
        } finally {
          if (previous === undefined) delete process.env.REPLICA_SET_AVAILABLE
          else process.env.REPLICA_SET_AVAILABLE = previous
        }
      })
    })
  }

  describe('multi-turn conversations', () => {
    const chains = [
      {
        name: 'assistant',
        create: () => controller.createConversation(appConfig) as Handler,
        followUp: () => controller.addMessage(appConfig) as Handler,
        params: {} as Record<string, string>,
        aiPath: '/api/v1/chat',
      },
      {
        name: 'agent',
        create: () => controller.createAgentConversation(appConfig) as Handler,
        followUp: () => controller.addMessageToAgentConversation(appConfig) as Handler,
        params: { agentKey: AGENT_KEY } as Record<string, string>,
        aiPath: `/api/v1/agent/${AGENT_KEY}/chat`,
      },
    ]

    for (const chain of chains) {
      it(`${chain.name}: create, a failed follow-up, then two more follow-ups keep one ordered history`, async () => {
        const store = new InMemoryChatStore()
        const ai = new FakeAIBackend()
        store.install()
        ai.install()
        const turn = async (handler: Handler, params: Record<string, string>, query: string) => {
          const res = new FakeSSEResponse()
          const next = sinon.stub()
          await handler(
            {
              headers: { authorization: 'Bearer user-token' },
              params,
              body: { query },
              query: {},
              user: { userId: OWNER, orgId: ORG, email: 'owner@example.com' },
              context: { requestId: 'req-multi-turn' },
            } as never,
            res as never,
            next as never,
          )
          return { res, error: next.firstCall?.args[0] as HttpError | undefined }
        }
        const answerNext = (status: number, body: unknown) => {
          ai.resetReplies()
          ai.reply(exactPath(chain.aiPath), status, body)
        }

        answerNext(200, answer('A1'))
        const first = await turn(chain.create(), chain.params, 'Q1')
        expect(first.error, String(first.error?.message)).to.be.undefined
        const conversationId = first.res.headers[CONVERSATION_ID_HEADER.toLowerCase()]!
        const conversation = () => store.session(conversationId)!
        const followUpParams = { ...chain.params, conversationId }

        answerNext(424, { code: 'llm_not_configured', message: 'Set up a model first.' })
        const second = await turn(chain.followUp(), followUpParams, 'Q2')
        expect(second.error?.statusCode).to.equal(424)
        expect(conversation().status).to.equal('Failed')

        answerNext(200, answer('A3'))
        const third = await turn(chain.followUp(), followUpParams, 'Q3')
        expect(third.error, String(third.error?.message)).to.be.undefined
        expect(conversation().status, 'a successful turn recovers a failed conversation').to.equal('Complete')
        expect(conversation().failReason).to.be.undefined
        // Error messages never go back to the model; the unanswered question does.
        expect(ai.calls[2]!.body.previousConversations).to.deep.equal([
          { content: 'Q1', role: 'user_query' },
          { content: 'A1', role: 'bot_response' },
          { content: 'Q2', role: 'user_query' },
        ])

        answerNext(200, answer('A4'))
        const fourth = await turn(chain.followUp(), followUpParams, 'Q4')
        expect(fourth.error, String(fourth.error?.message)).to.be.undefined
        expect(ai.calls[3]!.body.previousConversations).to.deep.equal([
          { content: 'Q1', role: 'user_query' },
          { content: 'A1', role: 'bot_response' },
          { content: 'Q2', role: 'user_query' },
          { content: 'Q3', role: 'user_query' },
          { content: 'A3', role: 'bot_response' },
        ])
        expect(ai.calls.map((c) => c.body.conversationId)).to.deep.equal(Array(4).fill(conversationId))

        const stored = store.messagesOf(conversationId).map((m) => [m.messageType, m.content])
        expect(stored).to.deep.equal([
          ['user_query', 'Q1'],
          ['bot_response', 'A1'],
          ['user_query', 'Q2'],
          ['error', 'Set up a model first.'],
          ['user_query', 'Q3'],
          ['bot_response', 'A3'],
          ['user_query', 'Q4'],
          ['bot_response', 'A4'],
        ])
        expect((stored.map((_, i) => store.messagesOf(conversationId)[i]!.seq))).to.deep.equal([1, 2, 3, 4, 5, 6, 7, 8])
        // Without a live Mongo connection attachPopulatedCitations returns only the new
        // message (see its doc comment); the full list is checked by the live-stack suite.
        const body = fourth.res.jsonBody as { conversation: { messages: Array<{ content: string }> } }
        expect(body.conversation.messages.at(-1)?.content).to.equal('A4')
      })
    }

    it('a follow-up continues a conversation that was created by the streaming route', async () => {
      const flow = flows.find((f) => f.name === 'addMessage')!
      // seedConversation writes the same session/message shape the streaming handlers persist.
      const r = await run(flow, (ai) => ai.reply(exactPath(flow.aiPath), 200, answer('continued')))

      expect(r.next.called, String(r.error()?.message)).to.be.false
      expect(r.messages().map((m) => m.content)).to.deep.equal([
        'What changed in the release?',
        'An older answer.',
        'What changed in the release?',
        'continued',
      ])
    })
  })

  describe('ownership and conversation type', () => {
    const followUps = flows.filter((f) => !f.create)

    for (const flow of followUps) {
      it(`${flow.name}: an unknown conversation id is a 404 and nothing is written or sent`, async () => {
        const r = await run(flow, (ai) => ai.reply(exactPath(flow.aiPath), 200, answer('none')), {
          req: { params: { ...flow.seed(new InMemoryChatStore()).params, conversationId: String(oid()) } },
        })

        expect(r.error()?.statusCode).to.equal(404)
        expect(r.ai.calls).to.have.length(0)
      })
    }

    for (const flow of followUps) {
      it(`${flow.name}: another user's conversation is a 404 and nothing is written or sent`, async () => {
        const r = await run(flow, (ai) => ai.reply(exactPath(flow.aiPath), 200, answer('leak')), {
          req: { user: { userId: oid(), orgId: ORG, email: 'intruder@example.com' } },
        })

        expect(r.error()?.statusCode).to.equal(404)
        expect(r.store.writes).to.deep.equal([])
        expect(r.ai.calls).to.have.length(0)
      })
    }

    it('addMessage cannot reach an agent conversation through the assistant route', async () => {
      const store = new InMemoryChatStore()
      const ai = new FakeAIBackend()
      store.install()
      ai.install()
      const { session } = seedConversation(store, true)
      const next = sinon.stub()

      await (controller.addMessage(appConfig) as Handler)(
        {
          headers: {},
          params: { conversationId: String(session._id) },
          body: { query: 'hi' },
          query: {},
          user: { userId: OWNER, orgId: ORG },
          context: {},
        } as never,
        new FakeSSEResponse() as never,
        next as never,
      )

      expect((next.firstCall.args[0] as HttpError).statusCode).to.equal(404)
      expect(ai.calls).to.have.length(0)
    })

    it('addMessageToAgentConversation cannot reach a conversation of a different agent', async () => {
      const flow = flows.find((f) => f.name === 'addMessageToAgentConversation')!
      const r = await run(flow, () => undefined, {
        req: { params: { conversationId: '', agentKey: 'other-agent' } },
      })

      expect(r.error()?.statusCode).to.equal(404)
      expect(r.ai.calls).to.have.length(0)
    })
  })

  describe('assistant chat modes', () => {
    const create = flows[0]!

    it('an agent chatMode runs on the universal agent with the selected tools', async () => {
      const r = await run(
        create,
        (ai) => ai.reply(exactPath('/api/v1/agent/agentIdPlaceholder/chat'), 200, answer('done')),
        { body: { chatMode: 'agent', tools: ['jira.search'], agentCapabilities: { webSearch: false } } },
      )

      expect(r.next.called, String(r.error()?.message)).to.be.false
      const call = r.ai.calls[0]!
      expect(call.url).to.equal('http://ai.test/api/v1/agent/agentIdPlaceholder/chat')
      expect(call.body).to.include({ chatMode: 'quick' })
      expect(call.body.tools).to.deep.equal(['jira.search'])
      expect(call.body.agentCapabilities).to.deep.equal({ webSearch: false })
    })

    it('a search chatMode runs on /chat and never forwards tools', async () => {
      const r = await run(create, (ai) => ai.reply(exactPath('/api/v1/chat'), 200, answer('found')), {
        body: { chatMode: 'web_search', tools: ['jira.search'] },
      })

      const call = r.ai.calls[0]!
      expect(call.body.chatMode).to.equal('web_search')
      expect(call.body).not.to.have.property('tools')
    })
  })

  describe('internal (scoped-token) callers', () => {
    // A token without an org is only unambiguous on a single-org instance.
    const singleOrgInstance = () =>
      sinon.stub(Org, 'find').resolves([{ _id: ORG }] as never)

    it('are resolved to their user and re-signed before the AI call', async () => {
      singleOrgInstance()
      const userId = oid()
      sinon.stub(Users, 'findOne').resolves({
        _id: userId,
        orgId: ORG,
        email: 'bot-user@example.com',
        fullName: 'Bot User',
        slug: 'bot-user',
      } as never)
      const create = flows[0]!

      const r = await run(create, (ai) => ai.reply(exactPath('/api/v1/chat'), 200, answer('hi')), {
        req: { user: undefined, tokenPayload: { email: 'bot-user@example.com' }, headers: { authorization: 'Bearer scoped' } },
      })

      expect(r.next.called, String(r.error()?.message)).to.be.false
      expect(String(r.conversation().userId)).to.equal(String(userId))
      const token = r.ai.calls[0]!.headers.authorization!.replace('Bearer ', '')
      expect(token).not.to.equal('scoped')
      expect((jwt.decode(token) as { userId: string }).userId).to.equal(String(userId))
    })

    it('look the user up only inside the org named on the token', async () => {
      sinon.stub(Org, 'findOne').resolves({ _id: ORG } as never)
      const findUser = sinon.stub(Users, 'findOne').resolves({
        _id: oid(),
        orgId: ORG,
        email: 'bot-user@example.com',
        fullName: 'Bot User',
        slug: 'bot-user',
      } as never)
      const create = flows[0]!

      await run(create, (ai) => ai.reply(exactPath('/api/v1/chat'), 200, answer('hi')), {
        req: {
          user: undefined,
          tokenPayload: { email: 'bot-user@example.com', orgId: String(ORG) },
          headers: { authorization: 'Bearer scoped' },
        },
      })

      expect(String(findUser.firstCall.args[0].orgId)).to.equal(String(ORG))
    })

    it('without an org on a multi-org instance are refused and nothing is written', async () => {
      sinon.stub(Org, 'find').resolves([{ _id: ORG }, { _id: oid() }] as never)
      const findUser = sinon.stub(Users, 'findOne')
      const create = flows[0]!

      const r = await run(create, () => undefined, {
        req: { user: undefined, tokenPayload: { email: 'bot-user@example.com' } },
      })

      expect(r.error()?.statusCode).to.equal(401)
      expect(findUser.called).to.be.false
      expect(r.store.writes).to.deep.equal([])
    })

    it('with no matching user are a 404 and nothing is written', async () => {
      singleOrgInstance()
      sinon.stub(Users, 'findOne').resolves(null)
      const create = flows[0]!

      const r = await run(create, () => undefined, {
        req: { user: undefined, tokenPayload: { email: 'nobody@example.com' } },
      })

      expect(r.error()?.statusCode).to.equal(404)
      expect(r.store.writes).to.deep.equal([])
    })
  })
})
