import 'reflect-metadata'
import { expect } from 'chai'
import { citationRow, memberRows, messageRow, nullsOf, sessionRow } from '../../../src/libs/repositories/conversation/pg-import'
import { sha256Hex } from '../../../src/libs/repositories/import'

const O = 'a'.repeat(24)
const U = 'b'.repeat(24)
const U2 = 'd'.repeat(24)
const S = 'c'.repeat(24)
const T = '2026-01-02T03:04:05.000Z'

describe('conversation importers: documents become rows', () => {
  it('members: one row per user, the first place kept and the highest level given', () => {
    const rows = memberRows(S, [
      { userId: U, accessLevel: 'read', _id: 'e'.repeat(24) },
      { userId: U2 },
      { userId: U, accessLevel: 'write' },
      { accessLevel: 'write' },
      'junk',
    ])
    expect(rows.map((r) => [r.user_id, r.access_level, r.pos])).to.deep.equal([[U, 'write', 0], [U2, 'read', 1]])
    expect(rows[0]!.entry_id).to.equal('e'.repeat(24))
    // an entry without an id gets the same one on every run
    expect(rows[1]!.entry_id).to.equal(sha256Hex(`${S}:${U2}`).slice(0, 24))
    expect(memberRows(S, undefined)).to.deep.equal([])
  })

  it('nulls: only an explicit null archivedBy is listed', () => {
    expect(nullsOf({ archivedBy: null })).to.deep.equal(['archived_by'])
    expect(nullsOf({})).to.deep.equal([])
  })

  it('session: defaults, agent type, model info only when present, and the unusable', () => {
    const d = { _id: S, orgId: O, userId: U, initiator: U, createdAt: T, lastActivityAt: 0, nextSeq: 4, archivedBy: null }
    const r = sessionRow(d)!
    expect(r).to.include({ session_type: 'chat', is_shared: false, is_pinned: false, next_seq: 4, updated_at: T, version: 0 })
    expect(r.last_activity_at).to.equal(new Date(0).toISOString())
    expect(r.nulls).to.deep.equal(['archived_by'])
    expect(r).to.not.have.property('model_info')
    const agent = sessionRow({ ...d, sessionType: 'agent', isPinned: true, modelInfo: { m: 1 }, compactedAtTurnIndex: 3, metadata: [] })!
    expect(agent).to.include({ session_type: 'agent', is_pinned: true, compacted_at_turn_index: 3 })
    expect(agent.model_info).to.deep.equal({ m: 1 })
    expect(agent.metadata).to.equal(null)
    expect(sessionRow({ ...d, initiator: 'nope' })).to.equal(null)
  })

  it('message: needs a numeric seq and a type; lists default to empty', () => {
    const d = { _id: S, orgId: O, sessionId: U, seq: 2, messageType: 'user_query', createdAt: T }
    const r = messageRow(d)!
    expect(r).to.include({ seq: 2, content: '', session_id: U, updated_at: T })
    expect(r.citations).to.deep.equal([])
    expect(r.metadata).to.equal(null)
    expect(messageRow({ ...d, content: 'hi', metadata: { a: 1 } })).to.deep.include({ content: 'hi', metadata: { a: 1 } })
    expect(messageRow({ ...d, seq: '2' })).to.equal(null)
    expect(messageRow({ ...d, messageType: undefined })).to.equal(null)
  })

  it('citation: its organisation comes from its metadata', () => {
    const d = { _id: S, content: 'c', chunkIndex: 3, citationType: 't', metadata: { orgId: O, recordId: 'r1', recordName: 'n' }, createdAt: T }
    expect(citationRow(d)).to.include({ org_id: O, record_id: 'r1', record_name: 'n', chunk_index: 3, citation_type: 't' })
    expect(citationRow({ ...d, metadata: { orgId: O } })).to.equal(null)
    expect(citationRow({ ...d, metadata: undefined })).to.equal(null)
  })
})
