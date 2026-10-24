import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

const ORG = oid()
const OTHER_ORG = oid()
const OWNER = oid()
const OTHER = oid()
const RECIPIENT = oid()

type User = { userId: Types.ObjectId; orgId: Types.ObjectId }
const owner: User = { userId: OWNER, orgId: ORG }

type Handler = (req: never, res: never, next: never) => Promise<unknown>

interface Outcome<T> {
  status: number | undefined
  body: T
  error: (Error & { statusCode?: number }) | undefined
}

async function call<T = Record<string, unknown>>(
  handler: Handler,
  { params = {}, query = {}, user = owner }: { params?: Record<string, string>; query?: Record<string, string>; user?: User | null } = {},
): Promise<Outcome<T>> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = { headers: { authorization: 'Bearer token' }, params, query, body: {}, user: user ?? undefined, context: { requestId: 'req-pin' } }
  await handler(req as never, res as never, next as never)
  await settle()
  return { status: res.statusCode, body: res.jsonBody as T, error: next.firstCall?.args[0] as Outcome<T>['error'] }
}

const pin = controller.pinConversation as Handler
const unpin = controller.unpinConversation as Handler
const list = controller.getAllConversations as Handler
const archive = controller.archiveConversation as Handler

function fresh(): InMemoryChatStore {
  const store = new InMemoryChatStore()
  store.install()
  return store
}

const idOf = (doc: { _id: unknown }): string => String(doc._id)

