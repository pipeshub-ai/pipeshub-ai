import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { Types } from 'mongoose'
import { InMemoryChatStore, matchesFilter, oid, plain } from './chat-test-harness'

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

  it('with copies, a conversation that saves is stored as written, not as the caller’s document', async () => {
    const store = new InMemoryChatStore({ copies: true })
    store.install()
    const valid = new ChatSession({ orgId: oid(), userId: oid(), initiator: oid(), title: 'owned' })

    expect(await valid.save()).to.equal(valid)
    expect(store.sessions.map(plain)).to.deep.equal([plain(valid)])
    expect(store.sessions[0]).to.not.equal(valid)
  })

  it('with copies, a read hands back a copy: a change reaches the store only when it is written, and then once', async () => {
    const store = new InMemoryChatStore({ copies: true })
    store.install()
    const stored = store.addSession({ orgId: oid(), userId: oid(), initiator: oid(), title: 'kept' })

    const read = await ChatSession.findById(stored._id)
    expect(read).to.not.equal(stored)
    read!.title = 'renamed'
    read!.conversationErrors.push({ message: 'failed once', errorType: 'internal_error' })
    expect(store.session(stored._id)?.title, 'an unsaved change').to.equal('kept')
    expect(store.session(stored._id)?.conversationErrors).to.have.length(0)

    await read!.save()
    const after = store.session(stored._id)
    expect(after?.title).to.equal('renamed')
    expect(after?.conversationErrors.map((e) => e.message), 'the save’s $push lands once').to.deep.equal(['failed once'])
  })

  it('with copies, what a new conversation’s creator changes after its save reaches the store only by a write', async () => {
    const store = new InMemoryChatStore({ copies: true })
    store.install()
    const created = new ChatSession({ orgId: oid(), userId: oid(), initiator: oid(), title: 'first' })
    await created.save()

    created.status = 'Failed'
    expect(store.session(created._id)?.status).to.not.equal('Failed')

    await ChatSession.updateOne({ _id: created._id }, { $set: { status: 'Failed' } })
    expect(store.session(created._id)?.status).to.equal('Failed')
  })

  describe('query operators follow the MongoDB manual', () => {
    const U1 = oid()
    const U2 = oid()

    it('$elemMatch needs one element to satisfy every condition, dotted paths may mix elements', () => {
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

    it('$elemMatch inside $or still honours a top-level orgId', () => {
      // Manual: $and implicit between top-level fields; $or with $elemMatch clauses
      const org = oid()
      const filter = { orgId: org, $or: [{ sharedWith: { $elemMatch: { userId: U1 } } }, { userId: U1 }] }
      const element = [{ userId: U1, accessLevel: 'read' }]
      expect(matchesFilter({ orgId: org, sharedWith: element }, filter)).to.equal(true)
      expect(matchesFilter({ orgId: oid(), sharedWith: element }, filter)).to.equal(false)
    })

    it('$elemMatch on scalars needs one element for all bounds; nested arrays are not flattened', () => {
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

    it('$lt and $gt compare Date, number and ObjectId bounds, and a missing field never matches', () => {
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

    it('$type matches by BSON type; null and a missing field are not objects, and unknown type names throw', () => {
      // Manual: $type
      expect(matchesFilter({ run: { id: 'r1' } }, { run: { $type: 'object' } })).to.equal(true)
      expect(matchesFilter({ run: null }, { run: { $type: 'object' } })).to.equal(false)
      expect(matchesFilter({}, { run: { $type: 'object' } })).to.equal(false)
      expect(matchesFilter({ key: 'k' }, { key: { $type: 'string' } })).to.equal(true)
      expect(matchesFilter({ key: 5 }, { key: { $type: 'string' } })).to.equal(false)
      expect(matchesFilter({ at: new Date(0) }, { at: { $type: 'date' } })).to.equal(true)
      expect(matchesFilter({ id: new Types.ObjectId() }, { id: { $type: 'objectId' } })).to.equal(true)
      expect(matchesFilter({ id: new Types.ObjectId() }, { id: { $type: 'object' } })).to.equal(false)
      expect(() => matchesFilter({ n: 1 }, { n: { $type: 'decimal' } })).to.throw('The in-memory store does not understand $type decimal')
    })

    it('comparison never crosses BSON types', () => {
      // Manual: Comparison Query Operators, "Type Bracketing"
      expect(matchesFilter({ _id: Types.ObjectId.createFromTime(1000) }, { _id: { $lt: new Date(2_000_000) } })).to.equal(false)
      expect(matchesFilter({ at: new Date(1000) }, { at: { $lt: 2000 } })).to.equal(false)
      expect(matchesFilter({ s: '10' }, { s: { $gt: 5 } })).to.equal(false)
    })

    it('$expr compares fields of the same document; unsupported expression operators throw', () => {
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

    it('$nor matches what fails every expression, a missing field included; an empty $nor is refused', () => {
      // Manual: $nor ("selects the documents that fail all the query expressions", those without the field included)
      expect(matchesFilter({ a: 1 }, { $nor: [{ a: 2 }, { b: 3 }] })).to.equal(true)
      expect(matchesFilter({}, { $nor: [{ a: 2 }] })).to.equal(true)
      expect(matchesFilter({ a: 1, b: 3 }, { $nor: [{ a: 2 }, { b: 3 }] })).to.equal(false)
      // As an ask's guard reads it: no element of this person for this version.
      const asks = { asks: [{ by: U1, ver: 'v1' }, { by: U2, ver: 'v2' }] }
      expect(matchesFilter(asks, { $nor: [{ asks: { $elemMatch: { by: U1, ver: 'v1' } } }] })).to.equal(false)
      expect(matchesFilter(asks, { $nor: [{ asks: { $elemMatch: { by: U1, ver: 'v2' } } }] })).to.equal(true)
      expect(() => matchesFilter({ a: 1 }, { $nor: [] })).to.throw('$nor argument must be a non-empty array')
    })
  })

  describe('update operators and stubs follow the MongoDB manual', () => {
    const seed = (fields: Record<string, unknown> = {}) => {
      const store = new InMemoryChatStore()
      store.install()
      const session = store.addSession({ orgId: oid(), userId: oid(), initiator: oid(), ...fields })
      return { store, session }
    }

    it('a conditional updateOne that matches nothing reports matchedCount 0 and changes nothing', async () => {
      // Manual: db.collection.updateOne() "Return" (matchedCount) and filter semantics
      const { store, session } = seed({ modelInfo: { modelKey: 'held' } })
      const result = await ChatSession.updateOne({ _id: session._id, 'modelInfo.modelKey': null }, { $set: { title: 'stolen' } })

      expect(result).to.deep.equal({ acknowledged: true, matchedCount: 0, modifiedCount: 0 })
      expect(session.title).to.equal(undefined)
      expect(store.writes).to.deep.equal([])
    })

    it('a matching updateOne applies, reports the counts and records the write', async () => {
      // Manual: db.collection.updateOne() "Return"
      const { store, session } = seed()
      const result = await ChatSession.updateOne({ _id: session._id, title: null }, { $set: { title: 'mine' } })

      expect(result).to.deep.equal({ acknowledged: true, matchedCount: 1, modifiedCount: 1 })
      expect(session.title).to.equal('mine')
      expect(store.writes).to.deep.equal(['chatSession.updateOne'])
      const again = await ChatSession.updateMany({ _id: session._id }, { $set: { title: 'mine' } })
      expect(again).to.deep.equal({ acknowledged: true, matchedCount: 1, modifiedCount: 0 })
    })

    it('$[identifier] with arrayFilters changes only the matching elements', async () => {
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

    it('arrayFilters are checked before any document is read: one name per filter, from a lowercase letter, one filter per identifier, each used', async () => {
      // Manual: $[<identifier>] ("must begin with a lowercase letter and contain only alphanumeric characters"); the
      // server also refuses a filter the update does not use and two filters for one identifier
      const { session } = seed({ sharedWith: [{ userId: oid(), accessLevel: 'write' }] })
      const outcome = (update: Record<string, unknown>, arrayFilters?: Array<Record<string, unknown>>, _id: unknown = session._id): Promise<string> =>
        ChatSession.updateOne({ _id }, update, arrayFilters ? { arrayFilters } : undefined).then(
          () => 'written',
          (e: Error) => e.message,
        )
      const byFilter = { $set: { 'sharedWith.$[p].accessLevel': 'read' } }
      const byIndex = { $set: { 'sharedWith.0.accessLevel': 'read' } }
      expect(await outcome(byIndex, [{ 'p.accessLevel': 'write' }])).to.equal("The array filter for identifier 'p' was not used in the update")
      expect(await outcome(byFilter, [{ 'p.accessLevel': 'write' }, { 'p.userId': oid() }])).to.equal('Found multiple array filters with the same top-level field name p')
      expect(await outcome({ $set: { 'sharedWith.$[P].accessLevel': 'read' } }, [{ 'P.accessLevel': 'write' }])).to.contain(
        "must be an alphanumeric string beginning with a lowercase letter, found 'P'",
      )
      expect(await outcome({ $set: { 'sharedWith.$[p_1].accessLevel': 'read' } }, [{ 'p_1.accessLevel': 'write' }])).to.contain("found 'p_1'")
      expect(await outcome({ $set: { 'sharedWith.$[p].accessLevel': 'read', 'sharedWith.$[q].userId': oid() } }, [{ 'p.accessLevel': 'write', 'q.accessLevel': 'read' }])).to.contain(
        "Expected a single top-level field name, found 'p' and 'q'",
      )
      expect(await outcome(byFilter)).to.equal("No array filter found for identifier 'p' in path 'sharedWith.$[p].accessLevel'")
      // Refused the same when no document matches, and by findOneAndUpdate too.
      expect(await outcome(byIndex, [{ 'p.accessLevel': 'write' }], oid())).to.equal("The array filter for identifier 'p' was not used in the update")
      expect(() => ChatSession.findOneAndUpdate({ _id: session._id }, byIndex, { arrayFilters: [{ 'p.accessLevel': 'write' }] })).to.throw('was not used in the update')
      expect(await outcome(byFilter, [{ 'p.accessLevel': 'write' }])).to.equal('written')
    })

    it('$[identifier] fails on a document without the array, changes nothing when no element matches, and takes $in and $or', async () => {
      // Manual: $[<identifier>] (the array must exist in the document; array filters take query operators)
      const [U1, U2, U3] = [oid(), oid(), oid()]
      const { session } = seed({
        sharedWith: [
          { userId: U1, accessLevel: 'write' },
          { userId: U2, accessLevel: 'write' },
          { userId: U3, accessLevel: 'write' },
        ],
      })
      const levels = () => (session.toObject().sharedWith as Array<{ accessLevel: string }>).map((e) => e.accessLevel)
      const toRead = { $set: { 'sharedWith.$[p].accessLevel': 'read' } }
      expect(await ChatSession.updateOne({ _id: session._id }, toRead, { arrayFilters: [{ 'p.userId': oid() }] })).to.deep.equal({
        acknowledged: true,
        matchedCount: 1,
        modifiedCount: 0,
      })
      await ChatSession.updateOne({ _id: session._id }, toRead, { arrayFilters: [{ 'p.userId': { $in: [U1, U3] } }] })
      expect(levels()).to.deep.equal(['read', 'write', 'read'])
      await ChatSession.updateOne({ _id: session._id }, toRead, { arrayFilters: [{ $or: [{ 'p.userId': U2 }, { 'p.accessLevel': 'none' }] }] })
      expect(levels()).to.deep.equal(['read', 'read', 'read'])
      const missing = await ChatSession.updateOne({ _id: session._id }, { $set: { 'nothing.$[p].x': 1 } }, { arrayFilters: [{ 'p.x': 0 }] }).then(
        () => 'written',
        (e: Error) => e.message,
      )
      expect(missing).to.equal("The path 'nothing' must exist in the document in order to apply array updates")
    })

    it('$push appends one element, makes the array where the field is absent, and fails on a field that is not an array', async () => {
      // Manual: $push ("If the field is absent in the document to update, $push adds the array field with the value as
      // its element"; "If the field is not an array, the operation will fail")
      const U1 = oid()
      const { session } = seed({ title: 'not a list' })
      await ChatSession.updateOne({ _id: session._id }, { $unset: { sharedWith: 1 } })
      expect(session.toObject().sharedWith).to.equal(undefined)
      await ChatSession.updateOne({ _id: session._id }, { $push: { sharedWith: { userId: U1, accessLevel: 'read' } } })
      expect((session.toObject().sharedWith as Array<{ accessLevel: string }>).map((e) => e.accessLevel)).to.deep.equal(['read'])
      const outcome = (update: Record<string, unknown>): Promise<string> =>
        ChatSession.updateOne({ _id: session._id }, update).then(
          () => 'written',
          (e: Error) => e.message,
        )
      expect(await outcome({ $push: { title: 'x' } })).to.match(/^The field 'title' must be an array but is of type string/)
      session.set('title', null)
      expect(await outcome({ $push: { title: 'x' } })).to.match(/^The field 'title' must be an array but is of type null/)
    })

    it('a $push guarded by $nor adds one element, and a repeat the guard rules out matches nothing', async () => {
      // Manual: updateOne() applies its update only to a document its filter matches; an ask is added this way, once per person
      const U1 = oid()
      const { store, session } = seed()
      const ask = () =>
        ChatSession.updateOne(
          { _id: session._id, $nor: [{ sharedWith: { $elemMatch: { userId: U1 } } }] },
          { $push: { sharedWith: { userId: U1, accessLevel: 'read' } } },
        )
      expect(await ask()).to.deep.equal({ acknowledged: true, matchedCount: 1, modifiedCount: 1 })
      expect(await ask()).to.deep.equal({ acknowledged: true, matchedCount: 0, modifiedCount: 0 })
      expect((session.toObject().sharedWith as unknown[]).length).to.equal(1)
      expect(store.writes).to.deep.equal(['chatSession.updateOne'])
    })

    it('$push with $each and $slice keeps the newest elements; $sort and $position are refused, not ignored', async () => {
      // Manual: $push modifiers ($each, $slice, $sort, $position); this store implements $each and $slice
      const [U1, U2, U3] = [oid(), oid(), oid()]
      const { session } = seed({ sharedWith: [{ userId: U1, accessLevel: 'read' }] })
      const pushed = [
        { userId: U2, accessLevel: 'read' },
        { userId: U3, accessLevel: 'write' },
      ]
      await ChatSession.updateOne({ _id: session._id }, { $push: { sharedWith: { $each: pushed, $slice: -2 } } })
      expect((session.toObject().sharedWith as Array<{ userId: unknown }>).map((e) => String(e.userId))).to.deep.equal([String(U2), String(U3)])
      for (const modifier of ['$sort', '$position']) {
        const outcome = await ChatSession.updateOne({ _id: session._id }, { $push: { sharedWith: { $each: [], [modifier]: 1 } } }).then(
          () => 'written',
          (e: Error) => e.message,
        )
        expect(outcome).to.contain(`does not understand ${modifier} in $push`)
      }
    })

    it('$[] updates every element and plain $ updates the element the query matched', async () => {
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

    it('$pull removes only the matching elements', async () => {
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

    it('$addToSet compares ObjectIds by value, and $max / $min keep the extreme', async () => {
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

    it('findOneAndUpdate with returnDocument before returns the pre-update value and the store holds the new one', async () => {
      // Manual: db.collection.findOneAndUpdate() returnDocument: "before" | "after"
      const { session } = seed({ title: 'old' })
      const before = await ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'new' } }, { returnDocument: 'before' })
      expect(before?.title).to.equal('old')
      expect(session.title).to.equal('new')
      const after = await ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'newer' } }, { new: true })
      expect(after?.title).to.equal('newer')
    })

    it('findOneAndUpdate returns the pre-update document by default, as Mongoose does', async () => {
      // Mongoose docs: Model.findOneAndUpdate() "options.new=false" / "options.returnDocument='before'" defaults
      const { session } = seed({ title: 'old' })
      const returned = await ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'new' } })
      expect(returned?.title).to.equal('old')
      expect(session.title).to.equal('new')
      const after = await ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'newer' } }, { returnDocument: 'after' })
      expect(after?.title).to.equal('newer')
    })

    it('an upsert throws, because the store cannot insert from an update', () => {
      // Manual: updateOne() / findOneAndUpdate() "upsert"; unsupported here, so it must not silently match nothing
      const { session } = seed()
      expect(() => ChatSession.updateOne({ _id: session._id }, { $set: { title: 'x' } }, { upsert: true })).to.throw('does not understand upsert')
      expect(() => ChatSession.findOneAndUpdate({ _id: session._id }, { $set: { title: 'x' } }, { upsert: true, returnDocument: 'after' })).to.throw(
        'does not understand upsert',
      )
    })

    it('operators the store does not understand throw instead of passing', async () => {
      // Manual: the server rejects or evaluates these; the harness must never silently ignore them
      const { session } = seed()
      expect(() => matchesFilter({ a: 1 }, { a: { $not: { $gt: 2 } } })).to.throw('does not understand $not')
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
