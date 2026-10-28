import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import * as conversationArtifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * Sharing an agent conversation is read-only: the owner shares, a recipient can
 * open the transcript and nothing else. These tests run the real controllers
 * against an in-memory store that evaluates the real query filters.
 */

const appConfig = {
  aiBackend: 'http://ai.test',
  iamBackend: 'http://iam.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const ORG = oid()
const OTHER_ORG = oid()
const OWNER = oid()
const RECIPIENT = oid()
const SECOND_RECIPIENT = oid()
const STRANGER = oid()
const OUTSIDER = oid()
const AGENT_KEY = 'agent-1'
const OTHER_AGENT_KEY = 'agent-2'

type User = { userId: Types.ObjectId; orgId: Types.ObjectId }
const owner: User = { userId: OWNER, orgId: ORG }
const recipient: User = { userId: RECIPIENT, orgId: ORG }
const stranger: User = { userId: STRANGER, orgId: ORG }
const outsider: User = { userId: OUTSIDER, orgId: OTHER_ORG }

interface Setup {
  store: InMemoryChatStore
  ai: FakeAIBackend
  agentChatId: string
  agentBotMessageId: string
  plainChatId: string
}

/** One agent chat and one plain chat, both owned by OWNER and not shared with anyone. */
let hookOrgUsers: () => void = () => undefined

function setup(): Setup {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  store.stubNotifications()
  ai.install()
  hookOrgUsers()

  const agentChat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Roadmap review',
    sessionType: 'agent',
    agentKey: AGENT_KEY,
    conversationSource: 'agent_chat',
  })
  store.addMessage(agentChat, { messageType: 'user_query', content: 'Summarise the roadmap' })
  const bot = store.addMessage(agentChat, { messageType: 'bot_response', content: 'The roadmap has 3 themes.' })

  const plainChat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Plain chat',
    sessionType: 'chat',
  })
  store.addMessage(plainChat, { messageType: 'user_query', content: 'Hello' })

  store.writes.length = 0
  return {
    store,
    ai,
    agentChatId: String(agentChat._id),
    agentBotMessageId: String(bot._id),
    plainChatId: String(plainChat._id),
  }
}

const shareWith = (s: Setup, ...users: Types.ObjectId[]): void => {
  s.store.session(s.agentChatId)?.set({
    isShared: true,
    sharedWith: users.map((userId) => ({ userId, accessLevel: 'read' })),
  })
  s.store.writes.length = 0
}

function request(user: User, params: Record<string, string>, body: Record<string, unknown> = {}, query: Record<string, unknown> = {}): never {
  return {
    headers: { authorization: 'Bearer token' },
    params,
    body,
    query,
    // The JWT carries ids as strings; ObjectIds here would hide string-vs-ObjectId comparison bugs.
    user: { userId: String(user.userId), orgId: String(user.orgId), email: 'someone@example.com' },
    context: { requestId: 'req-agent-share' },
  } as never
}

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
type StreamHandler = (req: never, res: never) => Promise<unknown>

interface JsonOutcome {
  status: number | undefined
  body: unknown
  error: (Error & { statusCode?: number }) | undefined
}

async function callJson(handler: JsonHandler, req: never): Promise<JsonOutcome> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await handler(req, res as never, next as never)
  await settle()
  return { status: res.statusCode, body: res.jsonBody, error: next.firstCall?.args[0] as JsonOutcome['error'] }
}

/** A refusal sent to `next` before any stream opened, if there was one. */
const refusals = new WeakMap<FakeSSEResponse, Error>()

async function callStream(handler: StreamHandler, req: never): Promise<FakeSSEResponse> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await (handler as (...args: unknown[]) => Promise<void>)(req, res, next)
  await settle()
  if (next.called) refusals.set(res, next.firstCall.args[0] as Error)
  return res
}

/** The "not found" a caller gets: a 404 before streaming for a new message, a RUN_ERROR for a regenerate. */
function notFoundMessage(res: FakeSSEResponse): string | undefined {
  const refusal = refusals.get(res)
  if (refusal) {
    expect(res.eventsOf('RUN_ERROR'), 'no stream after a refusal').to.have.length(0)
    return refusal.message
  }
  const errors = res.eventsOf('RUN_ERROR')
  expect(errors, 'RUN_ERROR frames').to.have.length(1)
  return errors[0]?.data.message as string | undefined
}

