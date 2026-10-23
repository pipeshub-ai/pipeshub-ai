import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import {
  ACCESS_REQUESTS_PER_HOUR,
  createAccessRequest,
} from '../../../../src/modules/enterprise_search/controller/access_request.controller'
import { AccessRequest } from '../../../../src/modules/enterprise_search/schema/access.request.schema'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import EnterpriseSemanticSearch from '../../../../src/modules/enterprise_search/schema/search.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema'
import { FakeAIBackend, InMemoryChatStore, matchesFilter, oid } from './chat-test-harness'

/**
 * POST /access-requests must not say whether a record exists. It runs here against an in-memory
 * chat store and an in-memory request store (not stubs that return one canned answer), so repeats,
 * the hourly quota and the stored rows behave as they do against MongoDB.
 */

const appConfig: any = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' }
const ORG = oid()
const OWNER = oid()
const VIEWER = oid()
const STRANGER = oid()
const COLLECTION_OWNER = String(oid())
const OTHER_OWNER = String(oid())
const WEB_URL = 'https://wiki.example/Secret-Page-Title'

/** What the query service knows about each record, whatever the viewer has been shown. */
const CATALOG: Record<string, any> = {
  'native-shown': { kind: 'native', recordName: 'Plan', ownerUserIds: [COLLECTION_OWNER] },
  'native-shown-2': { kind: 'native', recordName: 'Plan 2', ownerUserIds: [COLLECTION_OWNER] },
  'connector-shown': { kind: 'connector', recordName: 'Wiki page', webUrl: WEB_URL, ownerUserIds: [] },
  'connector-hidden': { kind: 'connector', recordName: 'Other wiki page', webUrl: WEB_URL, ownerUserIds: [] },
  'kb-record-a': { kind: 'native', recordName: 'Plan A', collection: { id: 'col-1', name: 'Finance' }, ownerUserIds: [COLLECTION_OWNER, OTHER_OWNER] },
  'kb-record-b': { kind: 'native', recordName: 'Plan B', collection: { id: 'col-1', name: 'Finance' }, ownerUserIds: [COLLECTION_OWNER, OTHER_OWNER] },
  'own-turn-record': { kind: 'native', recordName: 'Mine', ownerUserIds: [COLLECTION_OWNER] },
  'native-hidden': { kind: 'native', recordName: 'Elsewhere', ownerUserIds: [COLLECTION_OWNER] },
  'readable-hidden': { kind: 'native', readable: true },
}

class RequestStore {
  rows: any[] = []
  install() {
    sinon.stub(AccessRequest, 'findOne').callsFake(((filter: any) => ({
      lean: async () => this.rows.find((r) => matchesFilter(r, filter)) ?? null,
    })) as never)
    sinon.stub(AccessRequest, 'countDocuments').callsFake((async (filter: any) =>
      this.rows.filter((r) => matchesFilter(r, filter)).length) as never)
    sinon.stub(AccessRequest, 'create').callsFake((async (doc: any) => {
      const row = { _id: oid(), createdAt: new Date(), ...doc }
      this.rows.push(row)
      return row
    }) as never)
  }
}

interface World {
  ai: FakeAIBackend
  chat: InMemoryChatStore
  requests: RequestStore
  chatId: string
  notified: sinon.SinonStub
}

