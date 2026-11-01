import { expect } from 'chai'
import {
  FENCE_COLLECTION, FENCE_READ_DEADLINE_MS, WRITE_TIMEOUT_MS, assertMongoWritable, forgetFenceCache, installMongoFence,
} from '../../../src/libs/repositories/fence'
import { DocStoreFencedError, DocStoreReadOnlyError } from '../../../src/libs/repositories/errors'
import { ServiceUnavailableError } from '../../../src/libs/errors/http.errors'

type Fence = { mode?: string } | null | Error

/** A stand-in for the driver's Collection: records what reaches the "server". */
function makeCollectionClass(state: { fence: Fence; log: unknown[][] }) {
  class Col {
    s = {
      db: {
        collection: (name: string) => ({
          findOne: async (filter: object, options: object) => {
            state.log.push(['fence-read', name, filter, options])
            if (state.fence instanceof Error) throw state.fence
            return state.fence
          },
        }),
      },
    }
    constructor(readonly collectionName: string) {}
    async findOne() { return 'read' }
  }
  const proto = Col.prototype as unknown as Record<string, unknown>
  for (const m of ['insertOne', 'insertMany', 'bulkWrite', 'updateOne', 'updateMany', 'replaceOne', 'deleteOne', 'deleteMany', 'findOneAndDelete', 'findOneAndReplace', 'findOneAndUpdate', 'drop', 'rename'])
    proto[m] = async (...args: unknown[]) => { state.log.push([m, ...args]); return 'ok' }
  return Col as typeof Col & { prototype: { insertOne(d: object, o?: object): Promise<string>; updateOne(f: object, u: object, o?: object): Promise<string>; deleteMany(f?: object, o?: object): Promise<string> } }
}