const agentParams = (s: Setup, extra: Record<string, string> = {}): Record<string, string> => ({
  conversationId: s.agentChatId,
  agentKey: AGENT_KEY,
  ...extra,
})

const sharedWithOf = (s: Setup): string[] =>
  ((s.store.session(s.agentChatId)?.sharedWith ?? []) as Array<{ userId: Types.ObjectId }>).map((e) => String(e.userId))

const share = (userIds: unknown, accessLevel?: string) => ({ userIds, ...(accessLevel ? { accessLevel } : {}) })

describe('es_controller agent-chat sharing: read-only for recipients, owner-only to change', () => {
  // The users of the caller's organisation, and every filter the recipient lookup was asked with.
  let orgUsers: Set<string>
  let userLookups: Array<Record<string, any>>

  beforeEach(() => {
    sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves()
    orgUsers = new Set([OWNER, RECIPIENT, SECOND_RECIPIENT, STRANGER].map(String))
    userLookups = []
  })

  // Installed after setup(), which installs the store's own Users.find; this one answers from orgUsers.
  hookOrgUsers = (): void => {
    ;(Users.find as unknown as sinon.SinonStub).callsFake((filter: Record<string, any>) => {
      userLookups.push(filter)
      const asked: string[] = ((filter._id?.$in ?? []) as unknown[]).map(String)
      const rows = asked.filter((id) => orgUsers.has(id)).map((id) => ({ _id: new Types.ObjectId(id) }))
      const query = { select: () => query, lean: () => query, exec: async () => rows }
      return query
    })
  }

  afterEach(() => {
    sinon.restore()
  })

  const shareHandler = () => controller.shareAgentConversation(appConfig) as JsonHandler
  const unshareHandler = () => controller.unshareAgentConversation(appConfig) as JsonHandler
  const getHandler = () => controller.getAgentConversationById(appConfig) as JsonHandler

  describe('sharing', () => {
    it('the owner shares with a colleague: it is stored as read access and the response says so', async () => {
      const s = setup()

      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT)])))

      expect(out.status).to.equal(200)
      const body = out.body as { isShared: boolean; sharedWith: Array<{ userId: unknown; accessLevel: string }> }
      expect(body.isShared).to.equal(true)
      expect(body.sharedWith.map((e) => [String(e.userId), e.accessLevel])).to.deep.equal([[String(RECIPIENT), 'read']])
      expect(String(s.store.session(s.agentChatId)?.isShared)).to.equal('true')
      expect(sharedWithOf(s)).to.deep.equal([String(RECIPIENT)])
    })

    it('looks every recipient up in one query, scoped to the caller’s organisation, with no call to another service', async () => {
      const s = setup()

      await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT), String(SECOND_RECIPIENT)])))

      expect(userLookups).to.have.length(1)
      expect(String(userLookups[0]?.orgId)).to.equal(String(ORG))
      expect(userLookups[0]?.isDeleted).to.equal(false)
      expect((userLookups[0]?._id.$in as unknown[]).map(String)).to.have.members([String(RECIPIENT), String(SECOND_RECIPIENT)])
      expect(s.ai.calls.some((c) => new URL(c.url).hostname === 'iam.test')).to.equal(false)
    })


    it('grants the recipient the conversation’s artifacts through the AI service, as the owner', async () => {
      const s = setup()

      await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT)])))

      const grant = s.ai.calls.find((c) => /\/chat\/artifacts\/permissions$/.test(c.url))
      expect(grant, 'artifact grant call').to.not.equal(undefined)
      expect(grant?.method).to.equal('POST')
      expect(grant?.body).to.deep.equal({ conversationId: s.agentChatId, userIds: [String(RECIPIENT)] })
    })

    it('also grants and later revokes the chat attachments the owner uploaded', async () => {
      const s = setup()
      const chat = s.store.session(s.agentChatId)
      if (chat) s.store.addMessage(chat, { messageType: 'user_query', content: 'See file', attachments: [{ recordId: 'rec-1' }] })
      s.store.writes.length = 0

      await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT)])))
      await callJson(unshareHandler(), request(owner, agentParams(s), { userIds: [String(RECIPIENT)] }))

      const calls = s.ai.calls.filter((c) => /\/chat\/attachments\/permissions$/.test(c.url))
      expect(calls.map((c) => c.method)).to.deep.equal(['POST', 'DELETE'])
      for (const c of calls) expect(c.body).to.deep.equal({ userIds: [String(RECIPIENT)], recordIds: ['rec-1'] })
    })

    it('when the owner deletes a shared conversation its artifacts are removed and the recipient gets 404', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const deleted = await callJson(controller.deleteAgentConversationById(appConfig) as JsonHandler, request(owner, agentParams(s)))
      const out = await callJson(getHandler(), request(recipient, agentParams(s)))
      const list = await callJson(controller.getAllAgentConversations as JsonHandler, request(recipient, { agentKey: AGENT_KEY }))

      expect(deleted.status).to.equal(200)
      expect((conversationArtifacts.deleteConversationArtifacts as sinon.SinonStub).calledOnce).to.equal(true)
      expect(out.error?.statusCode).to.equal(404)
      expect((list.body as { sharedWithMeConversations: unknown[] }).sharedWithMeConversations).to.deep.equal([])
    })

    it('sharing twice with the same person, or listing them twice, stores them once', async () => {
      const s = setup()

      await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT), String(RECIPIENT)])))
      await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT)])))

      expect(sharedWithOf(s)).to.deep.equal([String(RECIPIENT)])
    })

    it('an id written in capitals is the same user: sharing with yourself that way is still refused', async () => {
      const s = setup()

      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(OWNER).toUpperCase()])))

      expect(out.error?.statusCode).to.equal(400)
      expect(sharedWithOf(s)).to.deep.equal([])
    })

    it('an id written in capitals is the same user: it does not add a second entry for a recipient', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT).toUpperCase(), String(RECIPIENT)])))

      expect(out.status).to.equal(200)
      expect(sharedWithOf(s)).to.deep.equal([String(RECIPIENT)])
    })

    it('sharing with a second person keeps the first', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(SECOND_RECIPIENT)])))

      expect(out.status).to.equal(200)
      expect(sharedWithOf(s)).to.have.members([String(RECIPIENT), String(SECOND_RECIPIENT)])
    })

    for (const [who, user] of [
      ['another user in the org', stranger],
      ['a user from another organisation', outsider],
      ['a recipient it was already shared with (no re-sharing)', recipient],
    ] as const) {
      it(`${who} cannot share it: 404 and nothing changes`, async () => {
        const s = setup()
        shareWith(s, RECIPIENT)
        const before = JSON.stringify(s.store.session(s.agentChatId)?.toObject())

        const out = await callJson(shareHandler(), request(user, agentParams(s), share([String(SECOND_RECIPIENT)])))

        expect(out.error?.statusCode).to.equal(404)
        expect(s.store.writes).to.deep.equal([])
        expect(JSON.stringify(s.store.session(s.agentChatId)?.toObject())).to.equal(before)
        expect(s.ai.calls, 'no permission grants').to.deep.equal([])
      })
    }

    it('write access is refused: a read-only share cannot be widened', async () => {
      const s = setup()

      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT)], 'write')))

      expect(out.error?.statusCode).to.equal(400)
      expect(out.error?.message).to.match(/read/i)
      expect(s.store.writes).to.deep.equal([])
      expect(sharedWithOf(s)).to.deep.equal([])
    })

    it('an explicit read access level is accepted', async () => {
      const s = setup()
      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT)], 'read')))
      expect(out.status).to.equal(200)
    })

    it('sharing with yourself is refused', async () => {
      const s = setup()

      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(OWNER)])))

      expect(out.error?.statusCode).to.equal(400)
      expect(s.store.writes).to.deep.equal([])
      expect(sharedWithOf(s)).to.deep.equal([])
    })

    it('a user who is not an active member of the organisation (other org, deleted) is refused and nothing is stored', async () => {
      const s = setup()
      orgUsers.delete(String(RECIPIENT))

      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT)])))

      expect(out.error?.statusCode).to.equal(400)
      expect(s.store.writes).to.deep.equal([])
      expect(sharedWithOf(s)).to.deep.equal([])
      expect(s.ai.calls).to.deep.equal([])
    })

    it('one unknown user in a batch refuses the whole batch', async () => {
      const s = setup()
      orgUsers.delete(String(SECOND_RECIPIENT))

      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT), String(SECOND_RECIPIENT)])))

      expect(out.error?.statusCode).to.equal(400)
      expect(sharedWithOf(s)).to.deep.equal([])
    })

    it('a malformed user id is refused', async () => {
      const s = setup()
      const out = await callJson(shareHandler(), request(owner, agentParams(s), share(['not-an-id'])))
      expect(out.error?.statusCode).to.equal(400)
      expect(sharedWithOf(s)).to.deep.equal([])
    })

    it('an empty or missing user list is refused', async () => {
      const s = setup()
      for (const body of [share([]), {}, share('abc')]) {
        const out = await callJson(shareHandler(), request(owner, agentParams(s), body))
        expect(out.error?.statusCode, JSON.stringify(body)).to.equal(400)
      }
      expect(s.store.writes).to.deep.equal([])
    })

    it('the agent in the URL must be the conversation’s agent', async () => {
      const s = setup()
      const out = await callJson(shareHandler(), request(owner, agentParams(s, { agentKey: OTHER_AGENT_KEY }), share([String(RECIPIENT)])))
      expect(out.error?.statusCode).to.equal(404)
      expect(s.store.writes).to.deep.equal([])
    })

    it('a deleted conversation cannot be shared', async () => {
      const s = setup()
      s.store.session(s.agentChatId)?.set({ isDeleted: true })
      const out = await callJson(shareHandler(), request(owner, agentParams(s), share([String(RECIPIENT)])))
      expect(out.error?.statusCode).to.equal(404)
    })

    it('the agent share route cannot share a plain chat, and the plain route cannot share an agent chat', async () => {
      const s = setup()

      const plainViaAgent = await callJson(
        shareHandler(),
        request(owner, { conversationId: s.plainChatId, agentKey: AGENT_KEY }, share([String(RECIPIENT)])),
      )
      const agentViaPlain = await callJson(
        controller.shareConversationById(appConfig) as JsonHandler,
        request(owner, { conversationId: s.agentChatId }, share([String(RECIPIENT)])),
      )

      expect(plainViaAgent.error?.statusCode).to.equal(404)
      expect(agentViaPlain.error?.statusCode).to.equal(404)
      expect(s.store.writes).to.deep.equal([])
    })
  })

  describe('unsharing', () => {
    it('the owner removes one recipient and the other keeps access', async () => {
      const s = setup()
      shareWith(s, RECIPIENT, SECOND_RECIPIENT)

      const out = await callJson(unshareHandler(), request(owner, agentParams(s), { userIds: [String(RECIPIENT)] }))

      expect(out.status).to.equal(200)
      expect(sharedWithOf(s)).to.deep.equal([String(SECOND_RECIPIENT)])
      expect(s.store.session(s.agentChatId)?.isShared).to.equal(true)
    })

    it('removing the last recipient turns sharing off and revokes the artifact grant', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await callJson(unshareHandler(), request(owner, agentParams(s), { userIds: [String(RECIPIENT)] }))

      expect(out.status).to.equal(200)
      expect(s.store.session(s.agentChatId)?.isShared).to.equal(false)
      expect(sharedWithOf(s)).to.deep.equal([])
      const revoke = s.ai.calls.find((c) => /\/chat\/artifacts\/permissions$/.test(c.url))
      expect(revoke?.method).to.equal('DELETE')
      expect(revoke?.body).to.deep.equal({ conversationId: s.agentChatId, userIds: [String(RECIPIENT)] })
    })

    it('after unsharing, the former recipient can no longer open the conversation', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      await callJson(unshareHandler(), request(owner, agentParams(s), { userIds: [String(RECIPIENT)] }))

      const out = await callJson(getHandler(), request(recipient, agentParams(s)))

      expect(out.error?.statusCode).to.equal(404)
    })

    it('an id written in capitals still removes that recipient, and the revoke names them in one form', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await callJson(unshareHandler(), request(owner, agentParams(s), { userIds: [String(RECIPIENT).toUpperCase()] }))

      expect(out.status).to.equal(200)
      expect(sharedWithOf(s)).to.deep.equal([])
      expect(s.store.session(s.agentChatId)?.isShared).to.equal(false)
      const revoke = s.ai.calls.find((c) => /\/chat\/artifacts\/permissions$/.test(c.url))
      expect(revoke?.body).to.deep.equal({ conversationId: s.agentChatId, userIds: [String(RECIPIENT)] })
    })

    it('unsharing someone who was never a recipient changes nothing', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await callJson(unshareHandler(), request(owner, agentParams(s), { userIds: [String(STRANGER)] }))

      expect(out.status).to.equal(200)
      expect(sharedWithOf(s)).to.deep.equal([String(RECIPIENT)])
    })

    for (const [who, user] of [
      ['another user in the org', stranger],
      ['a user from another organisation', outsider],
      ['a recipient (cannot remove themselves or others)', recipient],
    ] as const) {
      it(`${who} cannot unshare: 404 and nothing changes`, async () => {
        const s = setup()
        shareWith(s, RECIPIENT, SECOND_RECIPIENT)

        const out = await callJson(unshareHandler(), request(user, agentParams(s), { userIds: [String(SECOND_RECIPIENT)] }))

        expect(out.error?.statusCode).to.equal(404)
        expect(s.store.writes).to.deep.equal([])
        expect(sharedWithOf(s)).to.have.members([String(RECIPIENT), String(SECOND_RECIPIENT)])
        expect(s.ai.calls).to.deep.equal([])
      })
    }

    it('a malformed user id is refused', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      const out = await callJson(unshareHandler(), request(owner, agentParams(s), { userIds: ['nope'] }))
      expect(out.error?.statusCode).to.equal(400)
      expect(sharedWithOf(s)).to.deep.equal([String(RECIPIENT)])
    })

    it('the agent unshare route cannot touch a plain chat', async () => {
      const s = setup()
      s.store.session(s.plainChatId)?.set({ isShared: true, sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }] })
      s.store.writes.length = 0

      const out = await callJson(
        unshareHandler(),
        request(owner, { conversationId: s.plainChatId, agentKey: AGENT_KEY }, { userIds: [String(RECIPIENT)] }),
      )

      expect(out.error?.statusCode).to.equal(404)
      expect(s.store.writes).to.deep.equal([])
    })
  })

  describe('reading a shared conversation', () => {
    it('a recipient opens the transcript; the owner’s identity is shown and the recipient list is not', async () => {
      const s = setup()
      shareWith(s, RECIPIENT, SECOND_RECIPIENT)

      const out = await callJson(getHandler(), request(recipient, agentParams(s)))

      expect(out.status).to.equal(200)
      const conversation = (out.body as { conversation: Record<string, unknown> }).conversation
      expect(conversation.title).to.equal('Roadmap review')
      expect(conversation.messages).to.have.length(2)
      expect(conversation.access).to.deep.equal({ isOwner: false, accessLevel: 'read' })
      expect(conversation.sharedWith, 'other recipients stay private').to.equal(undefined)
      expect((conversation.sharedBy as { userId: string }).userId).to.equal(String(OWNER))
    })

    it('opening it writes nothing', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      await callJson(getHandler(), request(recipient, agentParams(s)))
      expect(s.store.writes).to.deep.equal([])
    })

    it('catches the recipient up on artifacts created after the share, granting as the owner', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      await callJson(getHandler(), request(recipient, agentParams(s)))

      const grant = s.ai.calls.find((c) => /\/chat\/artifacts\/permissions$/.test(c.url))
      expect(grant?.body).to.deep.equal({ conversationId: s.agentChatId, userIds: [String(RECIPIENT)] })
      expect(grant?.headers.authorization).to.match(/^Bearer /)
    })

    it('the owner still sees their own list of recipients and no AI-service grant is made', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await callJson(getHandler(), request(owner, agentParams(s)))

      const conversation = (out.body as { conversation: Record<string, unknown> }).conversation
      expect(conversation.access).to.deep.equal({ isOwner: true, accessLevel: 'read' })
      expect((conversation.sharedWith as unknown[]).length).to.equal(1)
      expect(conversation.sharedBy).to.equal(undefined)
      expect(s.ai.calls).to.deep.equal([])
    })

    it('someone it was not shared with gets 404, even when the conversation is shared with others', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      const out = await callJson(getHandler(), request(stranger, agentParams(s)))
      expect(out.error?.statusCode).to.equal(404)
    })

    it('isShared alone grants nothing: the user must be on the recipient list', async () => {
      const s = setup()
      s.store.session(s.agentChatId)?.set({ isShared: true, sharedWith: [] })
      const out = await callJson(getHandler(), request(stranger, agentParams(s)))
      expect(out.error?.statusCode).to.equal(404)
    })

    it('a recipient entry without isShared grants nothing (a stale or half-written share)', async () => {
      const s = setup()
      s.store.session(s.agentChatId)?.set({ isShared: false, sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }] })
      const out = await callJson(getHandler(), request(recipient, agentParams(s)))
      expect(out.error?.statusCode).to.equal(404)
    })

    it('a recipient id from another organisation grants nothing', async () => {
      const s = setup()
      s.store.session(s.agentChatId)?.set({ isShared: true, sharedWith: [{ userId: OUTSIDER, accessLevel: 'read' }] })
      const out = await callJson(getHandler(), request(outsider, agentParams(s)))
      expect(out.error?.statusCode).to.equal(404)
    })

    it('the URL’s agent must match: a recipient cannot reach it through another agent’s path', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      const out = await callJson(getHandler(), request(recipient, agentParams(s, { agentKey: OTHER_AGENT_KEY })))
      expect(out.error?.statusCode).to.equal(404)
    })

    it('once the owner deletes the conversation the recipient gets 404', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      s.store.session(s.agentChatId)?.set({ isDeleted: true })
      const out = await callJson(getHandler(), request(recipient, agentParams(s)))
      expect(out.error?.statusCode).to.equal(404)
    })

    it('the plain-chat read route does not expose an agent chat shared with the recipient', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      const out = await callJson(
        controller.getConversationById(appConfig) as JsonHandler,
        request(recipient, { conversationId: s.agentChatId }),
      )
      expect(out.error?.statusCode).to.equal(404)
    })
  })

  describe('listing', () => {
    const list = (user: User) =>
      callJson(controller.getAllAgentConversations as JsonHandler, request(user, { agentKey: AGENT_KEY }))

    it('a shared conversation shows up for the recipient under "shared with me", not in their own list', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await list(recipient)

      const body = out.body as {
        conversations: Array<{ _id: unknown }>
        sharedWithMeConversations: Array<Record<string, unknown>>
      }
      expect(body.conversations).to.deep.equal([])
      expect(body.sharedWithMeConversations.map((c) => String(c._id))).to.deep.equal([s.agentChatId])
      const row = body.sharedWithMeConversations[0] as Record<string, unknown>
      expect(row.isOwner).to.equal(false)
      expect(row.sharedWith, 'recipient list is not leaked in the list').to.equal(undefined)
      expect((row.sharedBy as { userId: string }).userId).to.equal(String(OWNER))
    })

    it('the owner sees it in their own list and not under "shared with me"', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await list(owner)

      const body = out.body as { conversations: Array<{ _id: unknown }>; sharedWithMeConversations: unknown[] }
      expect(body.conversations.map((c) => String(c._id))).to.deep.equal([s.agentChatId])
      expect(body.sharedWithMeConversations).to.deep.equal([])
    })

    it('another user sees nothing', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      const out = await list(stranger)
      const body = out.body as { conversations: unknown[]; sharedWithMeConversations: unknown[] }
      expect(body.conversations).to.deep.equal([])
      expect(body.sharedWithMeConversations).to.deep.equal([])
    })

    it('a project member who sees the chat through its project is not told who it was shared with', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      const projectId = oid()
      s.store.session(s.agentChatId)?.set({ projectId, projectVisibility: 'project' })
      ;(ProjectService.getAccessibleProjectIds as sinon.SinonStub).resolves([projectId])

      const asMember = await list(stranger)
      const asOwner = await list(owner)

      const memberRows = (asMember.body as { conversations: Array<Record<string, unknown>> }).conversations
      const ownerRows = (asOwner.body as { conversations: Array<Record<string, unknown>> }).conversations
      expect(memberRows.map((c) => String(c._id))).to.deep.equal([s.agentChatId])
      expect(memberRows[0]?.sharedWith).to.equal(undefined)
      expect((ownerRows[0]?.sharedWith as unknown[]).length).to.equal(1)
    })

    it('a conversation shared under one agent is not listed under another agent', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      const out = await callJson(controller.getAllAgentConversations as JsonHandler, request(recipient, { agentKey: OTHER_AGENT_KEY }))
      expect((out.body as { sharedWithMeConversations: unknown[] }).sharedWithMeConversations).to.deep.equal([])
    })

    it('an unshared conversation disappears from the recipient’s list', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      await callJson(unshareHandler(), request(owner, agentParams(s), { userIds: [String(RECIPIENT)] }))
      const out = await list(recipient)
      expect((out.body as { sharedWithMeConversations: unknown[] }).sharedWithMeConversations).to.deep.equal([])
    })

    it('the owner’s archived list does not include a conversation merely shared with them', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      s.store.session(s.agentChatId)?.set({ isArchived: true, archivedBy: OWNER })
      const out = await callJson(controller.listAllArchivesAgentConversation() as JsonHandler, request(recipient, { agentKey: AGENT_KEY }))
      expect((out.body as { conversations: unknown[] }).conversations).to.deep.equal([])
    })
  })

  describe('the global "shared with me" list', () => {
    const listConversations = (user: User, source: 'owned' | 'shared') =>
      callJson(controller.getAllConversations as JsonHandler, request(user, {}, {}, { source }))

    const idsOf = (out: JsonOutcome) => (out.body as { conversations: Array<{ _id: unknown }> }).conversations.map((c) => String(c._id))

    it('includes an agent chat shared with the recipient, with the agent key needed to open it', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await listConversations(recipient, 'shared')

      expect(idsOf(out)).to.deep.equal([s.agentChatId])
      const row = (out.body as { conversations: Array<Record<string, unknown>> }).conversations[0] as Record<string, unknown>
      expect(row.agentKey).to.equal(AGENT_KEY)
      expect(row.isOwner).to.equal(false)
      expect((row.sharedBy as { userId: string }).userId).to.equal(String(OWNER))
    })

    it('does not list an agent chat nobody shared with the user', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      expect(idsOf(await listConversations(stranger, 'shared'))).to.deep.equal([])
      expect(idsOf(await listConversations(outsider, 'shared'))).to.deep.equal([])
      expect(s.agentChatId).to.not.equal('')
    })

    it('does not list an agent chat once it is unshared or deleted', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      s.store.session(s.agentChatId)?.set({ isDeleted: true })
      expect(idsOf(await listConversations(recipient, 'shared'))).to.deep.equal([])

      s.store.session(s.agentChatId)?.set({ isDeleted: false, isShared: false })
      expect(idsOf(await listConversations(recipient, 'shared'))).to.deep.equal([])
    })

    it('keeps agent chats out of the owner’s own plain-chat list', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      expect(idsOf(await listConversations(owner, 'owned'))).to.deep.equal([s.plainChatId])
    })

    it('still lists a plain chat shared with the recipient next to the agent chat', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)
      s.store.session(s.plainChatId)?.set({ isShared: true, sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }] })
      expect(idsOf(await listConversations(recipient, 'shared'))).to.have.members([s.agentChatId, s.plainChatId])
    })
  })

  describe('a recipient can never change the conversation', () => {
    interface JsonCase {
      name: string
      handler: () => JsonHandler
      body?: Record<string, unknown>
    }
    const jsonCases: JsonCase[] = [
      { name: 'updateAgentConversationTitle', handler: () => controller.updateAgentConversationTitle as JsonHandler, body: { title: 'Hijacked' } },
      { name: 'updateAgentFeedback', handler: () => controller.updateAgentFeedback as JsonHandler, body: { isHelpful: false } },
      { name: 'archiveAgentConversation', handler: () => controller.archiveAgentConversation as JsonHandler },
      { name: 'unarchiveAgentConversation', handler: () => controller.unarchiveAgentConversation as JsonHandler },
      { name: 'cancelAgentConversationStream', handler: () => controller.cancelAgentConversationStream(appConfig) as JsonHandler, body: { runId: 'run-1' } },
      { name: 'forkAgentConversation', handler: () => controller.forkAgentConversation(appConfig) as JsonHandler },
      { name: 'setConversationProject', handler: () => controller.setConversationProject as JsonHandler, body: { projectId: null } },
      { name: 'setConversationProjectVisibility', handler: () => controller.setConversationProjectVisibility as JsonHandler, body: { visibility: 'project' } },
    ]

    for (const c of jsonCases) {
      it(`${c.name}: 404, nothing written, nothing sent to the AI service`, async () => {
        const s = setup()
        shareWith(s, RECIPIENT)
        const before = JSON.stringify(s.store.sessions.map((d) => d.toObject()))

        const out = await callJson(c.handler(), request(recipient, agentParams(s, { messageId: s.agentBotMessageId }), c.body))

        expect(out.error?.statusCode).to.equal(404)
        expect(s.store.writes).to.deep.equal([])
        expect(JSON.stringify(s.store.sessions.map((d) => d.toObject()))).to.equal(before)
        expect(s.ai.calls).to.deep.equal([])
      })
    }

    it('deleteAgentConversationById: the idempotent 200 deletes nothing and no artifact is removed', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const out = await callJson(controller.deleteAgentConversationById(appConfig) as JsonHandler, request(recipient, agentParams(s)))

      expect(out.status).to.equal(200)
      expect((out.body as { conversation: unknown }).conversation).to.equal(null)
      expect(s.store.session(s.agentChatId)?.isDeleted).to.equal(false)
      expect(s.store.writes).to.deep.equal([])
      expect((conversationArtifacts.deleteConversationArtifacts as sinon.SinonStub).called).to.equal(false)
    })

    it('regenerating is refused for a member the chat is shared with, even at write level', async () => {
      const s = setup()
      s.store.session(s.agentChatId)?.set({
        isShared: true,
        sharedWith: [{ userId: RECIPIENT, accessLevel: 'write' }],
      })
      s.store.writes.length = 0

      const res = await callStream(
        controller.regenerateAgentAnswers(appConfig) as StreamHandler,
        request(recipient, agentParams(s, { messageId: s.agentBotMessageId }), {}),
      )

      expect(res.eventsOf('RUN_ERROR')).to.have.length(1)
      expect(s.ai.streamCalls).to.deep.equal([])
    })

    it('regenerating is refused when the caller owns the row but did not initiate the chat, even if it is shared', async () => {
      const s = setup()
      // isShared is what the old filter let through; only the initiator may regenerate.
      s.store.session(s.agentChatId)?.set({ initiator: STRANGER, isShared: true })
      s.store.writes.length = 0

      const res = await callStream(
        controller.regenerateAgentAnswers(appConfig) as StreamHandler,
        request(owner, agentParams(s, { messageId: s.agentBotMessageId }), {}),
      )

      expect(res.eventsOf('RUN_ERROR')).to.have.length(1)
      expect(s.ai.streamCalls).to.deep.equal([])
    })

    const streamCases: Array<{ name: string; handler: () => StreamHandler; body: Record<string, unknown> }> = [
      { name: 'addMessageStreamToAgentConversation', handler: () => controller.addMessageStreamToAgentConversation(appConfig) as StreamHandler, body: { query: 'More detail' } },
      { name: 'regenerateAgentAnswers', handler: () => controller.regenerateAgentAnswers(appConfig) as StreamHandler, body: {} },
    ]
    for (const c of streamCases) {
      it(`${c.name}: the recipient is told the conversation was not found; nothing is generated or saved`, async () => {
        const s = setup()
        shareWith(s, RECIPIENT)

        const res = await callStream(c.handler(), request(recipient, agentParams(s, { messageId: s.agentBotMessageId }), c.body))

        expect(notFoundMessage(res)).to.match(/not found/i)
        expect(s.ai.streamCalls).to.deep.equal([])
        expect(s.store.writes.filter((w) => w.startsWith('message.'))).to.deep.equal([])
        expect(s.store.messagesOf(s.agentChatId)).to.have.length(2)
      })
    }

    it('the owner can still continue the conversation after sharing it', async () => {
      const s = setup()
      shareWith(s, RECIPIENT)

      const res = await callStream(
        controller.addMessageStreamToAgentConversation(appConfig) as StreamHandler,
        request(owner, agentParams(s), { query: 'More detail' }),
      )

      expect(res.eventsOf('RUN_ERROR')).to.have.length(0)
      expect(s.ai.streamCalls).to.have.length(1)
    })
  })
})
