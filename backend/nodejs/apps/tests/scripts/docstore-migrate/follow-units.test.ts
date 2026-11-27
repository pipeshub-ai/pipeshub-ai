import 'reflect-metadata'
import { expect } from 'chai'
import { ObjectId } from 'mongodb'
import { deleteChildren, deleteDocs, deleteUnclaimedRows, liveEvents, replaceDocs } from '../../../src/scripts/docstore-migrate/apply'
import { repositoryFor, sourceDb } from '../../../src/scripts/docstore-migrate/bulk'
import { cancelDrain } from '../../../src/scripts/docstore-migrate/drain'
import { MARKER_COLLECTION, clearMarkers, isHistoryLost, majorityPoint, watchOptions } from '../../../src/scripts/docstore-migrate/follow'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'
import { outboxTableId } from '../../../src/scripts/docstore-migrate/mapping/content'
import { setOutboxRepository } from '../../../src/libs/repositories/outbox'
import { PgOutboxRepository } from '../../../src/libs/repositories/outbox/pg'

const mapping = (name: string) => MAPPINGS.find((m) => m.collection === name)!
/** A transaction that records every statement and its parameters. */
const record = () => {
  const log: Array<{ sql: string; params: unknown[] }> = []
  return { log, k: { query: async (sql: string, params: unknown[] = []) => (log.push({ sql, params }), { rows: [], rowCount: 0 }) } as never }
}
/** Same, answering the read of the rows under folded outbox ids with `stored`. */
const recordWith = (stored: Array<Record<string, unknown>>) => {
  const log: Array<{ sql: string; params: unknown[] }> = []
  return { log, k: { query: async (sql: string, params: unknown[] = []) => (log.push({ sql, params }), { rows: /follow\.folded_rows/.test(sql) ? stored : [], rowCount: 0 }) } as never }
}
const ORG = '6afcbe98f03c0c4c54f37bce'
const AT = '2026-11-17T20:57:34.349Z'
/** An outbox event as MongoDB holds it, and the row the copy stores for it. */
const outboxDoc = (hex: string, status: string, key = 'k', org = ORG) => ({
  _id: new ObjectId(hex), topic: 'entity-events', key, status, attempts: 0, createdAt: new Date(AT),
  value: JSON.stringify({ payload: { orgId: org } }),
})
const storedRow = (hex: string, key = 'k', org = ORG) => ({
  org_id: org, id: outboxTableId(hex), topic: 'entity-events', msg_key: key, ordering_key: 'global',
  value: JSON.stringify({ payload: { orgId: org } }), created_at: new Date(AT),
})
const outboxDeletes = (log: Array<{ sql: string; params: unknown[] }>) => log.filter((l) => /DELETE FROM platform\.outbox/.test(l.sql))

