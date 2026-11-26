import 'reflect-metadata'
import { expect } from 'chai'
import { importGroupDocs } from '../../../src/libs/repositories/group/pg-import'
import { withUpsert } from '../../../src/libs/repositories/import'

const O = 'a'.repeat(24)
const G = 'b'.repeat(24)
const U1 = 'c'.repeat(24)
const doc = { _id: G, orgId: O, slug: 'usergroup-1', name: 'g', type: 'custom', users: [U1] }

// `there`: the group ids already in the table
const fakeTx = (log: string[], there: string[]) =>
  ({
    async query(sql: string, params: unknown[] = []) {
      if (sql.includes('import.group_existing')) return { rows: there.map((id) => ({ id })), rowCount: there.length }
      if (sql.includes('import.rows')) {
        const t = /INSERT INTO (\S+)/.exec(sql)![1]
        const n = (JSON.parse(params[0] as string) as unknown[]).length
        log.push(`${t}:${n}`)
        return { rows: [], rowCount: n }
      }
      log.push(sql.includes('import.group_members_replace') ? 'replace' : sql)
      return { rows: [], rowCount: 0 }
    },
  }) as never

describe('importGroupDocs members', () => {
  it('adds the members of a new group', async () => {
    const log: string[] = []
    await importGroupDocs(fakeTx(log, []), 'userGroups', [doc])
    expect(log).to.include('identity.user_group_members:1')
  })

  it('leaves the members of a group that is already there alone', async () => {
    const log: string[] = []
    await importGroupDocs(fakeTx(log, [G]), 'userGroups', [doc])
    expect(log.some((l) => l.startsWith('identity.user_group_members'))).to.equal(false)
  })

  it('replaces the members of a group written in place', async () => {
    const log: string[] = []
    await withUpsert(['identity.user_groups'], () => importGroupDocs(fakeTx(log, [G]), 'userGroups', [doc]))
    expect(log.indexOf('replace')).to.be.at.least(0)
    expect(log.indexOf('replace')).to.be.lessThan(log.indexOf('identity.user_group_members:1'))
  })
})
