/**
 * R40SLACK-FU-1: `docstore-migrate` copies the Slack account links (ADR-SLACKID-01 D6-D8), so nobody has to link Slack again after a
 * move to PostgreSQL. Links are their organisation's own rows: one naming a person who is not a copied user of that organisation is
 * reported, never stamped onto another organisation.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import { importSlackUserLinkDocs, slackUserLinkRow } from '../../../src/libs/repositories/slack-user-link/pg-import'
import { PgSlackUserLinkRepository } from '../../../src/libs/repositories/slack-user-link/pg'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'
import { REVERSE_MANUAL } from '../../../src/scripts/docstore-migrate/reverse'
import { SPECIAL } from '../../../src/scripts/docstore-migrate/reverse-special'
import { compareSlackLinks, slackLinkKey } from '../../../src/scripts/docstore-migrate/slack-links'
import { MONGO_COLLECTIONS } from '../../../src/scripts/docstore-migrate/mongo-collections'

const ORG_A = 'a'.repeat(24)
const ORG_B = 'b'.repeat(24)
const USER_A = 'c'.repeat(24)
const USER_B = 'd'.repeat(24)
const T = '2026-11-01T10:00:00.000Z'
type R = Record<string, any>

const link = (over: R = {}): R => ({
  _id: 'e'.repeat(24),
  orgId: ORG_A,
  workspaceId: 'E1',
  slackUserId: 'U1',
  teamId: 'T1',
  userId: USER_A,
  email: 'a@example.test',
  slackName: 'ada',
  linkedAt: T,
  createdAt: T,
  updatedAt: T,
  ...over,
})

/** Records the rows an INSERT would write; the users query answers with the copied users given. */
const capture = (users: Array<{ id: string; org_id: string }>) => {
  const writes: Record<string, R[]> = {}
  const sqls: string[] = []
  const tx = {
    async query(sql: string, params: unknown[] = []) {
      sqls.push(sql)
      const t = /INSERT INTO (\S+)/.exec(sql)
      if (t) {
        const rows = JSON.parse(params[0] as string) as R[]
        ;(writes[t[1]!] ??= []).push(...rows)
        return { rows: [], rowCount: rows.length }
      }
      if (sql.includes('import_link_users')) return { rows: users.filter((u) => (params[0] as string[]).includes(u.id)), rowCount: 0 }
      return { rows: [], rowCount: 0 }
    },
  } as never
  return { tx, writes, sqls }
}

