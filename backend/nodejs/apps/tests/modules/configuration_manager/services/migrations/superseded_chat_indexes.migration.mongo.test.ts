/**
 * The chat indexes an upgrade leaves behind, on a real MongoDB: the earlier schemas built six indexes these no longer
 * declare, and Mongoose never drops one. After the boot migration an upgraded database holds the indexes a fresh install
 * has; each old one goes only once the index that replaces it is built. Skipped unless MONGO_IT_URI is set; each run
 * uses its own databases and drops them afterwards.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import {
  SUPERSEDED_CHAT_INDEXES,
  SupersededChatIndexesMigration,
} from '../../../../../src/modules/configuration_manager/services/migrations/superseded_chat_indexes.migration'
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../../src/modules/enterprise_search/schema/chat.session.message.schema'

const URI = process.env.MONGO_IT_URI
const describeIfMongo = URI ? describe : describe.skip

const logger = () => ({ info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() })

const namesOf = async (collection: { indexes(): Promise<Array<{ name?: string }>> }): Promise<string[]> =>
  (await collection.indexes()).map((index) => String(index.name)).sort()

describeIfMongo('the chat indexes an upgrade leaves behind, on a real MongoDB', function () {
  this.timeout(120000)
  const db = `chat_indexes_${Date.now()}`
  let freshSessions: string[]
  let freshMessages: string[]

  /** The indexes the earlier chat schemas built and these do not, as an upgraded database still has them. */
  const buildSuperseded = async (): Promise<void> => {
    for (const old of SUPERSEDED_CHAT_INDEXES) {
      const collection = old.collection === 'chatSessions' ? ChatSession.collection : ChatSessionMessage.collection
      await collection.createIndex(old.key, { name: old.name })
    }
  }

  /** What `autoIndex` builds from these schemas at the first start. */
  const buildDeclared = async (): Promise<void> => {
    await ChatSession.createIndexes()
    await ChatSessionMessage.createIndexes()
  }

  const migrate = () => new SupersededChatIndexesMigration(logger() as never).run()

  before(async () => {
    await mongoose.connect(URI as string, { dbName: db, autoIndex: false })
    // A fresh install of these schemas, in a database of its own: the indexes an upgraded one should end with.
    const fresh = mongoose.connection.useDb(`${db}_fresh`)
    const sessions = fresh.model('FreshChatSession', ChatSession.schema, ChatSession.collection.collectionName)
    const messages = fresh.model('FreshChatSessionMessage', ChatSessionMessage.schema, ChatSessionMessage.collection.collectionName)
    await sessions.createIndexes()
    await messages.createIndexes()
    freshSessions = await namesOf(sessions.collection)
    freshMessages = await namesOf(messages.collection)
    await fresh.dropDatabase()
  })

  beforeEach(async () => {
    await mongoose.connection.dropDatabase()
  })

  after(async () => {
    await mongoose.connection.dropDatabase()
    await mongoose.disconnect()
  })

  afterEach(() => sinon.restore())

  it('an upgraded database ends with the indexes a fresh install has', async () => {
    await buildSuperseded()
    await buildDeclared()
    expect(await namesOf(ChatSession.collection)).to.include.members(['userId_1', 'agentKey_1', 'projectId_1'])

    const result = await migrate()

    expect(result).to.deep.equal({
      dropped: SUPERSEDED_CHAT_INDEXES.map((old) => `${old.collection}.${old.name}`),
      waiting: [],
      errored: 0,
    })
    expect(await namesOf(ChatSession.collection)).to.deep.equal(freshSessions)
    expect(await namesOf(ChatSessionMessage.collection)).to.deep.equal(freshMessages)
    // Each one it dropped is one a fresh install never has.
    for (const old of SUPERSEDED_CHAT_INDEXES) {
      expect([...freshSessions, ...freshMessages]).to.not.include(old.name)
    }
  })

  it('keeps each old index while the index that replaces it is not built, then drops it once it is', async () => {
    await buildSuperseded()

    const first = await migrate()

    expect(first.dropped).to.deep.equal([])
    expect(first.waiting).to.deep.equal(SUPERSEDED_CHAT_INDEXES.map((old) => `${old.collection}.${old.name}`))
    expect(await namesOf(ChatSession.collection)).to.include.members(['userId_1', 'agentKey_1', 'projectId_1'])

    await buildDeclared()
    const second = await migrate()

    expect(second.dropped).to.have.length(SUPERSEDED_CHAT_INDEXES.length)
    expect(await namesOf(ChatSession.collection)).to.deep.equal(freshSessions)
  })

  it('has nothing to do on a fresh install, before its collections exist or after, nor on a second run', async () => {
    expect(await migrate()).to.deep.equal({ dropped: [], waiting: [], errored: 0 })
    await buildDeclared()
    expect(await migrate()).to.deep.equal({ dropped: [], waiting: [], errored: 0 })
    await buildSuperseded()
    await migrate()
    expect(await migrate()).to.deep.equal({ dropped: [], waiting: [], errored: 0 })
  })

  it('leaves alone an index of the same name on another key', async () => {
    await buildDeclared()
    await ChatSession.collection.createIndex({ userId: -1 }, { name: 'userId_1' })

    const result = await migrate()

    expect(result.dropped).to.deep.equal([])
    expect(await namesOf(ChatSession.collection)).to.include('userId_1')
  })

  it('reports a drop that fails, never throws, and still checks the other collection', async () => {
    await buildSuperseded()
    await buildDeclared()
    sinon.stub(ChatSession.collection, 'dropIndex').rejects(Object.assign(new Error('not authorized'), { code: 13 }))
    const log = logger()

    const result = await new SupersededChatIndexesMigration(log as never).run()

    expect(result.errored).to.equal(1)
    expect(result.dropped).to.deep.equal(['chatSessionMessages.sessionId_1'])
    expect(log.error.calledOnce).to.equal(true)
  })
})
