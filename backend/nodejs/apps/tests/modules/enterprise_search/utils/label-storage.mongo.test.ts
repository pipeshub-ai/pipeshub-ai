/**
 * Answer labels on a real MongoDB: stored on the answer, never read unless asked for, archived when the answer is
 * replaced or removed. Skipped unless MONGO_IT_URI is set; REPLICA_SET_AVAILABLE says whether it is a replica set.
 * Each run uses its own database and drops it afterwards.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import {
  appendMessages,
  buildAIResponseMessage,
  getMessages,
  retireMessagesById,
  updateMessageById,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { labelContentHash, labelStateOf } from '../../../../src/modules/enterprise_search/services/conversation/privacy/label'

const URI = process.env.MONGO_IT_URI
const describeIfMongo = URI ? describe : describe.skip

type Row = Record<string, unknown>

const LABEL = {
  v: 5,
  ver: '',
  contentHash: '',
  direct: { records: ['rec-CANARY-R'], vrids: ['vrid-CANARY-R'], nodes: [], saVrids: [], personal: [], agents: [], chat: [], projects: [], marks: [] },
  parents: [],
  complete: true,
  tagset: 1,
  pub: false,
}

const answer = (text: string) =>
  buildAIResponseMessage({ statusCode: 200, data: { answer: text, citations: [], provenance: LABEL } as never })

describeIfMongo('answer labels on a real MongoDB', function () {
  this.timeout(60000)
  const orgId = new mongoose.Types.ObjectId()
  const owner = new mongoose.Types.ObjectId()

  before(async () => {
    await mongoose.connect(URI as string, { dbName: `label_test_${Date.now()}` })
    await ChatSession.syncIndexes()
    await ChatSessionMessage.syncIndexes()
  })

  after(async () => {
    await mongoose.connection.dropDatabase()
    await mongoose.disconnect()
  })

  const chat = async () =>
    ChatSession.create({ sessionType: 'chat', userId: owner, initiator: owner, orgId, title: 'Quarterly numbers' })

  const withLabel = (id: unknown): Promise<Row | null> =>
    ChatSessionMessage.findOne({ _id: id as mongoose.Types.ObjectId }).select('+provenance +labelHistory').lean<Row>().exec()

  it('stores the label on the answer, bound to its version and text, and an ordinary read gets none of it', async () => {
    const session = await chat()
    const [inserted] = await appendMessages(session._id, orgId, [answer('Revenue grew 12%.')])
    const returned = (inserted as unknown as mongoose.Document).toObject() as Row
    expect(returned).to.not.have.any.keys('provenance', 'labelHistory')

    const plain = (await ChatSessionMessage.findOne({ _id: returned._id as mongoose.Types.ObjectId }).lean()) as unknown as Row
    expect(plain).to.not.have.any.keys('provenance', 'labelHistory')
    expect(plain.labelVer).to.equal(returned.labelVer)
    for (const row of (await getMessages(session._id, { orgId })) as Row[]) {
      expect(JSON.stringify(row)).to.not.include('CANARY')
    }

    const stored = (await withLabel(returned._id)) as Row
    const provenance = stored.provenance as Row
    expect(provenance.ver).to.equal(stored.labelVer)
    expect(provenance.contentHash).to.equal(labelContentHash(stored.content as string, stored.parts))
    expect(labelStateOf(stored)).to.equal('ok')
  })

  it('a regenerated answer archives the label of the version it replaced, whole', async () => {
    const session = await chat()
    const [first] = await appendMessages(session._id, orgId, [answer('One.')])
    const before = (await withLabel(first?._id)) as Row
    const replaced = await updateMessageById(first?._id as mongoose.Types.ObjectId, orgId, answer('Two.'))
    expect((replaced as unknown as mongoose.Document).toObject()).to.not.have.any.keys('provenance', 'labelHistory')

    const after = (await withLabel(first?._id)) as Row
    expect(after.labelVer).to.not.equal(before.labelVer)
    expect(after.labelHistory).to.deep.equal([{ ver: before.labelVer, provenance: before.provenance }])
    expect(labelStateOf(after)).to.equal('ok')
  })

  it('a removed answer keeps its label only in the archive, with the time it was retired', async () => {
    const session = await chat()
    const [row] = await appendMessages(session._id, orgId, [answer('Gone soon.')])
    const before = (await withLabel(row?._id)) as Row
    expect(await retireMessagesById([row?._id as mongoose.Types.ObjectId], { sessionId: session._id, orgId })).to.equal(1)

    const after = (await withLabel(row?._id)) as Row
    expect(after).to.not.have.property('provenance')
    expect(after.content).to.equal('')
    expect(after.labelVer).to.not.equal(before.labelVer)
    const [entry] = after.labelHistory as Row[]
    expect(entry).to.deep.include({ ver: before.labelVer, provenance: before.provenance })
    expect(entry?.retiredAt).to.be.instanceOf(Date)
  })

  it('a write of the text that skips the label leaves the label stale, so only the asker can see the answer', async () => {
    const session = await chat()
    const [row] = await appendMessages(session._id, orgId, [answer('Original.')])
    await ChatSessionMessage.updateOne({ _id: row?._id }, { $set: { content: 'Changed behind the label.' } })
    expect(labelStateOf((await withLabel(row?._id)) as Row)).to.equal('stale')
  })
})