describe('docstore-migrate: Slack account links (R40SLACK-FU-1)', () => {
  it('the collection is mapped (it was neither mapped nor listed as not copied, so a move dropped every link)', () => {
    expect(MONGO_COLLECTIONS).to.include('slack_user_links')
    const m = MAPPINGS.find((x) => x.collection === 'slack_user_links')
    expect(m, 'mapping').to.not.equal(undefined)
    expect(m).to.include({ repository: 'slack-user-link', table: 'integrations.slack_user_links', orgPath: 'orgId', orgCol: 'org_id', custom: true })
    // copied after the users it names
    expect(MAPPINGS.findIndex((x) => x.collection === 'users')).to.be.lessThan(MAPPINGS.indexOf(m!))
    // a rollback writes the links back (an unlink in PostgreSQL must not come back as a live link)
    expect(SPECIAL.has('slack_user_links')).to.equal(true)
    expect(REVERSE_MANUAL(m!)).to.equal(null)
  })

  it('the PostgreSQL repository is importable', () => {
    expect(new PgSlackUserLinkRepository().importBatch).to.be.a('function')
  })

  it('a link becomes its organisation\'s row; a person of another organisation, or one not copied, is reported, not linked', async () => {
    const { tx, writes } = capture([{ id: USER_A, org_id: ORG_A }, { id: USER_B, org_id: ORG_B }])
    const st = await importSlackUserLinkDocs(tx, 'slack_user_links', [
      link(),
      // the same Slack account linked in organisation B too: B's own row
      link({ _id: '1'.repeat(24), orgId: ORG_B, userId: USER_B }),
      // a link of organisation A to organisation B's person: never copied
      link({ _id: '2'.repeat(24), slackUserId: 'U2', userId: USER_B }),
      // a person who was not copied (erased)
      link({ _id: '3'.repeat(24), slackUserId: 'U3', userId: 'f'.repeat(24) }),
      // no organisation
      link({ _id: '4'.repeat(24), orgId: undefined }),
    ])
    const rows = writes['integrations.slack_user_links']!
    expect(rows.map((r) => [r.org_id, r.slack_user_id, r.user_id])).to.deep.equal([
      [ORG_A, 'U1', USER_A],
      [ORG_B, 'U1', USER_B],
    ])
    expect(rows[0]).to.include({ workspace_id: 'E1', team_id: 'T1', email: 'a@example.test', slack_name: 'ada', linked_at: T, version: 0, created_at: T, updated_at: T })
    expect(st.skipped.map((s) => s.id)).to.deep.equal(['4'.repeat(24), '2'.repeat(24), '3'.repeat(24)])
    expect(st.skipped.map((s) => s.reason)).to.include('Slack account link whose person is not a copied user of its organisation (not linked to anyone)')
  })

  it('a second copy offers the same rows (written ON CONFLICT DO NOTHING by the plan), and an unknown collection is refused', async () => {
    const one = capture([{ id: USER_A, org_id: ORG_A }])
    const two = capture([{ id: USER_A, org_id: ORG_A }])
    await importSlackUserLinkDocs(one.tx, 'slack_user_links', [link()])
    await importSlackUserLinkDocs(two.tx, 'slack_user_links', [link()])
    expect(two.writes).to.deep.equal(one.writes)
    expect(two.sqls.find((s) => s.includes('INSERT INTO'))).to.include('ON CONFLICT DO NOTHING')
    let msg = ''
    await importSlackUserLinkDocs(one.tx, 'users', []).catch((e: Error) => (msg = e.message))
    expect(msg).to.contain("does not import 'users'")
  })

  it('row: defaults for a raw document and the reasons a document cannot be a row', () => {
    const r = slackUserLinkRow(link({ slackName: undefined, createdAt: undefined, updatedAt: undefined, __v: 3 })) as R
    expect(r).to.include({ slack_name: '', created_at: T, updated_at: T, version: 3 })
    expect(slackUserLinkRow(link({ teamId: '' }))).to.be.a('string')
    expect(slackUserLinkRow(link({ email: 7 }))).to.be.a('string')
  })

  it('verify compares by the full key: the same Slack account in two organisations is two links, and a missing or extra one is a difference', () => {
    const m = MAPPINGS.find((x) => x.collection === 'slack_user_links')!
    const row = (over: R = {}): R => ({ org_id: ORG_A, workspace_id: 'E1', slack_user_id: 'U1', team_id: 'T1', user_id: USER_A, email: 'a@example.test', slack_name: 'ada', linked_at: T, version: 0, created_at: T, updated_at: T, ...over })
    const docs = [link({ __v: 0 }), link({ _id: '1'.repeat(24), orgId: ORG_B, userId: USER_B, __v: 0 })]
    const same = compareSlackLinks(m, docs, [row(), row({ org_id: ORG_B, user_id: USER_B })])
    expect(same.diffs).to.deep.equal([])
    expect(Object.fromEntries(same.perOrgSource)).to.deep.equal({ [ORG_A]: 1, [ORG_B]: 1 })
    const moved = compareSlackLinks(m, docs, [row(), row({ org_id: ORG_B, user_id: USER_A })])
    expect(moved.diffs.map((d) => [d.id, d.kind])).to.deep.equal([[`${ORG_B}/E1/U1`, 'differs']])
    const unlinked = compareSlackLinks(m, docs.slice(0, 1), [row(), row({ org_id: ORG_B, user_id: USER_B })])
    expect(unlinked.diffs.map((d) => d.kind)).to.deep.equal(['extra_in_pg'])
    expect(slackLinkKey({ orgId: ORG_A.toUpperCase(), workspaceId: 'E1', slackUserId: 'U1' })).to.equal(`${ORG_A}/E1/U1`)
  })

  it('R48MIGFIX-2: a link the copy refused and listed is reported as skipped, not missing; once it has a row it is compared again', () => {
    const m = MAPPINGS.find((x) => x.collection === 'slack_user_links')!
    const row = (over: R = {}): R => ({ org_id: ORG_A, workspace_id: 'E1', slack_user_id: 'U1', team_id: 'T1', user_id: USER_A, email: 'a@example.test', slack_name: 'ada', linked_at: T, version: 0, created_at: T, updated_at: T, ...over })
    const erased = link({ _id: '3'.repeat(24), slackUserId: 'U3', userId: 'f'.repeat(24), __v: 0 })
    const why = 'Slack account link whose person is not a copied user of its organisation (not linked to anyone)'
    // before the fix the listed refusal was a `missing_in_pg` difference, so verify (and every offline move) failed
    const unlisted = compareSlackLinks(m, [link({ __v: 0 }), erased], [row()])
    expect(unlisted.diffs.map((d) => [d.id, d.kind])).to.deep.equal([[`${ORG_A}/E1/U3`, 'missing_in_pg']])
    const listed = compareSlackLinks(m, [link({ __v: 0 }), erased], [row()], { ['3'.repeat(24)]: why })
    expect(listed.diffs).to.deep.equal([])
    expect(listed.skippedListed).to.deep.equal([{ id: '3'.repeat(24), reason: why }])
    expect(Object.fromEntries(listed.perOrgSource)).to.deep.equal({ [ORG_A]: 1 })
    // a listed link that has a row now (the follower copied it later) is compared like any other
    const later = compareSlackLinks(m, [erased], [row({ slack_user_id: 'U3', user_id: 'f'.repeat(24), slack_name: 'other' })], { ['3'.repeat(24)]: why })
    expect(later.skippedListed).to.deep.equal([])
    expect(later.diffs.map((d) => d.kind)).to.deep.equal(['differs'])
  })
})
