import 'reflect-metadata'
import { expect } from 'chai'
import { deleteChildren, deleteDocs } from '../../../src/scripts/docstore-migrate/apply'
import { repositoryFor, sourceDb } from '../../../src/scripts/docstore-migrate/bulk'
import { cancelDrain } from '../../../src/scripts/docstore-migrate/drain'
import { MARKER_COLLECTION, clearMarkers, isHistoryLost, majorityPoint, watchOptions } from '../../../src/scripts/docstore-migrate/follow'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'
import { outboxTableId } from '../../../src/scripts/docstore-migrate/mapping/content'

const mapping = (name: string) => MAPPINGS.find((m) => m.collection === name)!
/** A transaction that records every statement and its parameters. */
const record = () => {
  const log: Array<{ sql: string; params: unknown[] }> = []
  return { log, k: { query: async (sql: string, params: unknown[] = []) => (log.push({ sql, params }), { rows: [], rowCount: 0 }) } as never }
}

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

  it('an outbox delete converts the ObjectId to the table\'s number and binds it as a bigint', async () => {
    const hex = '6ad8b333aaaaaaaaaa000001'
    const { log, k } = record()
    await deleteDocs(k, mapping('outbox_events'), [hex])
    const root = log[log.length - 1]!
    expect(root.sql).to.match(/DELETE FROM platform\.outbox WHERE id = ANY\(\$1::bigint\[\]\)/)
    expect(root.params).to.deep.equal([[outboxTableId(hex)]])
    expect(outboxTableId(hex)).to.match(/^\d+$/)
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
})
