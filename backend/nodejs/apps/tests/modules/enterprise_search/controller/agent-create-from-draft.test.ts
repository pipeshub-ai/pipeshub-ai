import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Types } from 'mongoose'
import { createAgent } from '../../../../src/modules/enterprise_search/controller/es_controller'
import {
  AgentDraftRefResolver,
  IDraftRowLookup,
} from '../../../../src/modules/enterprise_search/services/collaboration/agent-draft/agent-draft-ref.service'
import { ConversationNotFoundError } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { AgentHandleError } from '../../../../src/modules/enterprise_search/utils/agent-handle-error'

const USER = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const OTHER = 'dddddddddddddddddddddddd'
const ORG = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const CONVERSATION = 'cccccccccccccccccccccccc'
const MESSAGE = '111111111111111111111111'
const SESSION_ID = new Types.ObjectId(CONVERSATION)

const appConfig: any = {
  aiBackend: 'http://ai:8000',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
}

interface Sent {
  uri: string
  headers: Record<string, string>
  body: Record<string, unknown>
}

function stubPython(response: unknown = { statusCode: 200, data: { agent: { _key: 'agent-1' } } }): { sent: Sent[] } {
  const sent: Sent[] = []
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: any) {
    sent.push({ uri: this.uri, headers: this.headers, body: JSON.parse(this.body) })
    return Promise.resolve(response as any)
  })
  return { sent }
}

function resolver(opts: { requester?: string | null; flag?: boolean; deny?: boolean } = {}) {
  const guards = {
    authorizeById: sinon.stub().callsFake(async () => {
      if (opts.deny) throw new ConversationNotFoundError()
      return { session: { _id: SESSION_ID } }
    }),
  }
  const rows: IDraftRowLookup = {
    requesterOf: sinon.stub().resolves(opts.requester === undefined ? USER : opts.requester),
  }
  const flags = { isEnabled: sinon.stub().resolves(opts.flag ?? true) }
  return { guards, rows, flags, resolver: new AgentDraftRefResolver(guards as any, flags as any, rows) }
}

function request(body: Record<string, unknown>) {
  return {
    headers: { authorization: 'Bearer user-session' },
    body,
    params: {},
    query: {},
    user: { userId: USER, orgId: ORG },
    context: { requestId: 'r-1' },
  } as any
}

function response() {
  const res: any = { status: sinon.stub(), json: sinon.stub() }
  res.status.returns(res)
  return res
}

const DRAFT_REF = { conversationId: CONVERSATION, messageId: MESSAGE }

