import 'reflect-metadata'
import { expect } from 'chai'
import { importConnectorsConfigDocs } from '../../../src/libs/repositories/connectors-config/pg-import'
import { importDelegationDocs } from '../../../src/libs/repositories/delegation/pg-import'
import { documentRow, importDocumentDocs } from '../../../src/libs/repositories/document/pg-import'
import { importMailDocs } from '../../../src/libs/repositories/mail-log/pg-import'
import { importNotificationDocs } from '../../../src/libs/repositories/notification/pg-import'
import { importOAuthAppDocs } from '../../../src/libs/repositories/oauth-app/pg-import'
import { importOAuthTokenDocs } from '../../../src/libs/repositories/oauth-token/pg-import'
import { importOutboxDocs, outboxIdOf } from '../../../src/libs/repositories/outbox/pg-import'
import { importProjectDocs, writeProjects } from '../../../src/libs/repositories/project/pg-import'
import { importSlackThreadDocs } from '../../../src/libs/repositories/slack-thread/pg-import'
import { OutboxIdCollision } from '../../../src/libs/repositories/outbox/pg-import'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'
import { SourceReport, prepareSource } from '../../../src/scripts/docstore-migrate/source'
import { compareDocs } from '../../../src/scripts/docstore-migrate/compare'
import { CONTENT_MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping/content'
import { contentDocs } from '../../scripts/docstore-migrate/fixtures/content'
import { sha256Hex } from '../../../src/libs/repositories/import'

const O = 'a'.repeat(24)
const U = 'b'.repeat(24)
const I = 'c'.repeat(24)
const T = '2026-01-02T03:04:05.000Z'

type R = Record<string, any>

/** A transaction that records the rows each INSERT would write; `answer` serves any other statement. */
const capture = (answer: (sql: string) => unknown[] = () => []) => {
  const writes: Record<string, R[]> = {}
  const tx = {
    async query(sql: string, params: unknown[] = []) {
      const t = /INSERT INTO (\S+)/.exec(sql)
      if (t) {
        const rows = JSON.parse(params[0] as string) as R[]
        ;(writes[t[1]!] ??= []).push(...rows)
        return { rows: [], rowCount: rows.length }
      }
      const rows = answer(sql)
      return { rows, rowCount: rows.length }
    },
  } as never
  return { tx, writes }
}

describe('content importers: documents become rows', () => {
  it('connector configuration: defaults and a skipped record', async () => {
    const { tx, writes } = capture()
    const st = await importConnectorsConfigDocs(tx, [
      { _id: I, orgId: O, lastUpdatedBy: U, name: 'slack', createdAt: T },
      { _id: I, orgId: O, name: 'no author', createdAt: T },
    ])
    const r = writes['platform.connectors_config']![0]!
    expect(r).to.include({ name: 'slack', is_enabled: true, last_updated_by: U, version: 0, updated_at: T })
    expect(st.inserted['platform.connectors_config']).to.equal(1)
    expect(st.skipped).to.have.length(1)
  })

  it('delegation: a grant, an event, and a refused collection name', async () => {
    const { tx, writes } = capture()
    const grant = { _id: I, orgId: O, serviceAccountId: U, subjectUserId: U, grantedBy: U, createdAt: T, reason: 'x' }
    await importDelegationDocs(tx, 'serviceAccountDelegations', [grant, { ...grant, grantedBy: undefined }])
    expect(writes['oauth.service_account_delegations']).to.have.length(1)
    expect(writes['oauth.service_account_delegations']![0]).to.include({ granted_by: U, reason: 'x' })
    const ev = { _id: I, orgId: O, serviceAccountId: U, createdAt: T, outcome: 'issued', scopes: ['a'] }
    const st = await importDelegationDocs(tx, 'delegationEvents', [ev, { ...ev, outcome: undefined }])
    expect(writes['oauth.delegation_events']![0]).to.include({ outcome: 'issued', requested_subject: '', subject_user_id: null })
    expect(st.skipped.map((s) => s.reason)).to.deep.equal(['delegation event without an outcome'])
    let err: Error | undefined
    await importDelegationDocs(tx, 'other', []).catch((e: Error) => (err = e))
    expect(err?.message).to.contain("does not import 'other'")
  })

  it('document: the connector tag, the creation time from the id, and a skipped record', async () => {
    const d = { _id: '00000064' + 'a'.repeat(16), orgId: O, documentName: 'n', storageVendor: 's3', customMetadata: [{ key: 'connectorId', value: 'c1' }] }
    const row = documentRow(d)!
    expect(row.connector_id).to.equal('c1')
    expect(row.created_at).to.equal(new Date(100 * 1000).toISOString())
    expect(row.initiator_unset).to.equal(true)
    expect(documentRow({ ...d, initiatorUserId: null })!.initiator_unset).to.equal(false)
    expect(documentRow({ ...d, customMetadata: [{ key: 'connectorId', value: 5 }] })!.connector_id).to.equal(null)
    expect(documentRow({ ...d, documentName: 1 })).to.equal(null)
    const { tx, writes } = capture()
    const st = await importDocumentDocs(tx, [d, { ...d, storageVendor: undefined }])
    expect(writes['content.documents']).to.have.length(1)
    expect(st.skipped).to.have.length(1)
  })

  it('mail log: only text addresses are kept', async () => {
    const { tx, writes } = capture()
    const d = { _id: I, orgId: O, from: 'a@x', emailTemplateType: 't', createdAt: T, to: ['b@x', 3, 'c@x'] }
    const st = await importMailDocs(tx, [d, { ...d, orgId: undefined }])
    expect(writes['platform.mail_log']![0]).to.include({ from_address: 'a@x', subject: null })
    expect(writes['platform.mail_log']![0]!.to_addresses).to.deep.equal(['b@x', 'c@x'])
    expect(writes['platform.mail_log']![0]!.cc_addresses).to.deep.equal([])
    expect(st.skipped).to.have.length(1)
  })

  it('notification: unread by default and not deleted', async () => {
    const { tx, writes } = capture()
    const d = { _id: I, orgId: O, assignedTo: U, type: 'info', createdAt: T }
    const st = await importNotificationDocs(tx, [d, { ...d, assignedTo: 'nope' }])
    expect(writes['platform.notifications']![0]).to.include({ status: 'unread', is_deleted: false, assigned_to: U, title: null })
    expect(st.skipped).to.have.length(1)
  })

  it('OAuth app: defaults, and the slug it is given or one made from the id', async () => {
    const { tx, writes } = capture()
    const d = { _id: I, orgId: O, createdBy: U, clientId: 'c', clientSecretEncrypted: 's', name: 'app', createdAt: T }
    await importOAuthAppDocs(tx, [d, { ...d, _id: U, slug: 'mine', knowledgeLimit: { apps: ['a'] } }, { ...d, name: 1 }])
    const [a, b] = writes['oauth.apps']!
    expect(a).to.include({ slug: `oauthapp-${I}`, status: 'active', is_confidential: true, access_token_lifetime: 3600, refresh_token_lifetime: 2592000 })
    expect(a!.allowed_grant_types).to.deep.equal(['authorization_code', 'refresh_token'])
    expect(a!.knowledge_limit).to.equal(null)
    expect(b).to.include({ slug: 'mine' })
    expect(b!.knowledge_limit).to.deep.equal({ apps: ['a'], recordGroups: [], records: [] })
  })

  it('OAuth tokens: access, refresh and authorization code rows', async () => {
    const { tx, writes } = capture()
    const base = { _id: I, orgId: O, clientId: 'c', expiresAt: T, createdAt: T, userId: U, scopes: ['s'] }
    await importOAuthTokenDocs(tx, 'oauthAccessTokens', [{ ...base, tokenHash: 'h', isRevoked: true, revokedBy: U }, { ...base, tokenHash: undefined }])
    expect(writes['oauth.access_tokens']).to.have.length(1)
    expect(writes['oauth.access_tokens']![0]).to.include({ token_hash: 'h', is_revoked: true, revoked_by: U })
    await importOAuthTokenDocs(tx, 'oauthRefreshTokens', [{ ...base, tokenHash: 'h2', rotationCount: 2 }, { ...base, tokenHash: 'h3', userId: 'x' }])
    expect(writes['oauth.refresh_tokens']).to.have.length(1)
    expect(writes['oauth.refresh_tokens']![0]).to.include({ rotation_count: 2, is_revoked: false })
    const st = await importOAuthTokenDocs(tx, 'authorizationCodes', [{ ...base, code: 'plain', redirectUri: 'u' }, { ...base, redirectUri: 'u' }])
    expect(writes['oauth.authorization_codes']![0]).to.include({ code_hash: sha256Hex('plain'), is_used: false })
    expect(st.skipped[0]!.reason).to.contain('authorization code without')
    let err: Error | undefined
    await importOAuthTokenDocs(tx, 'nope', []).catch((e: Error) => (err = e))
    expect(err?.message).to.contain("does not import 'nope'")
  })

  it('device authorizations: held by the install organisation, expired ones left out', async () => {
    const future = new Date(Date.now() + 3600_000).toISOString()
    const d = { _id: I, deviceCodeHash: 'h', userCode: 'u', clientId: 'c', expiresAt: future, knowledgeLimit: { records: ['r'] } }
    const withOrg = capture((sql) => (sql.includes('import.install_org') ? [{ org_id: O }] : []))
    const st = await importOAuthTokenDocs(withOrg.tx, 'oauthDeviceCodes', [d, { ...d, _id: U, expiresAt: '2000-01-01T00:00:00.000Z' }, { ...d, _id: U, userCode: 1 }])
    const r = withOrg.writes['oauth.device_authorizations']![0]!
    expect(r).to.include({ org_id: O, status: 'pending', poll_interval: 5, created_at: future })
    expect(r.limit_records).to.deep.equal(['r'])
    expect(st.skipped.map((s) => s.reason)).to.deep.equal(['device authorization has expired', 'device authorization without a usable _id, hash, code, client or expiry'])
    const none = capture()
    const st2 = await importOAuthTokenDocs(none.tx, 'oauthDeviceCodes', [d])
    expect(st2.skipped[0]!.reason).to.contain('no organisation')
  })

  it('outbox: ids keep the creation order, published events are history, publishing becomes pending', async () => {
    expect(outboxIdOf('00000000' + '0'.repeat(16))).to.equal('0')
    const hex = (sec: number, counter: string) => (1577836800 + sec).toString(16).padStart(8, '0') + '0'.repeat(10) + counter
    expect(Number(outboxIdOf(hex(2, '000001')))).to.equal(2 * 2 ** 21 + 1)
    const { tx, writes } = capture()
    const ev = (id: string, extra: R) => ({ _id: id, topic: 't', key: 'k', value: JSON.stringify({ payload: { orgId: O } }), createdAt: T, ...extra })
    const st = await importOutboxDocs(tx, [
      ev(hex(2, '000001'), { status: 'publishing' }),
      ev(hex(1, '000001'), { status: 'failed', attempts: 3, lastError: 'boom' }),
      ev(hex(3, '000001'), { status: 'published' }),
      ev(hex(4, '000001'), { value: 'not json', orderingKey: `user:${U}` }),
      ev(hex(5, '000001'), { value: 'not json' }),
    ])
    const rows = writes['platform.outbox']!
    expect(rows.map((r) => r.status)).to.deep.equal(['failed', 'pending', 'pending'])
    expect(rows.map((r) => r.org_id)).to.deep.equal([O, O, U])
    expect(rows[0]).to.include({ attempts: 3, last_error: 'boom' })
    expect(Number(rows[0]!.id)).to.be.lessThan(Number(rows[1]!.id))
    expect(st.skipped).to.have.length(2)
  })

  it('outbox: events that fold to one id stop the import, an exact twin does not, and the verify reports the pair', async () => {
    const base = 1577836800 + 400_000_000
    const hexA = base.toString(16).padStart(8, '0') + 'abcdef0123' + '000005'
    const hexB = hexA.slice(0, 18) + '800005'
    const ev = (id: string, key: string) => ({ _id: id, topic: 't', key, value: JSON.stringify({ payload: { orgId: O } }), createdAt: T })
    expect(outboxIdOf(hexA)).to.equal(outboxIdOf(hexB))
    // two different events in one batch
    let err: Error | undefined
    await importOutboxDocs(capture().tx, [ev(hexA, 'a'), ev(hexB, 'b')]).catch((e: Error) => (err = e))
    expect(err).to.be.instanceOf(OutboxIdCollision)
    expect(err!.message).to.contain(hexA).and.to.contain(hexB)
    // the same event twice is written once
    const twin = capture()
    await importOutboxDocs(twin.tx, [ev(hexA, 'a'), ev(hexB, 'a')])
    expect(twin.writes['platform.outbox']).to.have.length(1)
    // an id the table already holds for another event
    const held = capture((sql) => (sql.includes('outbox_held') ? [{ id: outboxIdOf(hexA), org_id: O, topic: 't', msg_key: 'other', ordering_key: 'global', value: 'v', created_at: T }] : []))
    err = undefined
    await importOutboxDocs(held.tx, [ev(hexB, 'b')]).catch((e: Error) => (err = e))
    expect(err?.message).to.contain(hexB).and.to.contain('already holds')
    const same = capture((sql) => (sql.includes('outbox_held') ? [{ id: outboxIdOf(hexA), org_id: O, topic: 't', msg_key: 'a', ordering_key: 'global', value: ev(hexA, 'a').value, created_at: '2026-01-02T03:04:05+00:00' }] : []))
    await importOutboxDocs(same.tx, [ev(hexA, 'a')])
    // an exact retry whose time has milliseconds: the table hands back a Date, the importer writes text
    const withMs = ev(hexA, 'a')
    withMs.createdAt = '2026-01-02T03:04:05.678Z'
    const heldMs = capture((sql) => (sql.includes('outbox_held') ? [{ id: outboxIdOf(hexA), org_id: O, topic: 't', msg_key: 'a', ordering_key: 'global', value: withMs.value, created_at: new Date('2026-01-02T03:04:05.678Z') }] : []))
    await importOutboxDocs(heldMs.tx, [withMs])
    // the verify: two source events with one table id, one row in PostgreSQL, is a difference
    const m = MAPPINGS.find((x) => x.collection === 'outbox_events')!
    const prepared = [ev(hexA, 'a'), ev(hexB, 'b')].map((d) => prepareSource(m, d, new SourceReport())!)
    expect(prepared[0]!._id).to.equal(prepared[1]!._id)
    const diffs = compareDocs(prepared, [prepared[0]!])
    expect(diffs).to.have.length(1)
    expect(diffs[0]!.kind).to.equal('missing_in_pg')
  })

  it('every content mapping prepares its sample documents, and the outbox mapping reads them as the importer does', () => {
    const docs = contentDocs()
    const rep = new SourceReport()
    for (const m of CONTENT_MAPPINGS) {
      const list = docs[m.collection] ?? []
      const prepared = list.map((d) => prepareSource(m, d, rep)).filter((d) => d !== null)
      expect(prepared.length, m.collection).to.be.greaterThan(0)
      expect(prepared.length, m.collection).to.be.at.most(list.length)
    }
    const m = CONTENT_MAPPINGS.find((x) => x.collection === 'outbox_events')!
    const out = (docs.outbox_events ?? []).map((d) => prepareSource(m, d, new SourceReport()))
    // the published event is history and one names no organisation; the others get the folded id and no claim
    expect(out.filter((d) => d === null)).to.have.length(2)
    for (const d of out) if (d) expect(d._id).to.match(/^\d+$/)
    expect(out.some((d) => d && d.status === 'pending' && d.claimedAt === undefined)).to.equal(true)
    expect(out.filter((d) => d && d.__org !== undefined).length).to.equal(out.filter((d) => d).length)
  })

  it('projects: members keep their position, duplicates and unusable members are reported', async () => {
    const d = {
      _id: I, orgId: O, userId: U, name: 'p', createdAt: T, lastActivityAt: 0, pinnedBy: [U, 'x'],
      members: [{ principalId: U, role: 'editor' }, { principalId: U }, { principalId: 'all_org', principalType: 'team' }, { principalId: '' }],
    }
    const { projects, members, skipped } = importProjectDocs([d, { ...d, name: 1 }, { ...d, knowledgeScope: [], metadata: { a: 1 }, members: undefined }])
    expect(projects).to.have.length(2)
    expect(projects[0]).to.include({ visibility: 'private', last_activity_at: new Date(0).toISOString() })
    expect(projects[0]!.pinned_by).to.deep.equal([U])
    expect(projects[0]!.knowledge_scope).to.deep.equal({ apps: [], kb: [], recordGroups: [], records: [] })
    expect(members.map((m) => [m.principal_type, m.principal_id, m.role, m.pos])).to.deep.equal([
      ['user', U, 'editor', 0],
      ['team', 'all_org', 'viewer', 2],
    ])
    expect(skipped.map((s) => s.reason)).to.include.members(['duplicate member', 'member without a usable principalId'])
    const { tx, writes } = capture()
    const st = await writeProjects(tx, [d])
    expect(writes['content.projects']).to.have.length(1)
    expect(writes['content.project_members']).to.have.length(2)
    expect(st.skipped).to.have.length(2)
  })

  it('Slack threads: the organisation of the copied conversation, and none for a link without one', async () => {
    const d = { _id: I, threadId: 't', botId: 'b', email: 'e@x', conversationId: U, createdAt: T }
    const { tx, writes } = capture((sql) => (sql.includes('import_thread_orgs') ? [{ id: U, org_id: O }] : []))
    const st = await importSlackThreadDocs(tx, [d, { ...d, conversationId: O }])
    expect(writes['integrations.slack_threads']).to.have.length(1)
    expect(writes['integrations.slack_threads']![0]).to.include({ org_id: O, thread_id: 't', conversation_id: U })
    expect(st.skipped).to.have.length(1)
    const none = await importSlackThreadDocs(capture().tx, [{ ...d, conversationId: 'not-an-id' }])
    expect(none.skipped).to.have.length(1)
  })
})
