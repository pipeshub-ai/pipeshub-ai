import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import { Types } from 'mongoose'
import yaml from 'js-yaml'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import * as conversationArtifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * What the agent-chat sharing routes really answer, held against the published
 * spec. The schemas for agent conversations are closed (`additionalProperties:
 * false`), so a field the code returns and the spec does not list, or a field
 * the spec requires and the code leaves out, fails here instead of in a client.
 */

const SPEC = join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')

const appConfig = {
  aiBackend: 'http://ai.test',
  iamBackend: 'http://iam.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const ORG = oid()
const OWNER = oid()
const RECIPIENT = oid()
const AGENT_KEY = 'agent-1'

type Spec = { paths: Record<string, Record<string, any>>; components: { schemas: Record<string, any> } }
const spec = yaml.load(readFileSync(SPEC, 'utf8')) as Spec

type Schema = Record<string, any>

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/**
 * A small OpenAPI 3.0 schema check: `$ref`, `allOf`/`oneOf`/`anyOf`, `type`, `enum`, `nullable`,
 * `properties`, `required`, `additionalProperties` and `items`. Formats are not checked. It reports each
 * way a value falls outside the schema, which is what a client validating the response would reject.
 */
function check(schema: Schema, value: unknown, path = ''): string[] {
  if (schema.$ref) {
    const name = String(schema.$ref).replace('#/components/schemas/', '')
    const target = spec.components.schemas[name]
    if (!target) return [`${path || '/'}: unknown schema ${name}`]
    return check(target, value, path)
  }
  if (value === null) return schema.nullable ? [] : [`${path || '/'}: null is not allowed`]

  const here = path || '/'
  const errors: string[] = []
  for (const part of schema.allOf ?? []) errors.push(...check(part, value, path))
  for (const key of ['oneOf', 'anyOf'] as const) {
    if (schema[key] && !schema[key].some((option: Schema) => check(option, value, path).length === 0)) {
      errors.push(`${here}: matches none of ${key}`)
    }
  }
  if (schema.enum && !schema.enum.includes(value)) errors.push(`${here}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`)

  const type = schema.type ?? (schema.properties ? 'object' : undefined)
  const typeOk: Record<string, boolean> = {
    string: typeof value === 'string',
    boolean: typeof value === 'boolean',
    integer: Number.isInteger(value),
    number: typeof value === 'number',
    array: Array.isArray(value),
    object: isObject(value),
  }
  if (type && typeOk[type] === false) {
    errors.push(`${here}: expected ${type}, got ${Array.isArray(value) ? 'array' : typeof value}`)
    return errors
  }

  if (type === 'array') {
    if (schema.items) (value as unknown[]).forEach((item, i) => errors.push(...check(schema.items, item, `${path}[${i}]`)))
  } else if (type === 'object' && isObject(value)) {
    const properties: Record<string, Schema> = schema.properties ?? {}
    for (const name of schema.required ?? []) if (!(name in value)) errors.push(`${here}: missing required ${name}`)
    for (const [name, item] of Object.entries(value)) {
      if (properties[name]) errors.push(...check(properties[name], item, `${path}.${name}`))
      else if (schema.additionalProperties === false) errors.push(`${path}.${name}: not in the schema, which forbids extra fields`)
      else if (isObject(schema.additionalProperties)) errors.push(...check(schema.additionalProperties, item, `${path}.${name}`))
    }
  }
  return errors
}

/**
 * What the spec rejects about `value` for the named component schema (empty when it fits).
 * `sessionType` and `nextSeq` are `select: false` on the real collection, so a real query never returns
 * them; the in-memory store has no such rule, so they are dropped here.
 */
function problems(name: string, value: unknown): string[] {
  const { sessionType: _sessionType, nextSeq: _nextSeq, ...row } = JSON.parse(JSON.stringify(value))
  return check({ $ref: `#/components/schemas/${name}` }, row)
}

/** The body as a client receives it: JSON drops a key whose value is undefined. */
const onTheWire = (value: unknown): any => JSON.parse(JSON.stringify(value))

type User = { userId: Types.ObjectId; orgId: Types.ObjectId }
const owner: User = { userId: OWNER, orgId: ORG }
const recipient: User = { userId: RECIPIENT, orgId: ORG }

function request(user: User, params: Record<string, string>, body: Record<string, unknown> = {}, query: Record<string, unknown> = {}): never {
  return {
    headers: { authorization: 'Bearer token' },
    params,
    body,
    query,
    user: { userId: String(user.userId), orgId: String(user.orgId), email: 'someone@example.com' },
    context: { requestId: 'req-spec' },
  } as never
}

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>

async function call(handler: JsonHandler, req: never): Promise<{ status: number | undefined; body: any }> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await handler(req, res as never, next as never)
  await settle()
  if (next.called) throw next.firstCall.args[0]
  return { status: res.statusCode, body: res.jsonBody }
}