describe('es_controller pin: a chat the owner pins is listed first, and only the owner can pin it', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('pinConversation', () => {
    it('marks the chat pinned and says so', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })

      const out = await call<{ id: string; isPinned: boolean }>(pin, { params: { conversationId: idOf(chat) } })

      expect(out.error).to.equal(undefined)
      expect(out.status).to.equal(200)
      expect(String(out.body.id)).to.equal(idOf(chat))
      expect(out.body.isPinned).to.equal(true)
      expect(store.session(chat._id)?.isPinned).to.equal(true)
    })

    it('does not touch the chat’s activity time, so it keeps its place among other pinned chats', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, lastActivityAt: 1234 })

      await call(pin, { params: { conversationId: idOf(chat) } })

      expect(store.session(chat._id)?.lastActivityAt).to.equal(1234)
    })

    it('asks the database not to bump updatedAt, because pinning is not an edit of the chat', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })

      await call(pin, { params: { conversationId: idOf(chat) } })
      await call(unpin, { params: { conversationId: idOf(chat) } })

      const calls = (ChatSession.findOneAndUpdate as unknown as sinon.SinonStub).getCalls()
      expect(calls).to.have.length(2)
      for (const c of calls) expect(c.args[2]).to.include({ timestamps: false })
    })

    it('is safe to repeat: a second pin succeeds and the chat stays pinned', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })

      const first = await call(pin, { params: { conversationId: idOf(chat) } })
      const second = await call<{ isPinned: boolean }>(pin, { params: { conversationId: idOf(chat) } })

      expect(first.status).to.equal(200)
      expect(second.status).to.equal(200)
      expect(second.body.isPinned).to.equal(true)
      expect(store.session(chat._id)?.isPinned).to.equal(true)
    })

    it('refuses an archived chat, which is not in the list to be pinned', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, isArchived: true })
      store.writes.length = 0

      const out = await call(pin, { params: { conversationId: idOf(chat) } })

      expect(out.error?.statusCode).to.equal(404)
      expect(store.writes).to.deep.equal([])
      expect(store.session(chat._id)?.isPinned).to.equal(undefined)
    })

    const refused = [
      { name: 'a deleted chat', fields: { isDeleted: true } },
      { name: 'an agent chat', fields: { sessionType: 'agent', agentKey: 'agent-1' } },
      { name: 'a chat of another organisation', fields: { orgId: OTHER_ORG } },
      { name: 'a chat owned by someone else', fields: { userId: OTHER, initiator: OTHER } },
      { name: 'a chat someone else started, which the list does not call the caller’s own', fields: { initiator: OTHER } },
      {
        name: 'a chat the caller can only edit through a share',
        fields: { userId: OTHER, initiator: OTHER, isShared: true, sharedWith: [{ userId: OWNER, accessLevel: 'write' }] },
      },
    ]
    for (const c of refused) {
      it(`refuses ${c.name} with 404 and writes nothing`, async () => {
        const store = fresh()
        const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, ...c.fields })
        store.writes.length = 0

        const out = await call(pin, { params: { conversationId: idOf(chat) } })

        expect(out.error?.statusCode).to.equal(404)
        expect(out.status).to.equal(undefined)
        expect(store.writes).to.deep.equal([])
        expect(store.session(chat._id)?.isPinned).to.equal(undefined)
      })
    }

    it('refuses an id that matches no chat', async () => {
      fresh()
      const out = await call(pin, { params: { conversationId: String(oid()) } })
      expect(out.error?.statusCode).to.equal(404)
    })

    it('refuses a request with no signed-in user', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })
      store.writes.length = 0

      const out = await call(pin, { params: { conversationId: idOf(chat) }, user: null })

      expect(out.error?.statusCode).to.equal(400)
      expect(store.writes).to.deep.equal([])
    })

    it('passes a database failure on instead of answering success', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })
      ;(ChatSession.findOneAndUpdate as unknown as sinon.SinonStub).throws(new Error('mongo down'))

      const out = await call(pin, { params: { conversationId: idOf(chat) } })

      expect(out.status).to.equal(undefined)
      expect(out.error?.message).to.equal('mongo down')
    })
  })

  describe('unpinConversation', () => {
    it('removes the pin field instead of storing false', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, isPinned: true })

      const out = await call<{ isPinned: boolean }>(unpin, { params: { conversationId: idOf(chat) } })

      expect(out.status).to.equal(200)
      expect(out.body.isPinned).to.equal(false)
      expect(store.session(chat._id)?.toObject().isPinned).to.equal(undefined)
    })

    it('succeeds on a chat that was never pinned and leaves it unpinned', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })

      const out = await call(unpin, { params: { conversationId: idOf(chat) } })

      expect(out.status).to.equal(200)
      expect(store.session(chat._id)?.isPinned).to.equal(undefined)
    })

    it('still clears a pin on an archived chat', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, isArchived: true, isPinned: true })

      const out = await call(unpin, { params: { conversationId: idOf(chat) } })

      expect(out.status).to.equal(200)
      expect(store.session(chat._id)?.isPinned).to.equal(undefined)
    })

    it('does not let another user clear the owner’s pin', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, isPinned: true })
      store.writes.length = 0

      const out = await call(unpin, { params: { conversationId: idOf(chat) }, user: { userId: OTHER, orgId: ORG } })

      expect(out.error?.statusCode).to.equal(404)
      expect(store.session(chat._id)?.isPinned).to.equal(true)
      expect(store.writes).to.deep.equal([])
    })
  })

  describe('archiveConversation', () => {
    it('drops the pin, so restoring the chat later does not bring it back pinned', async () => {
      const store = fresh()
      const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, isPinned: true })

      const out = await call(archive, { params: { conversationId: idOf(chat) } })

      expect(out.status).to.equal(200)
      expect(store.session(chat._id)?.isArchived).to.equal(true)
      expect(store.session(chat._id)?.isPinned).to.equal(undefined)
    })
  })

  describe('getAllConversations', () => {
    const titles = (out: Outcome<{ conversations: Array<{ title: string }> }>): string[] =>
      out.body.conversations.map((c) => c.title)

    function seed(store: InMemoryChatStore): void {
      // By recency alone the order would be c5, c4, c3, c2, c1.
      for (let i = 1; i <= 5; i += 1) {
        store.addSession({
          orgId: ORG,
          userId: OWNER,
          initiator: OWNER,
          title: `c${String(i)}`,
          lastActivityAt: i * 1000,
          // c1 is the oldest chat and c3 sits in the middle: both are pinned.
          ...(i === 1 || i === 3 ? { isPinned: true } : {}),
        })
      }
    }

    it('puts pinned chats first, and keeps newest-first order inside both groups', async () => {
      const store = fresh()
      seed(store)

      const out = await call<{ conversations: Array<{ title: string }> }>(list, { query: { source: 'owned' } })

      expect(titles(out)).to.deep.equal(['c3', 'c1', 'c5', 'c4', 'c2'])
    })

    it('with no pins, the order is the same newest-first order as before', async () => {
      const store = fresh()
      for (let i = 1; i <= 3; i += 1) {
        store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: `c${String(i)}`, lastActivityAt: i })
      }

      const out = await call<{ conversations: Array<{ title: string }> }>(list, { query: { source: 'owned' } })

      expect(titles(out)).to.deep.equal(['c3', 'c2', 'c1'])
    })

    it('cuts the pages after pinned chats are first, so a pinned chat never lands on page 2', async () => {
      const store = fresh()
      seed(store)

      const first = await call<{ conversations: Array<{ title: string }> }>(list, { query: { source: 'owned', page: '1', limit: '2' } })
      const second = await call<{ conversations: Array<{ title: string }> }>(list, { query: { source: 'owned', page: '2', limit: '2' } })
      const third = await call<{ conversations: Array<{ title: string }> }>(list, { query: { source: 'owned', page: '3', limit: '2' } })

      expect(titles(first)).to.deep.equal(['c3', 'c1'])
      expect(titles(second)).to.deep.equal(['c5', 'c4'])
      expect(titles(third)).to.deep.equal(['c2'])
    })

    it('keeps pinned chats first when the caller sorts by another field', async () => {
      const store = fresh()
      seed(store)

      const out = await call<{ conversations: Array<{ title: string }> }>(list, {
        query: { source: 'owned', sortBy: 'title', sortOrder: 'asc' },
      })

      expect(titles(out)).to.deep.equal(['c1', 'c3', 'c2', 'c4', 'c5'])
    })

    it('returns the pin flag on pinned chats and nothing on the others', async () => {
      const store = fresh()
      seed(store)

      const out = await call<{ conversations: Array<{ title: string; isPinned?: boolean }> }>(list, { query: { source: 'owned' } })

      expect(out.body.conversations.map((c) => c.isPinned)).to.deep.equal([true, true, undefined, undefined, undefined])
    })

    it('does not show a chat as pinned to the person it was shared with, nor sort it first for them', async () => {
      const store = fresh()
      store.addSession({
        orgId: ORG,
        userId: OWNER,
        initiator: OWNER,
        title: 'older shared, pinned by owner',
        lastActivityAt: 1,
        isShared: true,
        isPinned: true,
        sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }],
      })
      store.addSession({
        orgId: ORG,
        userId: OTHER,
        initiator: OTHER,
        title: 'newer shared',
        lastActivityAt: 2,
        isShared: true,
        sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }],
      })

      const out = await call<{ conversations: Array<{ title: string; isPinned?: boolean }> }>(list, {
        query: { source: 'shared' },
        user: { userId: RECIPIENT, orgId: ORG },
      })

      expect(out.error).to.equal(undefined)
      expect(titles(out)).to.deep.equal(['newer shared', 'older shared, pinned by owner'])
      expect(out.body.conversations.every((c) => !('isPinned' in c))).to.equal(true)
    })

    it('does not list another user’s pinned chat in the caller’s own list', async () => {
      const store = fresh()
      store.addSession({ orgId: ORG, userId: OTHER, initiator: OTHER, title: 'theirs', isPinned: true })
      store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'mine' })

      const out = await call<{ conversations: Array<{ title: string }> }>(list, { query: { source: 'owned' } })

      expect(titles(out)).to.deep.equal(['mine'])
    })
  })
})
