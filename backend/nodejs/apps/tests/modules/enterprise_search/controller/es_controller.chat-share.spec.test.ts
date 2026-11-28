import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import { Types } from 'mongoose'
import yaml from 'js-yaml'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { behindGuard, realGuards } from '../helpers/guarded-chat'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

/**
 * Sharing a chat, as the published spec says: a `write` share is stored as `write`, so the answer never carries a
 * warning that the level was lowered, and the spec lists no such field. Only the person who started the chat shares it.
 */

const SPEC = join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')
const SPEC_TEXT = readFileSync(SPEC, 'utf8')
type Schema = { properties: Record<string, unknown>; required?: string[]; additionalProperties?: boolean }
const spec = yaml.load(SPEC_TEXT) as { paths: Record<string, Record<string, any>> }
const share = spec.paths['/conversations/{conversationId}/share']!.post
const answer = share.responses['200'].content['application/json'].schema as Schema

const appConfig = {
  aiBackend: 'http://ai.test',
  iamBackend: 'http://iam.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const ORG = oid()
const OWNER = oid()
const RECIPIENT = oid()

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>

async function shareAsOwner(chatId: string, body: Record<string, unknown>): Promise<Record<string, any>> {
  const handler = behindGuard(realGuards(), 'manageSharing', controller.shareConversationById(appConfig) as JsonHandler)
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = {
    headers: { authorization: 'Bearer token' },
    params: { conversationId: chatId },
    body,
    query: {},
    user: { userId: String(OWNER), orgId: String(ORG), email: 'someone@example.com' },
    context: { requestId: 'req-share' },
  }
  await handler(req as never, res as never, next as never)
  await settle()
  if (next.called) throw next.firstCall.args[0]
  return JSON.parse(JSON.stringify(res.jsonBody)) as Record<string, any>
}

describe('sharing a chat answers what the spec says', () => {
  beforeEach(() => registerAppUpstreams(appConfig as never))
  afterEach(() => {
    registerUpstreams({})
    sinon.restore()
  })

  function setup(): string {
    const store = new InMemoryChatStore()
    store.install()
    new FakeAIBackend().install()
    // The recipient lookup is one Users query; everyone here is a member of the organisation.
    ;(Users.find as unknown as sinon.SinonStub).callsFake((filter: Record<string, any>) => {
      const rows = ((filter._id?.$in ?? []) as unknown[]).map((id) => ({ _id: new Types.ObjectId(String(id)) }))
      const query = { select: () => query, lean: () => query, exec: async () => rows }
      return query
    })
    // Shared at read already: raising it is no news, so no notice is written.
    const chat = store.addSession({
      orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Roadmap review',
      isShared: true, sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }],
    })
    return String(chat._id)
  }

  it('a write share, over a read one, is stored as write, with no warning, in the fields the spec lists', async () => {
    const out = await shareAsOwner(setup(), { userIds: [String(RECIPIENT)], accessLevel: 'write' })

    expect(out.sharedWith.map((row: { userId: string; accessLevel: string }) => [row.userId, row.accessLevel])).to.deep.equal([
      [String(RECIPIENT), 'write'],
    ])
    expect(out).to.not.have.property('warnings')
    expect(answer.additionalProperties).to.equal(false)
    expect(Object.keys(out).filter((key) => !(key in answer.properties))).to.deep.equal([])
    expect((answer.required ?? []).filter((key) => !(key in out))).to.deep.equal([])
  })

  it('documents no lowered-level warning, and that only the person who started the chat may share it', () => {
    expect(answer.properties).to.not.have.property('warnings')
    expect(SPEC_TEXT).to.not.include('ACCESS_LEVEL_DOWNGRADED')
    expect(share.description).to.include("Only the conversation's initiator can share.")
  })
})