describe('Mongo store fence', () => {
  beforeEach(() => forgetFenceCache());
  // the tests below read the fence on every write; the trust window has its own test
  before(() => { process.env.DOC_STORE_FENCE = 'on'; process.env.DOC_STORE_FENCE_TRUST_MS = '0' })
  after(() => { process.env.DOC_STORE_FENCE = 'off'; delete process.env.DOC_STORE_FENCE_TRUST_MS })

  // installMongoFence wraps once per process; each test gets its own class, so reset the module flag by re-requiring
  const fresh = () => {
    delete require.cache[require.resolve('../../../src/libs/repositories/fence')]
    return require('../../../src/libs/repositories/fence') as typeof import('../../../src/libs/repositories/fence')
  }
  const setup = (fence: Fence) => {
    const state = { fence, log: [] as unknown[][] }
    const Col = makeCollectionClass(state)
    fresh().installMongoFence(Col)
    return { state, c: new Col('things') }
  }

  it('reads the fence from the primary with a 2 s deadline, then (while moving) writes with timeoutMS 5000', async () => {
    const { state, c } = setup({ mode: 'draining' })
    await c.insertOne({ a: 1 })
    expect(state.log[0]).to.deep.equal(['fence-read', FENCE_COLLECTION, { _id: 'fence' }, { readPreference: 'primary', timeoutMS: FENCE_READ_DEADLINE_MS }])
    expect(state.log[1]).to.deep.equal(['insertOne', { a: 1 }, { timeoutMS: WRITE_TIMEOUT_MS }])
    expect(FENCE_READ_DEADLINE_MS).to.equal(2000)
    expect(WRITE_TIMEOUT_MS).to.equal(5000)
  })

  it('an active store puts no deadline on any write: a long updateMany is not cut short; a store being moved does', async () => {
    const active = setup({ mode: 'active' })
    await active.c.updateMany({}, { $set: { b: 1 } })
    await active.c.deleteMany({})
    await active.c.bulkWrite([])
    expect(active.state.log.filter((l) => l[0] !== 'fence-read')).to.deep.equal([['updateMany', {}, { $set: { b: 1 } }], ['deleteMany', {}], ['bulkWrite', []]])
    const moving = setup({ mode: 'draining' })
    await moving.c.updateMany({}, { $set: { b: 1 } })
    expect(moving.state.log[1]).to.deep.equal(['updateMany', {}, { $set: { b: 1 } }, { timeoutMS: 5000 }])
  })

  it('a missing fence document means active', async () => {
    const { state, c } = setup(null)
    await c.updateOne({ a: 1 }, { $set: { b: 1 } })
    expect(state.log[1]).to.deep.equal(['updateOne', { a: 1 }, { $set: { b: 1 } }])
  })

  it('keeps the options the caller passed and a timeout it set; skips the deadline inside a transaction (while the store is being moved)', async () => {
    const { state, c } = setup({ mode: 'draining' })
    await c.insertOne({}, { ordered: false })
    expect(state.log[1]?.[2]).to.deep.equal({ ordered: false, timeoutMS: 5000 })
    await c.insertOne({}, { timeoutMS: 100 })
    expect(state.log[3]?.[2]).to.deep.equal({ timeoutMS: 100 })
    const session = { inTransaction: () => true }
    await c.insertOne({}, { session })
    expect(state.log[5]?.[2]).to.deep.equal({ session })
  })

  it('pads omitted arguments so the options land in place (deleteMany())', async () => {
    const { state, c } = setup({ mode: 'draining' })
    await c.deleteMany()
    expect(state.log[1]).to.deep.equal(['deleteMany', undefined, { timeoutMS: 5000 }])
  })

  it('refuses every write while read-only, and answers a retired store with the fenced error; nothing is written', async () => {
    const ro = setup({ mode: 'readonly' })
    expect(await ro.c.insertOne({}).catch((e) => e)).to.be.instanceOf(DocStoreReadOnlyError)
    expect(ro.state.log.filter((l) => l[0] === 'insertOne')).to.have.length(0)
    const retired = setup({ mode: 'retired' })
    expect(await retired.c.updateOne({}, {}).catch((e) => e)).to.be.instanceOf(DocStoreFencedError)
    expect(retired.state.log.filter((l) => l[0] === 'updateOne')).to.have.length(0)
  })

  it('fails closed when the fence cannot be read', async () => {
    const { state, c } = setup(new Error('timed out'))
    expect(await c.insertOne({}).catch((e) => e)).to.be.instanceOf(ServiceUnavailableError)
    expect(state.log.filter((l) => l[0] === 'insertOne')).to.have.length(0)
  })

  it('does not touch reads, and does not fence the fence collection itself', async () => {
    const { state, c } = setup({ mode: 'readonly' })
    expect(await c.findOne()).to.equal('read')
    const fence = new (Object.getPrototypeOf(c).constructor)(FENCE_COLLECTION)
    await fence.insertOne({ _id: 'fence', mode: 'active' })
    expect(state.log.map((l) => l[0])).to.deep.equal(['insertOne'])
  })

  it('trusts an active answer for a moment (a burst of writes pays for one read) and re-reads a refusal every time', async () => {
    process.env.DOC_STORE_FENCE_TRUST_MS = '500'
    try {
      let reads = 0
      const db = (mode: string) => ({ collection: () => ({ findOne: async () => ((reads += 1), { mode, epoch: 1 }) }) })
      await assertMongoWritable(db('active') as never)
      await assertMongoWritable(db('active') as never)
      expect(reads).to.equal(1)
      forgetFenceCache()
      reads = 0
      for (let i = 0; i < 3; i++) await assertMongoWritable(db('retired') as never).catch(() => undefined)
      expect(reads).to.equal(3)
    } finally {
      process.env.DOC_STORE_FENCE_TRUST_MS = '0'
    }
  })

  it('a draining answer is not trusted: the next write after a move to readonly is refused', async () => {
    process.env.DOC_STORE_FENCE_TRUST_MS = '60000'
    try {
      let mode = 'draining'
      const db = { collection: () => ({ findOne: async () => ({ mode }) }) }
      expect(await assertMongoWritable(db as never)).to.equal('draining')
      mode = 'readonly'
      let err: unknown
      await assertMongoWritable(db as never).catch((e) => { err = e })
      expect(err).to.be.instanceOf(DocStoreReadOnlyError)
    } finally {
      process.env.DOC_STORE_FENCE_TRUST_MS = '0'
    }
  })

  it('assertMongoWritable refuses a read that came back late', async () => {
    const slow = { collection: () => ({ findOne: async () => { await new Promise((r) => setTimeout(r, 30)); return { mode: 'active' } } }) }
    const realNow = process.hrtime.bigint
    let calls = 0
    process.hrtime.bigint = (() => BigInt(calls++ === 0 ? 0 : 2_500_000_000)) as typeof process.hrtime.bigint
    try {
      expect(await assertMongoWritable(slow).catch((e) => e)).to.be.instanceOf(ServiceUnavailableError)
    } finally {
      process.hrtime.bigint = realNow
    }
  })

  it('wraps each driver method once even if installed twice', async () => {
    const state = { fence: { mode: 'active' } as Fence, log: [] as unknown[][] }
    const Col = makeCollectionClass(state)
    const f = fresh()
    f.installMongoFence(Col)
    f.installMongoFence(Col)
    await new Col('t').insertOne({})
    expect(state.log.filter((l) => l[0] === 'fence-read')).to.have.length(1)
    expect(typeof installMongoFence).to.equal('function')
  })

  it('is off unless DOC_STORE_FENCE=on, and off wraps nothing, reads no fence, adds no timeout', async () => {
    delete process.env.DOC_STORE_FENCE
    expect(fresh().mongoFenceArmed()).to.equal(false)
    process.env.DOC_STORE_FENCE = 'off'
    try {
      const state = { fence: { mode: 'readonly' } as Fence, log: [] as unknown[][] }
      const Col = makeCollectionClass(state)
      fresh().installMongoFence(Col)
      await new Col('t').insertOne({ a: 1 })
      expect(state.log).to.deep.equal([['insertOne', { a: 1 }]])
      expect(fresh().mongoFenceArmed()).to.equal(false)
    } finally {
      process.env.DOC_STORE_FENCE = 'on'
    }
    expect(fresh().mongoFenceArmed()).to.equal(true)
  })
})