describe('createAgent from a chat draft', () => {
  afterEach(() => sinon.restore())

  it('AB-02: Python gets the server-set provenance and a token bound to the draft', async () => {
    const { sent } = stubPython()
    const { resolver: draftRefs, guards } = resolver()
    const res = response()
    const next = sinon.stub()

    await createAgent(appConfig, draftRefs)(
      request({ name: 'Offer drafter', handle: 'offer-drafter', draftRef: DRAFT_REF }),
      res,
      next,
    )

    expect(next.called).to.equal(false)
    expect(res.status.calledWith(201)).to.equal(true)
    expect(guards.authorizeById.firstCall.args.slice(1)).to.deep.equal(['read', 'chat', CONVERSATION])
    expect(sent).to.have.length(1)
    expect(sent[0].uri).to.equal('http://ai:8000/api/v1/agent/internal/create-from-chat')
    expect(sent[0].body).to.deep.equal({
      name: 'Offer drafter',
      handle: 'offer-drafter',
      createdVia: 'chat',
      sourceConversationId: CONVERSATION,
      sourceMessageId: MESSAGE,
    })
    const token = sent[0].headers.authorization.replace('Bearer ', '')
    expect(sent[0].headers.authorization).to.not.contain('user-session')
    const claims = jwt.verify(token, 'test-scoped-secret') as any
    expect(claims).to.include({ userId: USER, orgId: ORG, conversationId: CONVERSATION, messageId: MESSAGE })
    expect(claims.scopes).to.deep.equal(['agent:create:chat'])
    expect(claims.exp - claims.iat).to.be.at.most(60)
  })

  it('overrides provenance keys even if one reached the controller', async () => {
    const { sent } = stubPython()

    await createAgent(appConfig, resolver().resolver)(
      request({ name: 'X', draftRef: DRAFT_REF, createdVia: 'ui', sourceConversationId: 'forged', sourceMessageId: 'forged' }),
      response(),
      sinon.stub(),
    )

    expect(sent[0].body).to.include({ createdVia: 'chat', sourceConversationId: CONVERSATION, sourceMessageId: MESSAGE })
  })

  it('PH11-06: a draft another user asked for is a 404 and Python is never called', async () => {
    const { sent } = stubPython()
    const next = sinon.stub()

    await createAgent(appConfig, resolver({ requester: OTHER }).resolver)(request({ name: 'X', draftRef: DRAFT_REF }), response(), next)

    expect(sent).to.have.length(0)
    expect(next.firstCall.args[0]).to.be.instanceOf(ConversationNotFoundError)
  })

  it('a message that is not a draft card is a 404', async () => {
    const { sent } = stubPython()
    const next = sinon.stub()

    await createAgent(appConfig, resolver({ requester: null }).resolver)(request({ name: 'X', draftRef: DRAFT_REF }), response(), next)

    expect(sent).to.have.length(0)
    expect(next.firstCall.args[0]).to.be.instanceOf(ConversationNotFoundError)
  })

  it('a conversation the caller cannot read is a 404 before any lookup', async () => {
    const { sent } = stubPython()
    const next = sinon.stub()
    const { resolver: draftRefs, rows } = resolver({ deny: true })

    await createAgent(appConfig, draftRefs)(request({ name: 'X', draftRef: DRAFT_REF }), response(), next)

    expect(sent).to.have.length(0)
    expect((rows.requesterOf as sinon.SinonStub).called).to.equal(false)
    expect(next.firstCall.args[0]).to.be.instanceOf(ConversationNotFoundError)
  })

  it('with the builder flag off the draftRef is a 404 without touching the conversation', async () => {
    const { sent } = stubPython()
    const next = sinon.stub()
    const { resolver: draftRefs, guards } = resolver({ flag: false })

    await createAgent(appConfig, draftRefs)(request({ name: 'X', draftRef: DRAFT_REF }), response(), next)

    expect(sent).to.have.length(0)
    expect(guards.authorizeById.called).to.equal(false)
    expect(next.firstCall.args[0]).to.be.instanceOf(ConversationNotFoundError)
  })

  it('every refusal is the same error, so a probe learns nothing', async () => {
    stubPython()
    const errors: Error[] = []
    for (const opts of [{ requester: OTHER }, { requester: null }, { deny: true }, { flag: false }]) {
      const next = sinon.stub()
      await createAgent(appConfig, resolver(opts).resolver)(request({ name: 'X', draftRef: DRAFT_REF }), response(), next)
      errors.push(next.firstCall.args[0])
    }
    expect(new Set(errors.map((e) => `${e.name}:${e.message}`)).size).to.equal(1)
  })

  it('a draftRef with a malformed message id is a 404', async () => {
    const { sent } = stubPython()
    const next = sinon.stub()

    await createAgent(appConfig, resolver().resolver)(request({ name: 'X', draftRef: { ...DRAFT_REF, messageId: 'nope' } }), response(), next)

    expect(sent).to.have.length(0)
    expect(next.firstCall.args[0]).to.be.instanceOf(ConversationNotFoundError)
  })

  it('without a draftRef the request goes to the ordinary route as the user', async () => {
    const { sent } = stubPython()

    await createAgent(appConfig, resolver().resolver)(request({ name: 'Plain' }), response(), sinon.stub())

    expect(sent[0].uri).to.equal('http://ai:8000/api/v1/agent/create')
    expect(sent[0].headers.authorization).to.equal('Bearer user-session')
    expect(sent[0].body).to.deep.equal({ name: 'Plain' })
  })

  it('relays an access refusal with the offending ids', async () => {
    stubPython({
      statusCode: 400,
      data: { detail: { code: 'INVALID_KNOWLEDGE', message: 'Some knowledge sources are not available to you.', ids: ['kb-1'] } },
    })
    const next = sinon.stub()

    await createAgent(appConfig, resolver().resolver)(request({ name: 'X', draftRef: DRAFT_REF }), response(), next)

    const error = next.firstCall.args[0] as AgentHandleError
    expect(error).to.be.instanceOf(AgentHandleError)
    expect(error.statusCode).to.equal(400)
    expect(error.publicDetails).to.deep.equal({ ids: ['kb-1'] })
  })
})
