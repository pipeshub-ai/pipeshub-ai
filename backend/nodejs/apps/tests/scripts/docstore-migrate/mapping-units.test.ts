import 'reflect-metadata'
import { expect } from 'chai'
import { importActivityDocs } from '../../../src/libs/repositories/activity/pg-import'
import { importAuthConfigDocs } from '../../../src/libs/repositories/org-auth-config/pg-import'
import { importOrgDocs, orgLogoRow, orgRow } from '../../../src/libs/repositories/org/pg-import'
import { importPictureDocs } from '../../../src/libs/repositories/picture/pg-import'
import { importResetLinkDocs } from '../../../src/libs/repositories/reset-link/pg-import'
import { importSearchDocs } from '../../../src/libs/repositories/semantic-search/pg-import'
import { importCounterDocs } from '../../../src/libs/repositories/slug/pg-import'
import { importUserDocs, userRow } from '../../../src/libs/repositories/user/pg-import'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'
import { SourceReport, prepareSource, toNeutral } from '../../../src/scripts/docstore-migrate/source'
import { stripNulCounted } from '../../../src/scripts/docstore-migrate/canonical'
import { idFilter, idString } from '../../../src/scripts/docstore-migrate/mongo-read'
import { loadConfig } from '../../../src/scripts/docstore-migrate/config'
import { canon } from '../../../src/scripts/docstore-migrate/state'
import { sha256 } from '../../../src/scripts/docstore-migrate/decode'
import { identityDocs } from './fixtures/identity'
import { conversationDocs } from './fixtures/conversation'

const O = 'a'.repeat(24)
const U = 'b'.repeat(24)
const I = 'c'.repeat(24)
const T = '2026-01-02T03:04:05.000Z'
type R = Record<string, any>

/** A transaction that records the rows each INSERT would write; `answer` serves any other statement. */
const capture = (answer: (sql: string, p: unknown[]) => { rows?: unknown[]; rowCount?: number } = () => ({})) => {
  const writes: Record<string, R[]> = {}
  const tx = {
    async query(sql: string, params: unknown[] = []) {
      const t = /INSERT INTO (\S+)/.exec(sql)
      if (t && typeof params[0] === 'string' && params[0].startsWith('[')) {
        const rows = JSON.parse(params[0]) as R[]
        ;(writes[t[1]!] ??= []).push(...rows)
        return { rows: [], rowCount: rows.length }
      }
      const a = answer(sql, params)
      return { rows: a.rows ?? [], rowCount: a.rowCount ?? 0 }
    },
  } as never
  return { tx, writes }
}
const refused = async (p: Promise<unknown>): Promise<string> => {
  try {
    await p
  } catch (e) {
    return (e as Error).message
  }
  return ''
}

describe('identity importers: documents become rows', () => {
  it('organisation: the slug only when given, the logo needs an organisation, counters go to the slug importer', async () => {
    const d = { _id: O, domain: 'x.example', contactEmail: 'o@x', accountType: 'business', createdAt: T }
    expect(orgRow(d)).to.not.have.property('slug')
    expect(orgRow({ ...d, slug: 's' })).to.include({ slug: 's', org_id: O, is_deleted: false, updated_at: T })
    expect(() => orgRow({ ...d, _id: 'bad' })).to.throw('not an ObjectId')
    expect(orgLogoRow({ _id: I, orgId: O, logo: 'x' })).to.include({ org_id: O, id: I, logo: 'x', logo_url: null })
    expect(orgLogoRow({ _id: I })).to.equal(null)
    const { tx, writes } = capture()
    const st = await importOrgDocs(tx, 'org-logos', [{ _id: I, orgId: O }, { _id: U }])
    expect(writes['identity.org_logos']).to.have.length(1)
    expect(st.skipped).to.have.length(1)
    await importOrgDocs(tx, 'org', [d])
    expect(writes['identity.orgs']).to.have.length(1)
    const counters = await importOrgDocs(capture().tx, 'counters', [{ _id: I, name: 'Org', seq: 7 }])
    expect(counters.followUps).to.deep.equal([{ kind: 'seed_org_slug_seq', seq: 7 }])
    expect(await refused(importOrgDocs(tx, 'other', []))).to.contain("does not import 'other'")
  })

  it('counters: Org seeds the sequence, a known name seeds per-organisation counters, another is reported', async () => {
    const { tx } = capture((sql) => (sql.includes('slug_counters') ? { rowCount: 3 } : {}))
    const st = await importCounterDocs(tx, [{ _id: I, name: 'User', seq: 40 }, { _id: U, name: 'Mystery', seq: 1 }, { _id: O }])
    expect(st.inserted['identity.slug_counters']).to.equal(3)
    expect(st.skipped.map((s) => s.reason)).to.deep.equal(["counter 'Mystery' has no counterpart in PostgreSQL", "counter '" + O + "' has no counterpart in PostgreSQL"])
  })

  it('user: lower-cased address, defaults, and the documents that cannot be stored', async () => {
    const d = { _id: U, orgId: O, slug: 'user-1', email: 'Ada@X.Example', createdAt: T }
    expect(userRow(d)).to.include({ email: 'ada@x.example', kind: 'human', role: 'member', has_logged_in: false, version: 0, updated_at: T })
    expect(() => userRow({ ...d, orgId: undefined })).to.throw('no usable orgId')
    expect(() => userRow({ ...d, slug: 1 })).to.throw('no slug')
    const { tx, writes } = capture()
    await importUserDocs(tx, 'users', [d])
    expect(writes['identity.users']).to.have.length(1)
    expect(await refused(importUserDocs(tx, 'org', []))).to.contain("does not import 'org'")
  })

  it('activity, picture, reset link and sign-in configuration: rows, or a reason', async () => {
    const { tx, writes } = capture()
    const a = await importActivityDocs(tx, 'userActivities', [{ _id: I, orgId: O, userId: U, email: 'A@B', activityType: 'login', ipAddress: '1.1.1.1', createdAt: T }, { _id: U, activityType: 'login' }])
    expect(writes['identity.user_activities']![0]).to.include({ email: 'a@b', user_id: U })
    expect(a.skipped.map((s) => s.reason)).to.deep.equal(['activity without an organisation'])
    const p = await importPictureDocs(tx, 'user-dps', [{ _id: I, orgId: O, userId: U, pic: 'x' }, { _id: U, orgId: O }])
    expect(writes['identity.user_pictures']![0]).to.include({ pic: 'x', pic_url: null })
    expect(p.skipped).to.have.length(1)
    const r = await importResetLinkDocs(tx, 'usedPasswordResetLinks', [{ _id: I, orgId: O, linkHash: 'h', userId: U, expiresAt: T, createdAt: T }, { _id: U, linkHash: 'h' }])
    expect(writes['identity.used_reset_links']![0]).to.include({ link_hash: 'h', created_at: T })
    expect(r.skipped).to.have.length(1)
    await importAuthConfigDocs(tx, 'orgAuthConfig', [{ _id: I, orgId: O, removedSignInMethods: ['x', 2] }])
    expect(writes['identity.org_auth_config']![0]!.removed_sign_in_methods).to.deep.equal(['x', '2'])
    expect(await refused(importAuthConfigDocs(tx, 'orgAuthConfig', [{ _id: I }]))).to.contain('no usable orgId')
    for (const f of [importActivityDocs, importPictureDocs, importResetLinkDocs, importAuthConfigDocs])
      expect(await refused(f(tx, 'nope', []))).to.contain("does not import 'nope'")
  })

  it('saved searches: members, nulls and a document that cannot be stored', async () => {
    const { tx, writes } = capture()
    const d = { _id: I, orgId: O, userId: U, query: 'q', limit: 5, citationIds: [I, 'x'], archivedBy: null, sharedWith: [{ userId: O, accessLevel: 'write' }], createdAt: T }
    const st = await importSearchDocs(tx, 'searches', [d, { ...d, _id: U, limit: 'many' }])
    expect(writes['conversation.semantic_searches']![0]).to.include({ result_limit: 5, is_shared: false })
    expect(writes['conversation.semantic_searches']![0]!.citation_ids).to.deep.equal([I])
    expect(writes['conversation.semantic_searches']![0]!.nulls).to.deep.equal(['archived_by'])
    expect(writes['conversation.semantic_search_members']![0]).to.include({ search_id: I, user_id: O, access_level: 'write' })
    expect(st.skipped).to.have.length(1)
    expect(await refused(importSearchDocs(tx, 'x', []))).to.contain("does not import 'x'")
  })
})