function setup() {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()
  // The recipient lookup is one Users query; everyone here is a member of the organisation.
  ;(Users.find as unknown as sinon.SinonStub).callsFake((filter: Record<string, any>) => {
    const rows = ((filter._id?.$in ?? []) as unknown[]).map((id) => ({ _id: new Types.ObjectId(String(id)) }))
    const query = { select: () => query, lean: () => query, exec: async () => rows }
    return query
  })
  const chat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Roadmap review',
    sessionType: 'agent',
    agentKey: AGENT_KEY,
    conversationSource: 'agent_chat',
    isShared: true,
    sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }],
    // MongoDB stamps these on save; the in-memory store does not.
    createdAt: new Date(),
    updatedAt: new Date(),
  })
  store.addMessage(chat, { messageType: 'user_query', content: 'Summarise the roadmap' })
  store.addMessage(chat, { messageType: 'bot_response', content: 'The roadmap has 3 themes.' })
  return { store, chatId: String(chat._id) }
}

describe('agent-chat sharing answers what the spec says', () => {
  beforeEach(() => {
    sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves()
  })
  afterEach(() => sinon.restore())

  const params = (chatId: string) => ({ agentKey: AGENT_KEY, conversationId: chatId })
  const detailQuery = { page: '1', limit: '20' }

  describe('the share routes are documented', () => {
    const share = spec.paths['/agents/{agentKey}/conversations/{conversationId}/share']?.post
    const unshare = spec.paths['/agents/{agentKey}/conversations/{conversationId}/unshare']?.post

    it('as owner-level writes that need the agent:write scope', () => {
      expect(share?.operationId).to.equal('shareAgentConversation')
      expect(unshare?.operationId).to.equal('unshareAgentConversation')
      for (const operation of [share, unshare]) {
        expect(operation.security).to.deep.equal([{ bearerAuth: [] }, { oauth2: ['agent:write'] }])
      }
    })

    it('with a body that takes only read access, at most fifty users and no other field', () => {
      const body = share.requestBody.content['application/json'].schema
      expect(body.additionalProperties).to.equal(false)
      expect(body.properties.accessLevel.enum).to.deep.equal(['read'])
      expect(body.properties.userIds.maxItems).to.equal(50)
      expect(unshare.requestBody.content['application/json'].schema.properties.userIds.maxItems).to.equal(50)
    })
  })

  it('the owner’s by-id answer fits AgentConversationDetail, and includes who it is shared with', async () => {
    const { chatId } = setup()

    const out = await call(controller.getAgentConversationById(appConfig) as JsonHandler, request(owner, params(chatId), {}, detailQuery))

    expect(problems('AgentConversationDetail', out.body.conversation)).to.deep.equal([])
    expect(out.body.conversation.sharedWith).to.have.length(1)
  })

  it('a recipient’s by-id answer fits AgentConversationDetail, with sharedBy and without sharedWith', async () => {
    const { chatId } = setup()

    const out = await call(controller.getAgentConversationById(appConfig) as JsonHandler, request(recipient, params(chatId), {}, detailQuery))

    expect(problems('AgentConversationDetail', out.body.conversation)).to.deep.equal([])
    expect(onTheWire(out.body.conversation)).to.not.have.property('sharedWith')
    expect(onTheWire(out.body.conversation).sharedBy).to.have.property('userId')
  })

  it('every row of the agent list fits AgentConversationListItem, for the owner and for a recipient', async () => {
    setup()

    for (const user of [owner, recipient]) {
      const out = await call(controller.getAllAgentConversations as JsonHandler, request(user, { agentKey: AGENT_KEY }, {}, { limit: '20' }))
      const rows = [...out.body.conversations, ...out.body.sharedWithMeConversations]
      expect(rows.length, `rows for ${user === owner ? 'the owner' : 'the recipient'}`).to.be.greaterThan(0)
      for (const row of rows) {
        expect(problems('AgentConversationListItem', row)).to.deep.equal([])
      }
    }
  })

  it('a shared row in the list names who shared it', async () => {
    setup()

    const out = await call(controller.getAllAgentConversations as JsonHandler, request(recipient, { agentKey: AGENT_KEY }, {}, { limit: '20' }))

    const row = onTheWire(out.body.sharedWithMeConversations[0])
    expect(row.sharedBy).to.have.property('userId')
    expect(row).to.not.have.property('sharedWith')
  })

  it('the share answer fits AgentConversationShareResponse', async () => {
    const { chatId } = setup()

    const out = await call(
      controller.shareAgentConversation(appConfig) as JsonHandler,
      request(owner, params(chatId), { userIds: [String(RECIPIENT)] }),
    )

    expect(problems('AgentConversationShareResponse', out.body)).to.deep.equal([])
  })

  it('the unshare answer fits AgentConversationShareResponse, with the users it removed', async () => {
    const { chatId } = setup()

    const out = await call(
      controller.unshareAgentConversation(appConfig) as JsonHandler,
      request(owner, params(chatId), { userIds: [String(RECIPIENT)] }),
    )

    expect(problems('AgentConversationShareResponse', out.body)).to.deep.equal([])
    expect(out.body.unsharedUsers).to.deep.equal([String(RECIPIENT)])
  })

  it('the global shared list fits ConversationListItem, with agentKey on an agent chat', async () => {
    setup()

    const out = await call(controller.getAllConversations as JsonHandler, request(recipient, {}, {}, { source: 'shared', limit: '20' }))

    expect(out.body.conversations.length).to.be.greaterThan(0)
    for (const row of out.body.conversations) {
      expect(problems('ConversationListItem', row)).to.deep.equal([])
    }
    expect(out.body.conversations[0].agentKey).to.equal(AGENT_KEY)
  })
})