const world = (extraShown: string[] = []): World => {
  const chat = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  chat.install()
  ai.install()
  ai.replyWith(/\/chat\/records\/access$/, (call) => {
    const ids = (call.body.recordIds as string[]) ?? []
    // "readable" records answer as accessible for the viewer, everything else in the catalog is denied.
    return {
      status: 200,
      body: {
        accessible: ids.filter((id) => CATALOG[id]?.readable),
        deniedDetails: ids
          .filter((id) => CATALOG[id] && !CATALOG[id].readable)
          .map((id) => ({ recordId: id, collection: null, ...CATALOG[id] })),
      },
    }
  })
  const cite = (chunkIndex: number, recordId: string) => {
    const doc = new Citation({
      content: `chunk ${recordId}`,
      chunkIndex,
      citationType: 'vectordb|document',
      metadata: { orgId: String(ORG), mimeType: 'text/plain', recordId, recordName: `name-${recordId}`, origin: 'UPLOAD', connector: 'KB' },
    })
    chat.citations.push(doc)
    return { citationId: doc._id }
  }
  const chat1 = chat.addSession({
    orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Shared', sessionType: 'chat', isShared: true,
    sharedWith: [{ userId: VIEWER, accessLevel: 'read' }], isArchived: false, isDeleted: false,
  })
  const shown = ['native-shown', 'native-shown-2', 'connector-shown', 'readable-hidden', 'kb-record-a', 'kb-record-b', ...extraShown]
  chat.addMessage(chat1, { messageType: 'user_query', content: 'q' })
  chat.addMessage(chat1, { messageType: 'bot_response', content: 'a', citations: shown.map((id, i) => cite(i + 1, id)) })
  const requests = new RequestStore()
  requests.install()
  const notified = sinon.stub(Notifications, 'create').resolves({ toObject: () => ({}) } as never)
  sinon.stub(Users, 'findById').returns({ select: () => ({ lean: async () => ({ fullName: 'Casey Viewer' }) }) } as never)
  return { ai, chat, requests, chatId: String(chat1._id), notified }
}

const ask = async (w: World, who: Types.ObjectId, recordId: string, from: Record<string, string> = { conversationId: w.chatId }) => {
  const out: any = {}
  const res: any = { status: (c: number) => ((out.status = c), res), json: (b: any) => ((out.body = b), res) }
  await createAccessRequest(appConfig)(
    { user: { userId: String(who), orgId: String(ORG) }, body: { recordId, ...from }, params: {}, query: {} } as never,
    res,
    (e: any) => (out.error = e),
  )
  return out
}

const shape = (out: any) => ({
  status: out.status,
  keys: Object.keys(out.body ?? {}).sort(),
  requestKeys: Object.keys(out.body?.request ?? {}).sort(),
  kind: out.body?.request?.kind,
  state: out.body?.request?.status,
})

