import 'reflect-metadata'
import { expect } from 'chai'
import { ObjectId } from 'mongodb'
import { collectionCounts, readBatches } from '../../../src/scripts/docstore-migrate/mongo-read'
import { countByOrg } from '../../../src/scripts/docstore-migrate/pg-read'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'

/** A database whose collection answers `find` with these documents, remembering the filter it was asked with. */
const fakeDb = (docs: unknown[]) => {
  const seen: { filter?: any; closed: number } = { closed: 0 }
  const db = {
    collection: () => ({
      find: (filter: unknown) => {
        seen.filter = filter
        return {
          async *[Symbol.asyncIterator]() {
            for (const d of docs) yield d
          },
          close: async () => void (seen.closed += 1),
        }
      },
    }),
  } as never
  return { db, seen }
}
const ids = [1, 2, 3, 4, 5].map(() => new ObjectId())
const docs = ids.map((_id) => ({ _id, v: 'x' }))

describe('reading the source in batches', () => {
  it('batches by count, ends with the rest, and names the last id in lower case', async () => {
    const { db, seen } = fakeDb(docs)
    const out = []
    for await (const b of readBatches(db, 'users', null, 2, 1_000_000)) out.push(b)
    expect(out.map((b) => b.docs.length)).to.deep.equal([2, 2, 1])
    expect(out[2]!.last).to.equal(String(ids[4]).toLowerCase())
    expect(seen.filter).to.deep.equal({})
    expect(seen.closed, 'the cursor is closed').to.equal(1)
  })

  it('a batch ends as soon as it is as large as the byte limit', async () => {
    const { db } = fakeDb(docs)
    const out = []
    for await (const b of readBatches(db, 'users', null, 100, 1)) out.push(b)
    expect(out.map((b) => b.docs.length)).to.deep.equal([1, 1, 1, 1, 1])
  })

  it('continues after a checkpoint and stops at the end of a range, with ObjectId bounds for hex ids', async () => {
    const { db, seen } = fakeDb([])
    const after = String(ids[0])
    const upTo = String(ids[3])
    const batches = []
    for await (const b of readBatches(db, 'users', after, 10, 1000, upTo)) batches.push(b)
    expect(batches).to.have.length(0)
    expect(String(seen.filter._id.$gt)).to.equal(after)
    expect(String(seen.filter._id.$lte)).to.equal(upTo)
    expect(seen.filter._id.$gt).to.be.instanceOf(ObjectId)
  })

  it('counts the collections of a database and leaves the system ones out', async () => {
    const db = {
      listCollections: () => ({ toArray: async () => [{ name: 'users' }, { name: 'system.views' }, { name: 'org' }] }),
      collection: (n: string) => ({ estimatedDocumentCount: async () => (n === 'users' ? 5 : 2) }),
    } as never
    expect([...(await collectionCounts(db))]).to.deep.equal([['users', 5], ['org', 2]])
  })
})

describe('counting the rows of a table', () => {
  const k = (rows: unknown[]) => ({ query: async () => ({ rows }) }) as never
  it('per organisation when the table has one, and as one count when it has none', async () => {
    const users = MAPPINGS.find((m) => m.collection === 'users')!
    expect([...(await countByOrg(k([{ o: 'a', n: '3' }, { o: 'b', n: '1' }]), users))]).to.deep.equal([['a', 3], ['b', 1]])
    const noOrg = { ...users, orgCol: null }
    expect([...(await countByOrg(k([{ n: '7' }]), noOrg))]).to.deep.equal([['', 7]])
    expect([...(await countByOrg(k([]), noOrg))]).to.deep.equal([['', 0]])
  })
})

describe('a copy stops writing once its job is no longer running', () => {
  const { JobState } = require('../../../src/scripts/docstore-migrate/state') as typeof import('../../../src/scripts/docstore-migrate/state')
  const job = (status: string, calls: unknown[][] = []) =>
    Object.assign(Object.create(JobState.prototype), {
      id: 5,
      rt: { migratePool: { query: async (sql: string, p: unknown[]) => (calls.push([sql, p]), { rows: [{ status }] }) } },
    }) as InstanceType<typeof JobState>

  it('takes the shared copy lock first and lets a running job write', async () => {
    const taken: string[] = []
    await job('running').guardWrite({ query: async (sql: string) => void taken.push(sql) })
    expect(taken[0]).to.contain('pg_advisory_xact_lock_shared')
  })

  it('refuses a cancelled or finished job, naming it', async () => {
    for (const status of ['cancelled', 'done']) {
      let err: Error | undefined
      await job(status).guardWrite({ query: async () => undefined }).catch((e: Error) => (err = e))
      expect(err?.message).to.equal('job 5 is no longer running')
    }
  })

  it('ends a job with the status it is given', async () => {
    const calls: unknown[][] = []
    await job('running', calls).finish('cancelled')
    expect(calls[0]![1]).to.deep.equal([5, 'cancelled'])
  })
})
