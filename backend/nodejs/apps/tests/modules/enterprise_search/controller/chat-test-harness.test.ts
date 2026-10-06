import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { Types } from 'mongoose'
import { InMemoryChatStore, matchesFilter, oid } from './chat-test-harness'

describe('chat test harness: the in-memory store only keeps what was really saved', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('a conversation whose save fails is not stored, so later reads cannot find it', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const invalid = new ChatSession({ orgId: oid(), title: 'no owner' })

    let error: unknown
    try {
      await invalid.save()
    } catch (e) {
      error = e
    }

    expect((error as Error | undefined)?.name).to.equal('ValidationError')
    expect(store.sessions).to.deep.equal([])
    expect(await ChatSession.findOne({ title: 'no owner' })).to.equal(null)
  })

  it('a conversation that saves is stored and returned as saved', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const valid = new ChatSession({ orgId: oid(), userId: oid(), initiator: oid(), title: 'owned' })

    expect(await valid.save()).to.equal(valid)
    expect(store.sessions).to.deep.equal([valid])
  })

  describe('query operators follow the MongoDB manual', () => {
    const U1 = oid()
    const U2 = oid()

    it('SEC-05: $elemMatch needs one element to satisfy every condition, dotted paths may mix elements', () => {
      // Manual: Query Array of Embedded Documents ($elemMatch vs. dot notation across elements)
      const doc = {
        sharedWith: [
          { userId: U1, accessLevel: 'read' },
          { userId: U2, accessLevel: 'write' },
        ],
      }
      expect(matchesFilter(doc, { sharedWith: { $elemMatch: { userId: U1, accessLevel: 'write' } } })).to.equal(false)
      expect(matchesFilter(doc, { sharedWith: { $elemMatch: { userId: U2, accessLevel: 'write' } } })).to.equal(true)
      expect(matchesFilter(doc, { 'sharedWith.userId': U1, 'sharedWith.accessLevel': 'write' })).to.equal(true)
    })

    it('PH00-01: $elemMatch inside $or still honours a top-level orgId', () => {
      // Manual: $and implicit between top-level fields; $or with $elemMatch clauses
      const org = oid()
      const filter = { orgId: org, $or: [{ sharedWith: { $elemMatch: { userId: U1 } } }, { userId: U1 }] }
      const element = [{ userId: U1, accessLevel: 'read' }]
      expect(matchesFilter({ orgId: org, sharedWith: element }, filter)).to.equal(true)
      expect(matchesFilter({ orgId: oid(), sharedWith: element }, filter)).to.equal(false)
    })

    it('PH00-02: $elemMatch on scalars needs one element for all bounds; nested arrays are not flattened', () => {
      // Manual: $elemMatch "Element Match" on scalar arrays; dotted/bare operators may be met by different elements
      const bounds = { $gt: 3, $lt: 5 }
      expect(matchesFilter({ seqs: [4] }, { seqs: { $elemMatch: bounds } })).to.equal(true)
      expect(matchesFilter({ seqs: [2, 6] }, { seqs: { $elemMatch: bounds } })).to.equal(false)
      expect(matchesFilter({ seqs: [2, 6] }, { seqs: bounds })).to.equal(true)

      const members = [{ roles: ['viewer'] }, { name: 'b', roles: ['admin', 'editor'] }]
      expect(matchesFilter({ members }, { members: { $elemMatch: { roles: { $elemMatch: { $eq: 'admin' } } } } })).to.equal(true)
      expect(matchesFilter({ members: [{ roles: [['admin']] }] }, { members: { $elemMatch: { roles: { $elemMatch: { $eq: 'admin' } } } } })).to.equal(false)
      expect(matchesFilter({ members }, { members: { $elemMatch: { name: 'b', roles: 'viewer' } } })).to.equal(false)
    })

    it('PH00-03: $lt and $gt compare Date, number and ObjectId bounds, and a missing field never matches', () => {
      // Manual: $lt / $gt ("Comparison Query Operators"); comparison only within the same BSON type
      const early = new Date('2026-01-01')
      const late = new Date('2026-02-01')
      expect(matchesFilter({ at: early }, { at: { $lt: late } })).to.equal(true)
      expect(matchesFilter({ at: early }, { at: { $gt: late } })).to.equal(false)
      expect(matchesFilter({ n: 3 }, { n: { $gt: 2, $lt: 4 } })).to.equal(true)
      expect(matchesFilter({ n: 3 }, { n: { $lt: 3 } })).to.equal(false)
      const low = Types.ObjectId.createFromTime(1000)
      const high = Types.ObjectId.createFromTime(2000)
      expect(matchesFilter({ _id: low }, { _id: { $lt: high } })).to.equal(true)
      expect(matchesFilter({ _id: high }, { _id: { $lt: low } })).to.equal(false)
      expect(matchesFilter({ s: 'apple' }, { s: { $lt: 'banana' } })).to.equal(true)
      expect(matchesFilter({}, { missing: { $lt: 5 } })).to.equal(false)
      expect(matchesFilter({}, { missing: { $gt: 5 } })).to.equal(false)
    })

    it('PH00-03b: comparison never crosses BSON types', () => {
      // Manual: Comparison Query Operators, "Type Bracketing"
      expect(matchesFilter({ _id: Types.ObjectId.createFromTime(1000) }, { _id: { $lt: new Date(2_000_000) } })).to.equal(false)
      expect(matchesFilter({ at: new Date(1000) }, { at: { $lt: 2000 } })).to.equal(false)
      expect(matchesFilter({ s: '10' }, { s: { $gt: 5 } })).to.equal(false)
    })

    it('PH00-04: $expr compares fields of the same document; unsupported expression operators throw', () => {
      // Manual: $expr ("Compare Two Fields from A Single Document")
      expect(matchesFilter({ seq: 1, nextSeq: 2 }, { $expr: { $lt: ['$seq', '$nextSeq'] } })).to.equal(true)
      expect(matchesFilter({ seq: 2, nextSeq: 2 }, { $expr: { $lt: ['$seq', '$nextSeq'] } })).to.equal(false)
      expect(matchesFilter({ a: { b: 3 } }, { $expr: { $and: [{ $gte: ['$a.b', 3] }, { $not: [{ $eq: ['$a.b', 4] }] }] } })).to.equal(true)
      expect(matchesFilter({ tags: ['x', 'y'] }, { $expr: { $eq: [{ $size: { $ifNull: ['$tags', []] } }, 2] } })).to.equal(true)
      expect(matchesFilter({ runId: null }, { $expr: { $eq: ['$runId', null] } })).to.equal(true)
      expect(matchesFilter({}, { $expr: { $eq: ['$runId', null] } })).to.equal(false)
      expect(matchesFilter({}, { $expr: { $eq: [{ $ifNull: ['$runId', null] }, null] } })).to.equal(true)
      expect(() => matchesFilter({ a: 1 }, { $expr: { $function: { body: 'x', args: [], lang: 'js' } } })).to.throw(
        'The in-memory store does not understand $expr.function',
      )
    })
  })

  describe('update operators and stubs follow the MongoDB manual', () => {
    const seed = (fields: Record<string, unknown> = {}) => {
      const store = new InMemoryChatStore()
      store.install()
      const session = store.addSession({ orgId: oid(), userId: oid(), initiator: oid(), ...fields })
      return { store, session }
    }

    it('PH00-05: a conditional updateOne that matches nothing reports matchedCount 0 and changes nothing', async () => {
      // Manual: db.collection.updateOne() "Return" (matchedCount) and filter semantics
      const { store, session } = seed({ modelInfo: { modelKey: 'held' } })
      const result = await ChatSession.updateOne({ _id: session._id, 'modelInfo.modelKey': null }, { $set: { title: 'stolen' } })

      expect(result).to.deep.equal({ acknowledged: true, matchedCount: 0, modifiedCount: 0 })
      expect(session.title).to.equal(undefined)
      expect(store.writes).to.deep.equal([])
    })

    it('PH00-05b: a matching updateOne applies, reports the counts and records the write', async () => {
      // Manual: db.collection.updateOne() "Return"
      const { store, session } = seed()
      const result = await ChatSession.updateOne({ _id: session._id, title: null }, { $set: { title: 'mine' } })

      expect(result).to.deep.equal({ acknowledged: true, matchedCount: 1, modifiedCount: 1 })
      expect(session.title).to.equal('mine')
      expect(store.writes).to.deep.equal(['chatSession.updateOne'])
      const again = await ChatSession.updateMany({ _id: session._id }, { $set: { title: 'mine' } })
      expect(again).to.deep.equal({ acknowledged: true, matchedCount: 1, modifiedCount: 0 })
    })

    it('PH00-06: $[identifier] with arrayFilters changes only the matching elements', async () => {
      // Manual: Update Operators > $[<identifier>] (Filtered Positional Operator)
      const [U1, U2] = [oid(), oid()]
      const { session } = seed({
        sharedWith: [
          { userId: U1, accessLevel: 'write' },
          { userId: U2, accessLevel: 'write' },
        ],
      })
      await ChatSession.updateOne(
        { _id: session._id },
        { $set: { 'sharedWith.$[p].accessLevel': 'read' } },
        { arrayFilters: [{ 'p.userId': U2 }] },
      )

      const levels = (session.toObject().sharedWith as Array<{ accessLevel: string }>).map((e) => e.accessLevel)
      expect(levels).to.deep.equal(['write', 'read'])
    })

    it('PH00-06b: $[] updates every element and plain $ updates the element the query matched', async () => {
      // Manual: Update Operators > $[] (All Positional Operator) and $ (Positional Operator)
      const [U1, U2] = [oid(), oid()]
      const { session } = seed({
        sharedWith: [
          { userId: U1, accessLevel: 'read' },
          { userId: U2, accessLevel: 'read' },
        ],
      })
      const levels = () => (session.toObject().sharedWith as Array<{ accessLevel: string }>).map((e) => e.accessLevel)

      await ChatSession.updateOne({ _id: session._id, 'sharedWith.userId': U2 }, { $set: { 'sharedWith.$.accessLevel': 'write' } })
      expect(levels()).to.deep.equal(['read', 'write'])
      await ChatSession.updateOne({ _id: session._id }, { $set: { 'sharedWith.$[].accessLevel': 'read' } })
      expect(levels()).to.deep.equal(['read', 'read'])
    })

    it('PH00-07: $pull removes only the matching elements', async () => {
      // Manual: Update Operators > $pull (condition on embedded documents)
      const [U1, U2] = [oid(), oid()]
      const { session } = seed({
        sharedWith: [
          { userId: U1, accessLevel: 'read' },
          { userId: U2, accessLevel: 'read' },
        ],
      })
      await ChatSession.updateOne({ _id: session._id }, { $pull: { sharedWith: { userId: U1 } } })

      expect((session.toObject().sharedWith as Array<{ userId: Types.ObjectId }>).map((e) => String(e.userId))).to.deep.equal([String(U2)])
    })

    it('PH00-07b: $addToSet compares ObjectIds by value, and $max / $min keep the extreme', async () => {
      // Manual: $addToSet (adds only if absent), $max, $min
      const { store, session } = seed({ lastActivityAt: 10 })
      const id = oid()
      const message = store.addMessage(session, {})
      message.set('parts', [id])
      await ChatSessionMessage.updateOne({ _id: message._id }, { $addToSet: { parts: new Types.ObjectId(id.toHexString()) } })
      expect(message.get('parts')).to.have.length(1)
      await ChatSessionMessage.updateOne({ _id: message._id }, { $addToSet: { parts: oid() } })
      expect(message.get('parts')).to.have.length(2)

      await ChatSession.updateOne({ _id: session._id }, { $max: { lastActivityAt: 5 } })
      expect(session.lastActivityAt).to.equal(10)
      await ChatSession.updateOne({ _id: session._id }, { $max: { lastActivityAt: 20 } })
      expect(session.lastActivityAt).to.equal(20)
      await ChatSession.updateOne({ _id: session._id }, { $min: { lastActivityAt: 7 } })
      expect(session.lastActivityAt).to.equal(7)
    })

    it('PH00-08: findOneAndUpdate with returnDocument before returns the pre-update value and the store holds the new one', async () => {
      // Manual: db.collection.findOneAndUpdate() returnDocument: "before" | "after"
      const { session } = seed({ title: 'old' })
      const before = await ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'new' } }, { returnDocument: 'before' })
      expect(before?.title).to.equal('old')
      expect(session.title).to.equal('new')
      const after = await ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'newer' } }, { new: true })
      expect(after?.title).to.equal('newer')
    })

    it('PH00-08b: findOneAndUpdate returns the pre-update document by default, as Mongoose does', async () => {
      // Mongoose docs: Model.findOneAndUpdate() "options.new=false" / "options.returnDocument='before'" defaults
      const { session } = seed({ title: 'old' })
      const returned = await ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'new' } })
      expect(returned?.title).to.equal('old')
      expect(session.title).to.equal('new')
      const after = await ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'newer' } }, { returnDocument: 'after' })
      expect(after?.title).to.equal('newer')
    })

    it('PH00-08c: an upsert throws, because the store cannot insert from an update', () => {
      // Manual: updateOne() / findOneAndUpdate() "upsert"; unsupported here, so it must not silently match nothing
      const { session } = seed()
      expect(() => ChatSession.updateOne({ _id: session._id }, { $set: { title: 'x' } }, { upsert: true })).to.throw('does not understand upsert')
      expect(() => ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'x' } }, { upsert: true, new: true })).to.throw(
        'does not understand upsert',
      )
    })

    it('PH00-09: operators the store does not understand throw instead of passing', async () => {
      // Manual: the server rejects or evaluates these; the harness must never silently ignore them
      const { session } = seed()
      expect(() => matchesFilter({ a: 1 }, { $nor: [{ a: 2 }] })).to.throw('does not understand $nor')
      expect(() => matchesFilter({ a: 1 }, { $where: 'true' })).to.throw('does not understand $where')
      expect(() => matchesFilter({ a: 1 }, { a: { $mod: [2, 1] } })).to.throw('does not understand $mod')
      let error: unknown
      try {
        await ChatSession.updateOne({ _id: session._id }, { $rename: { title: 'name' } })
      } catch (e) {
        error = e
      }
      expect((error as Error | undefined)?.message).to.contain('does not understand $rename')
    })
  })
})
