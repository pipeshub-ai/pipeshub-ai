import 'reflect-metadata'
import { expect } from 'chai'
import { Binary } from 'mongodb'
import {
  byteaJson,
  emptyStats,
  idText,
  insertRows,
  limitJson,
  msToIso,
  neutral,
  withUpsert,
  writePlan,
} from '../../../src/libs/repositories/import'

type Call = { sql: string; params: unknown[] }
const fakeTx = (log: Call[], pk = ['org_id', 'id'], cols = ['org_id', 'id', 'name'], existing: string[] = []) =>
  ({
    async query(sql: string, params: unknown[] = []) {
      log.push({ sql, params })
      if (sql.includes('import.primary_key')) return { rows: pk.map((c) => ({ c })), rowCount: pk.length }
      if (sql.includes('import.columns')) return { rows: cols.map((c) => ({ c })), rowCount: cols.length }
      const sent = JSON.parse(params[0] as string) as Array<{ id: string }>
      // as PostgreSQL does: rowCount covers inserted and updated rows, xmax is 0 only on the inserted ones
      const rows = sql.includes('RETURNING') ? sent.map((r) => ({ ins: !existing.includes(r.id) })) : []
      return { rows, rowCount: sent.length }
    },
  }) as never

describe('document-store import helpers', () => {
  it('neutral turns driver values into plain ones', () => {
    const oid = { _bsontype: 'ObjectId', toString: () => 'ABCDEF0123456789ABCDEF01' }
    expect(neutral(oid)).to.equal('abcdef0123456789abcdef01')
    expect(neutral(new Date('2026-01-02T03:04:05.678Z'))).to.equal('2026-01-02T03:04:05.678Z')
    expect(neutral(new Date('nope'))).to.equal(null)
    expect(neutral({ a: undefined, b: [1, undefined], c: { d: oid } })).to.deep.equal({
      b: [1, null],
      c: { d: 'abcdef0123456789abcdef01' },
    })
  })

  it('neutral copies only the content of a Binary built in memory', () => {
    const b = new Binary() // allocates spare capacity
    b.write(Buffer.from([1, 2, 3]))
    expect(b.buffer.length).to.be.greaterThan(3)
    expect(neutral(b)).to.deep.equal(Buffer.from([1, 2, 3]))
  })

  it('idText, msToIso, byteaJson and limitJson keep their edge cases', () => {
    expect(idText('ABCDEF0123456789ABCDEF01')).to.equal('abcdef0123456789abcdef01')
    expect(idText('short')).to.equal(null)
    expect(msToIso('0')).to.equal('1970-01-01T00:00:00.000Z')
    expect(msToIso('x')).to.equal(null)
    expect(byteaJson(Buffer.from([1, 255]))).to.equal('\\x01ff')
    expect(byteaJson(null)).to.equal(null)
    expect(limitJson(undefined)).to.equal(null)
    expect(limitJson({ apps: ['a', 1] })).to.deep.equal({ apps: ['a'], recordGroups: [], records: [] })
  })
})

describe('insertRows', () => {
  it('writes nothing for no rows and refuses names that are not plain identifiers', async () => {
    const log: Call[] = []
    expect(await insertRows(fakeTx(log), 'identity.users', [])).to.equal(0)
    expect(log).to.have.length(0)
    for (const bad of ['users', 'identity.users; drop', 'Identity.Users'])
      await insertRows(fakeTx(log), bad, [{ id: 'x' }]).then(
        () => expect.fail(bad),
        (e: Error) => expect(e.message).to.match(/bad table name/),
      )
    await insertRows(fakeTx(log), 'identity.users', [{ 'id; --': 1 }]).then(
      () => expect.fail('column'),
      (e: Error) => expect(e.message).to.match(/bad column name/),
    )
  })

  it('sends rows that name different columns as separate statements and leaves existing rows alone', async () => {
    const log: Call[] = []
    const n = await insertRows(fakeTx(log), 'identity.users', [
      { org_id: 'o', id: '1', name: 'a' },
      { org_id: 'o', id: '2' },
      { org_id: 'o', id: '3', name: 'c' },
    ])
    expect(n).to.equal(3)
    expect(log).to.have.length(2)
    expect(log.every((c) => c.sql.includes('ON CONFLICT DO NOTHING'))).to.equal(true)
    expect(JSON.parse(log[0].params[0] as string)).to.have.length(2)
  })

  it('replaces rows in place only for the tables named in withUpsert', async () => {
    const log: Call[] = []
    await withUpsert(['identity.users'], () =>
      insertRows(fakeTx(log), 'identity.users', [{ org_id: 'o', id: '1', name: 'a' }]),
    )
    const insert = log.find((c) => c.sql.includes('import.rows'))!
    expect(insert.sql).to.contain('ON CONFLICT (org_id, id) DO UPDATE SET name = excluded.name')
    const other: Call[] = []
    await withUpsert(['identity.users'], () =>
      insertRows(fakeTx(other), 'identity.orgs', [{ id: '1' }]),
    )
    expect(other.find((c) => c.sql.includes('import.rows'))!.sql).to.contain('DO NOTHING')
  })

  it('counts only the rows an upsert inserted, not the ones it updated', async () => {
    const n = await withUpsert(['identity.users'], () =>
      insertRows(fakeTx([], undefined, undefined, ['2']), 'identity.users', [
        { org_id: 'o', id: '1', name: 'a' },
        { org_id: 'o', id: '2', name: 'b' },
      ]),
    )
    expect(n).to.equal(1)
  })

  it('sends one row per key in an upsert, the last one', async () => {
    const log: Call[] = []
    await withUpsert(['identity.users'], () =>
      insertRows(fakeTx(log), 'identity.users', [
        { org_id: 'o', id: '1', name: 'old' },
        { org_id: 'o', id: '1', name: 'new' },
      ]),
    )
    expect(JSON.parse(log.find((c) => c.sql.includes('import.rows'))!.params[0] as string)).to.deep.equal([
      { org_id: 'o', id: '1', name: 'new' },
    ])
  })

  it('writePlan adds the counts per table to the stats', async () => {
    const stats = emptyStats(2)
    await writePlan(
      fakeTx([]),
      [
        { table: 'identity.orgs', rows: [{ id: '1' }] },
        { table: 'identity.users', rows: [{ id: '1' }, { id: '2' }] },
      ],
      stats,
    )
    expect(stats.inserted).to.deep.equal({ 'identity.orgs': 1, 'identity.users': 2 })
  })
})