describe('follow: deleting a document and what the stream asks for', () => {
  it('a delete removes the rows a document owns first, then its own row, by the key the table uses', async () => {
    const { log, k } = record()
    await deleteDocs(k, mapping('userGroups'), ['g1', 'g2'])
    expect(log.length).to.be.greaterThan(1)
    expect(log[log.length - 1]!.sql).to.match(/DELETE FROM identity\.user_groups WHERE id = ANY\(\$1::text\[\]\)/)
    expect(log[0]!.sql).to.match(/DELETE FROM identity\.user_group_members/)
    expect(log[log.length - 1]!.params).to.deep.equal([['g1', 'g2']])
    const none = record()
    await deleteDocs(none.k, mapping('userGroups'), [])
    await deleteChildren(none.k, mapping('userGroups'), [])
    expect(none.log).to.have.length(0)
  })

  it('an outbox delete event removes, by organisation and the folded bigint id, only the rows that are no event still copied there', async () => {
    const gone = '6ad8b333aaaaaaaaaa000001'
    const live = '6ad8b333bbbbbbbbbb000001'   // same second and counter: the same table id
    const other = '6afcbe98f03c0c4c54f37bcf'
    const { log, k } = recordWith([storedRow(gone, 'gone'), storedRow(live, 'live'), storedRow(gone, 'gone', other)])
    await deleteUnclaimedRows(k, mapping('outbox_events'), [gone], [storedRow(live, 'live')])
    const read = log.find((l) => /follow\.folded_rows/.test(l.sql))!
    expect(read.sql).to.match(/WHERE t\.id = ANY\(\$1::bigint\[\]\)/)
    expect(read.params).to.deep.equal([[outboxTableId(gone)]])
    const del = outboxDeletes(log)
    expect(del).to.have.length(1)
    expect(del[0]!.sql).to.match(/unnest\(\$1::text\[\], \$2::bigint\[\]\)/)
    expect(del[0]!.params).to.deep.equal([[ORG, other], [outboxTableId(gone), outboxTableId(gone)]])
    expect(outboxTableId(gone)).to.match(/^\d+$/)
  })

  it('a row that lacks the fields naming its event is kept, never deleted on a guess', async () => {
    const hex = '6ad8b333aaaaaaaaaa000001'
    const { log, k } = recordWith([{ ...storedRow(hex), msg_key: null }])
    await deleteUnclaimedRows(k, mapping('outbox_events'), [hex], [])
    expect(outboxDeletes(log)).to.have.length(0)
    const skippedLog = recordWith([{ ...storedRow(hex), msg_key: null }])
    setOutboxRepository(new PgOutboxRepository())
    try {
      const rt = { jobs: async (fn: (tx: unknown, k: unknown) => Promise<void>) => fn({ pgTx: { kernel: skippedLog.k } }, skippedLog.k) } as never
      await replaceDocs(rt, mapping('outbox_events'), [outboxDoc(hex, 'published')])
    } finally {
      setOutboxRepository()
    }
    expect(outboxDeletes(skippedLog.log)).to.have.length(0)
  })

  it('a folded table is never deleted from by id alone', async () => {
    const { k } = record()
    let err: Error | undefined
    await deleteDocs(k, mapping('outbox_events'), ['6ad8b333aaaaaaaaaa000001']).catch((e: Error) => (err = e))
    expect(err?.message).to.match(/folded/)
  })

  it('names the databases and the resume position of the stream', () => {
    expect(watchOptions()).to.deep.equal({ fullDocument: 'updateLookup', promoteLongs: false })
    expect(watchOptions({ _data: 'x' })).to.deep.include({ resumeAfter: { _data: 'x' } })
  })

  it('knows a stream that cannot be resumed from one that failed otherwise', () => {
    expect(isHistoryLost({ code: 286 })).to.equal(true)
    expect(isHistoryLost({ message: 'resume token was not found' })).to.equal(true)
    expect(isHistoryLost({ message: 'the resume token may no longer be in the oplog' })).to.equal(true)
    expect(isHistoryLost({ code: 11000 })).to.equal(false)
    expect(isHistoryLost(null)).to.equal(false)
  })

  it('reads the majority commit point, and nothing when the server does not report one', async () => {
    const rt = (status: unknown) => ({ mongo: { db: () => ({ command: async () => status }) } }) as never
    expect(await majorityPoint(rt({ optimes: { lastCommittedOpTime: { ts: 'T' } } }))).to.equal('T')
    expect(await majorityPoint(rt({}))).to.equal(null)
  })

  it('drops the follower\'s marker collection, and does not mind when it is not there', async () => {
    const dropped: string[] = []
    const rt = (fail: boolean) => ({ db: () => ({ collection: (n: string) => ({ drop: async () => (dropped.push(n), fail ? Promise.reject(new Error('ns not found')) : true) }) }) }) as never
    await clearMarkers(rt(false))
    await clearMarkers(rt(true))
    expect(dropped).to.deep.equal([MARKER_COLLECTION, MARKER_COLLECTION])
  })

  it('cancelling a drain only moves a draining fence back to active', async () => {
    const seen: any[] = []
    const rt = { db: () => ({ collection: () => ({ updateOne: async (f: unknown, u: unknown) => (seen.push([f, u]), { matchedCount: 1 }) }) }) } as never
    expect(await cancelDrain(rt)).to.equal(true)
    expect(seen[0][0]).to.deep.equal({ _id: 'fence', mode: { $in: ['draining'] } })
    expect(seen[0][1].$set.mode).to.equal('active')
  })

  it('refuses a repository that cannot import, and reads the Slack collection from its own database', () => {
    expect(() => repositoryFor({ ...mapping('users'), repository: 'nope' })).to.throw("repository 'nope' has no importBatch")
    const rt = { cfg: { slackDb: 'slack' }, db: (n?: string) => n ?? 'api' } as never
    expect(sourceDb(rt, mapping('users'))).to.equal('api')
    expect(sourceDb(rt, { ...mapping('users'), collection: 'slack_conversations' })).to.equal('slack')
  })

  it('a document the copy now skips loses the row an earlier event wrote (an event published after it was copied pending)', async () => {
    // the registry keeps the first repository built in this process, which another test may have built on MongoDB
    setOutboxRepository(new PgOutboxRepository())
    try {
      const hex = '6afcbfbe0d70d44bb8638f31'
      const run = async (status: string) => {
        // the row the event was copied as while it was pending
        const { log, k } = recordWith([storedRow(hex)])
        const rt = { jobs: async (fn: (tx: unknown, k: unknown) => Promise<void>) => fn({ pgTx: { kernel: k } }, k) } as never
        await replaceDocs(rt, mapping('outbox_events'), [outboxDoc(hex, status)])
        const del = log.findIndex((l) => /DELETE FROM platform\.outbox/.test(l.sql))
        const ins = log.findIndex((l) => /INSERT INTO platform\.outbox/.test(l.sql))
        return { deletes: outboxDeletes(log), del, ins }
      }
      // the event's own row goes first in every case; only an event the copy still holds is written again, after that
      const published = await run('published')
      expect(published.deletes).to.have.length(1)
      expect(published.deletes[0]!.params).to.deep.equal([[ORG], [outboxTableId(hex)]])
      expect(published.ins, 'a published event is not written again').to.equal(-1)
      const pending = await run('pending')
      expect(pending.deletes).to.have.length(1)
      expect(pending.ins, 'a pending event is written again after its row was removed').to.be.greaterThan(pending.del)
    } finally {
      setOutboxRepository()
    }
  })

  it('the remove-and-insert fallback keeps the row of another event of the same organisation under the same folded key', async () => {
    setOutboxRepository(new PgOutboxRepository())
    try {
      // same second, same counter: the neighbour's row sits under the published event's table id, in the same organisation
      const published = outboxDoc('6afcbfbe0d70d44bb8638f31', 'published')
      const { log, k } = recordWith([storedRow('6afcbfbe1122334455638f31', 'neighbour')])
      let first = true
      const rt = {
        jobs: async (fn: (tx: unknown, k: unknown) => Promise<void>) => {
          if (first) {
            first = false
            throw Object.assign(new Error('unique'), { code: '23505' })
          }
          return fn({ pgTx: { kernel: k } }, k)
        },
      } as never
      await replaceDocs(rt, mapping('outbox_events'), [published])
      expect(outboxDeletes(log), 'the row is the neighbour\'s, not the published event\'s').to.have.length(0)
    } finally {
      setOutboxRepository()
    }
  })

  it('reads each second of deleted outbox ids once, and names the live, copied events that fold to their ids', async () => {
    const reads: unknown[] = []
    const gone1 = '6afcbfbe0d70d44bb8638f31'
    const gone2 = '6afcbfbe0d70d44bb8638f32'                    // same second
    const live = outboxDoc('6afcbfbe1122334455638f31', 'pending', 'live')     // folds to gone1's id
    const published = outboxDoc('6afcbfbe2233445566638f32', 'published')       // folds to gone2's id, but is not copied
    const elsewhere = outboxDoc('6afcbfbe3344556677000001', 'pending')        // same second, another id
    const rt = { cfg: {}, db: () => ({ collection: () => ({ find: (q: unknown) => (reads.push(q), { toArray: async () => [live, published, elsewhere] }) }) }) } as never
    const found = await liveEvents(rt, mapping('outbox_events'), [gone1, gone2])
    expect(reads, 'one range read for the shared second').to.have.length(1)
    expect(found.map((e) => e.msg_key)).to.deep.equal(['live'])
    expect(found[0]!.id).to.equal(outboxTableId(gone1))
  })
})