describe('docstore-migrate source preparation', () => {
  it('every identity and chat mapping prepares its sample documents', () => {
    const docs = { ...identityDocs(), ...conversationDocs() }
    const rep = new SourceReport()
    for (const m of MAPPINGS) {
      const list = docs[m.collection] ?? []
      // credentials and counters are not copied document by document (newest-of-each-account rule, per-organisation counters)
      if (!list.length || ['userCredentials', 'counters'].includes(m.collection)) continue
      const prepared = list.map((x) => prepareSource(m, x, rep)).filter((x) => x !== null)
      expect(prepared.length, m.collection).to.be.greaterThan(0)
      for (const x of prepared) expect(String(x!._id).length, m.collection).to.be.greaterThan(0)
    }
    // the transforms the mappings list were applied and reported
    expect(rep.applied.size).to.be.greaterThan(0)
  })

  it('a report adds up and prints as JSON', () => {
    const a = new SourceReport()
    a.bump('x')
    a.nul.set('p', 1)
    a.skipped.set('why', 2)
    const b = new SourceReport()
    b.bump('x', 2)
    b.nul.set('p', 3)
    b.merge(a)
    expect(b.toJSON()).to.deep.equal({ nulStripped: { p: 4 }, transforms: { x: 3 }, skipped: { why: 2 } })
  })

  it('neutral form strips U+0000 and counts it by path', () => {
    const rep = new SourceReport()
    const doc = toNeutral({ _id: I, name: 'a\u0000b', list: [{ t: 'x\u0000\u0000' }] }, rep, 'users')
    expect(doc.name).to.equal('ab')
    expect(rep.nul.get('users.name')).to.equal(1)
    expect(rep.nul.get('users.list[].t')).to.equal(2)
    const counts = new Map<string, number>()
    expect(stripNulCounted({ k: 'v\u0000' }, counts)).to.deep.equal({ k: 'v' })
    expect(counts.get('k')).to.equal(1)
  })

  it('checkpoint ids: an ObjectId when 24 hex digits, otherwise the text; text is lower-case', () => {
    expect(idFilter(I).constructor.name).to.equal('ObjectId')
    expect(idFilter('abc')).to.equal('abc')
    expect(idString(I.toUpperCase())).to.equal(I)
  })

  it('the configuration names the variable that is missing; json is stored with keys in one order; hashes are SHA-256', () => {
    expect(() => loadConfig({})).to.throw('MONGO_URI is not set')
    expect(loadConfig({ MONGO_URI: 'mongodb://x' }).mongoUri).to.equal('mongodb://x')
    expect(canon({ b: 1, a: { d: 1, c: 2 } })).to.equal(canon({ a: { c: 2, d: 1 }, b: 1 }))
    expect(sha256('abc')).to.equal('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  })
})