describe('POST /access-requests does not say whether a record exists', () => {
  afterEach(() => sinon.restore())

  it('answers a record that was never shown exactly like a first request for a native record', async () => {
    const w = world()
    const real = await ask(w, VIEWER, 'native-shown')
    for (const id of ['no-such-record', 'native-hidden', 'connector-hidden', 'readable-hidden-elsewhere']) {
      const out = await ask(w, VIEWER, id)
      expect(shape(out), id).to.deep.equal(shape(real))
      expect(out.body.sourceUrl, id).to.equal(undefined)
      expect(JSON.stringify(out.body), id).to.not.include('Secret-Page-Title')
    }
  })

  it('notifies nobody for a record that was not shown, and the stored row is never listed', async () => {
    const w = world()
    await ask(w, VIEWER, 'native-hidden')
    expect(w.notified.called).to.equal(false)
    const row = w.requests.rows[0]
    expect(row.status).to.equal('void')
    expect(row.ownerUserIds).to.deep.equal([])
    await ask(w, VIEWER, 'native-shown')
    expect(w.notified.called).to.equal(true)
  })

  it('does not tell a repeat of a real request from a repeat of an unknown one', async () => {
    const w = world()
    const seen: Record<string, any[]> = {}
    for (const id of ['native-shown', 'no-such-record']) {
      seen[id] = [await ask(w, VIEWER, id), await ask(w, VIEWER, id)]
    }
    for (const id of Object.keys(seen)) {
      const [first, again] = seen[id]!
      expect(shape(again), id).to.deep.equal(shape(first))
      expect(again.body.request.id, id).to.equal(first.body.request.id)
      expect(again.body, id).to.not.have.property('duplicate')
    }
    expect(shape(seen['no-such-record']![1])).to.deep.equal(shape(seen['native-shown']![1]))
  })

  it('uses up the hourly quota the same way for a real and an unknown record', async () => {
    const extra = Array.from({ length: ACCESS_REQUESTS_PER_HOUR }, (_, i) => `native-${i}`)
    extra.forEach((id) => (CATALOG[id] = { kind: 'native', recordName: id, ownerUserIds: [COLLECTION_OWNER] }))
    try {
      const unknownFirst = world(extra)
      for (let i = 0; i < ACCESS_REQUESTS_PER_HOUR; i++) {
        expect((await ask(unknownFirst, VIEWER, `ghost-${i}`)).status, `ghost ${i}`).to.equal(201)
      }
      const afterGhosts = await ask(unknownFirst, VIEWER, 'native-shown')
      sinon.restore()

      const realFirst = world(extra)
      for (let i = 0; i < ACCESS_REQUESTS_PER_HOUR; i++) {
        expect((await ask(realFirst, VIEWER, `native-${i}`)).status, `real ${i}`).to.equal(201)
      }
      const afterReal = await ask(realFirst, VIEWER, 'ghost-next')

      expect(afterGhosts.error?.statusCode).to.equal(429)
      expect(afterReal.error?.statusCode).to.equal(429)
    } finally {
      extra.forEach((id) => delete CATALOG[id])
    }
  })

  it('shows a connector record’s page only for a connector record the viewer was shown', async () => {
    const w = world()
    const shown = await ask(w, VIEWER, 'connector-shown')
    expect(shape(shown)).to.include({ status: 201, kind: 'connector', state: 'forwarded' })
    expect(shown.body.sourceUrl).to.equal(WEB_URL)
    const hidden = await ask(w, VIEWER, 'connector-hidden')
    expect(hidden.body.sourceUrl).to.equal(undefined)
    expect(shape(hidden)).to.include({ kind: 'native', state: 'pending' })
    // A repeat of the shown connector request still answers the same way, source page included.
    const again = await ask(w, VIEWER, 'connector-shown')
    expect(again.body.request.id).to.equal(shown.body.request.id)
    expect(again.body.sourceUrl).to.equal(WEB_URL)
  })

  it('treats a record the viewer can read as not shown: the uniform answer, no source page', async () => {
    const w = world()
    const out = await ask(w, VIEWER, 'readable-hidden')
    expect(shape(out)).to.deep.equal(shape(await ask(w, VIEWER, 'native-shown')))
    expect(w.requests.rows.find((r) => r.recordId === 'readable-hidden')?.status).to.equal('void')
  })

  it('does not ask the query service about an id the viewer was not shown', async () => {
    const w = world()
    const asked = (id: string) =>
      w.ai.calls.filter((c) => /records\/access$/.test(c.url) && (c.body.includeOwners === true) && (c.body.recordIds as string[]).includes(id))
    await ask(w, VIEWER, 'no-such-record')
    await ask(w, VIEWER, 'native-hidden')
    expect(asked('no-such-record')).to.have.length(0)
    expect(asked('native-hidden')).to.have.length(0)
    await ask(w, VIEWER, 'native-shown')
    expect(asked('native-shown')).to.have.length(1)
  })

  it('lets a void row for an id be followed by a genuine request for it: the owner is told', async () => {
    const w = world()
    // The same record asked for before it was shown to this viewer (a void row), then shown.
    w.requests.rows.push({ _id: oid(), orgId: ORG, requesterId: VIEWER, recordId: 'native-shown', kind: 'native', status: 'void', ownerUserIds: [], createdAt: new Date() })
    const out = await ask(w, VIEWER, 'native-shown')
    expect(out.status).to.equal(201)
    expect(w.notified.called, 'the owner is notified').to.equal(true)
    expect(w.requests.rows.map((r) => r.status).sort()).to.deep.equal(['pending', 'void'])
  })

  it('accepts only the items actually sent as redacted: not a citation in a turn the viewer wrote', async () => {
    const w = world()
    const doc = new Citation({
      content: 'mine', chunkIndex: 9, citationType: 'vectordb|document',
      metadata: { orgId: String(ORG), mimeType: 'text/plain', recordId: 'own-turn-record', recordName: 'n', origin: 'UPLOAD', connector: 'KB' },
    })
    w.chat.citations.push(doc)
    w.chat.addMessage(w.chat.sessions[0]!, { messageType: 'bot_response', content: 'mine', authorUserId: VIEWER, citations: [{ citationId: doc._id }] })
    const out = await ask(w, VIEWER, 'own-turn-record')
    expect(out.status).to.equal(201)
    expect(w.notified.called, 'a record in the viewer’s own turn is not something to ask for').to.equal(false)
    expect(w.requests.rows[0]!.status).to.equal('void')
  })

  it('treats a chat the caller cannot open, or their own chat, as showing nothing', async () => {
    const w = world()
    const real = await ask(w, VIEWER, 'native-shown')
    const stranger = await ask(w, STRANGER, 'native-shown')
    const owner = await ask(w, OWNER, 'native-shown')
    expect(shape(stranger)).to.deep.equal(shape(real))
    expect(shape(owner)).to.deep.equal(shape(real))
    expect(stranger.body.sourceUrl).to.equal(undefined)
    // Neither of them created a real request: nobody was notified for them.
    expect(w.requests.rows.filter((r) => r.status !== 'void').map((r) => String(r.requesterId))).to.deep.equal([String(VIEWER)])
  })

  it('stores a real request for the owner, tells every owner of the collection, and sends the viewer nothing about either', async () => {
    const w = world()
    const out = await ask(w, VIEWER, 'kb-record-a')
    expect(out.status).to.equal(201)
    const raw = JSON.stringify(out.body)
    for (const leak of ['Finance', 'Plan A', COLLECTION_OWNER, OTHER_OWNER, 'ownerUserIds', 'collectionName', 'recordName']) {
      expect(raw, leak).to.not.include(leak)
    }
    const row = w.requests.rows[0]
    expect(row).to.include({ recordId: 'kb-record-a', kind: 'native', status: 'pending', collectionId: 'col-1', collectionName: 'Finance' })
    expect(row.ownerUserIds.map(String)).to.have.members([COLLECTION_OWNER, OTHER_OWNER])
    expect(w.notified.getCalls().map((c) => String(c.args[0].assignedTo))).to.have.members([COLLECTION_OWNER, OTHER_OWNER])
    expect(w.notified.firstCall.args[0].message).to.equal('Casey Viewer asked for read access to the collection "Finance".')
  })

  it('keeps one open request per collection, whichever record of it was asked for, without saying so', async () => {
    const w = world()
    const first = await ask(w, VIEWER, 'kb-record-a')
    const second = await ask(w, VIEWER, 'kb-record-b')
    expect(second.status).to.equal(201)
    expect(second.body.request.id).to.equal(first.body.request.id)
    expect(second.body).to.not.have.property('duplicate')
    expect(w.notified.callCount).to.equal(2) // two owners, told once
  })

  it('works from a saved search shared with the viewer: only the hits they were shown can be asked for', async () => {
    const w = world()
    const searchId = oid()
    const search = {
      _id: searchId, orgId: ORG, userId: OWNER, isShared: true, sharedWith: [{ userId: VIEWER }],
      citationIds: [{ _id: oid(), metadata: { recordId: 'native-shown' } }],
    }
    sinon.stub(EnterpriseSemanticSearch, 'findOne').callsFake(((filter: any) => ({
      populate: () => ({ lean: async () => (matchesFilter(search as any, { ...filter, _id: searchId }) ? search : null) }),
    })) as never)
    const from = { searchId: String(searchId) }
    const real = await ask(w, VIEWER, 'native-shown', from)
    const other = await ask(w, VIEWER, 'native-shown-2', from)
    expect(w.requests.rows.map((r) => [r.recordId, r.status])).to.deep.equal([['native-shown', 'pending'], ['native-shown-2', 'void']])
    expect(shape(other)).to.deep.equal(shape(real))
  })

  it('refuses a body that names neither a chat nor a search', async () => {
    const { createAccessRequestSchema } = await import('../../../../src/modules/enterprise_search/validators/es_validators')
    expect(createAccessRequestSchema.safeParse({ body: { recordId: 'r1' } }).success).to.equal(false)
    expect(createAccessRequestSchema.safeParse({ body: { recordId: 'r1', conversationId: String(oid()), searchId: String(oid()) } }).success).to.equal(false)
    expect(createAccessRequestSchema.safeParse({ body: { recordId: 'r1', conversationId: String(oid()) } }).success).to.equal(true)
  })
})
