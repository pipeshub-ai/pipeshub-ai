import { expect } from 'chai'
import { Params, fill, likeEscape, oidOf, oidsOf, unescapeRegex, newObjectId } from '../../../src/libs/repositories/pg-util'
import { countStatement, pageStatement, planOf } from '../../../src/libs/repositories/conversation/pg'
import { messageDto, sessionDto, storedCitationMetadata, storedFeedback, storedMessage, storedModelInfo } from '../../../src/libs/repositories/conversation/pg-rows'
import type { SessionListQuery } from '../../../src/libs/repositories/conversation'

const O = 'a'.repeat(24)
const U = 'b'.repeat(24)
const P = 'c'.repeat(24)

const base = (over: Partial<SessionListQuery> = {}): SessionListQuery => ({
  orgId: O, userId: U, scope: { kind: 'chat' }, owned: true, shared: true, archived: 'no', sort: { lastActivityAt: -1, _id: -1 }, skip: 5, limit: 20, ...over,
})

/** every parameter a statement is sent with is named in its text, and nothing is named that is not sent */
function parametersMatch(text: string, values: unknown[]): void {
  const named = [...new Set([...text.matchAll(/\$(\d+)/g)].map((m) => Number(m[1])))].sort((a, b) => a - b)
  expect(named, text).to.deep.equal(values.map((_, i) => i + 1))
}

describe('PostgreSQL session list statements', () => {
  const queries: Record<string, SessionListQuery> = {
    'own chats': base({ shared: false }),
    'shared with me': base({ owned: false }),
    'shared with me, agent chats included': base({ owned: false, sharedIncludesAgentChats: true }),
    'shared with me and a project': base({ owned: false, accessibleProjectIds: [P] }),
    'own and shared and a project': base({ accessibleProjectIds: [P] }),
    'search with content matches': base({ search: 'a\\.b', contentMatchIds: [P] }),
    'dates and share flag and project filter': base({ startDate: new Date(0), endDate: new Date(), isSharedFilter: true, projectFilter: { kind: 'project', id: P } }),
    'unassigned': base({ projectFilter: { kind: 'unassigned' } }),
    'archives only': base({ archived: 'only' }),
    'one by id': base({ id: P }),
    'agent chats': base({ scope: { kind: 'agent', agentKey: 'ag' }, archived: 'notTrue' }),
    'agent chats shared for writing, with a project': base({ scope: { kind: 'agent', agentKey: 'ag' }, sharedAccess: 'write', accessibleProjectIds: [P] }),
    'agent chats by any agent': base({ scope: { kind: 'agent' }, archived: 'any' }),
  }
  for (const [name, q] of Object.entries(queries)) {
    it(`${name}: the page and its count name exactly the parameters they are sent with, and put the org first`, () => {
      const page = pageStatement(q, true)
      expect(page, 'a page').to.not.equal(undefined)
      parametersMatch((page as { text: string }).text, (page as { values: unknown[] }).values)
      expect((page as { values: unknown[] }).values[0]).to.equal(O)
      const count = countStatement(q)
      parametersMatch(count.text, count.values)
      expect(count.values[0]).to.equal(O)
      for (const sql of [(page as { text: string }).text, count.text]) {
        expect(sql).to.match(/^\/\* pipeshub:conversation\.\w+ \*\//)
        expect(sql).to.not.include('${')
        expect(sql.match(/conversation\.chat_sessions s/g)?.length).to.be.greaterThan(0)
        expect(sql).to.not.match(/@\{/)
      }
    })
  }

  it('reaches agent chats only for a shared-with-me list that asks for them', () => {
    const sql = (q: SessionListQuery) => (pageStatement(q, true) as { text: string }).text
    expect(sql(base({ owned: false, sharedIncludesAgentChats: true }))).to.include("s.session_type IN ('chat', 'agent')")
    expect(sql(base({ owned: false }))).to.not.include("'agent'")
    expect(sql(base({ sharedIncludesAgentChats: true }))).to.not.include("'agent'")
  })

  it('reaches no session by an owner-less, member-less, project-less query', () => {
    expect(pageStatement(base({ owned: false, shared: false }), true)).to.equal(undefined)
    expect(planOf(base({ owned: false, shared: false }), new Params()).access).to.equal('FALSE')
  })

  it('reads each way of seeing a session in its own branch, never one OR across them', () => {
    const text = (pageStatement(base({ accessibleProjectIds: [P] }), true) as { text: string }).text
    expect(text.match(/UNION ALL/g)).to.have.length(2)
    expect(text).to.match(/LATERAL/)
    expect(text).to.not.match(/\bOR s\.user_id/)
  })

  it('keeps the document store’s order: missing first when ascending, last when descending, the id breaks ties in the sort direction', () => {
    const text = (pageStatement(base({ sort: { title: 1 } }), true) as { text: string }).text
    expect(text).to.include('title ASC NULLS FIRST, d.id ASC')
    const down = (pageStatement(base({ sort: { title: -1 } }), true) as { text: string }).text
    expect(down).to.include('title DESC NULLS LAST, d.id DESC')
  })
})

describe('PostgreSQL repository helpers', () => {
  it('new ids are 24 hex digits and increase', () => {
    const ids = Array.from({ length: 50 }, () => newObjectId())
    ids.forEach((i) => expect(i).to.match(/^[0-9a-f]{24}$/))
    expect([...ids].sort()).to.deep.equal(ids)
    expect(new Set(ids).size).to.equal(50)
  })

  it('an id outside the stored form matches nothing; upper case is the stored lower case', () => {
    expect(oidOf('nope')).to.equal(undefined)
    expect(oidOf('A'.repeat(24))).to.equal('a'.repeat(24))
    expect(oidsOf([O, 'x', U])).to.deep.equal([O, U])
  })

  it('a search the caller regex-escaped becomes literal LIKE text', () => {
    expect(unescapeRegex('a\\.b\\*\\\\')).to.equal('a.b*\\')
    expect(likeEscape('100%_a\\')).to.equal('100\\%\\_a\\\\')
  })

  it('fill replaces markers in a static statement', () => {
    expect(fill('SELECT @{A} FROM t WHERE @{B}', { A: 'x', B: 'y = $1' })).to.equal('SELECT x FROM t WHERE y = $1')
  })
})

describe('PostgreSQL conversation row shapes', () => {
  const row = (over: Record<string, unknown> = {}) => ({
    id: P, session_type: 'chat' as const, user_id: U, initiator: U, title: null, is_shared: false, share_link: null, is_deleted: false, deleted_by: null,
    is_archived: false, archived_by: null, last_activity_at: new Date(5), status: null, fail_reason: null, next_seq: 0, model_info: { chatMode: 'quick' },
    conversation_errors: [], metadata: null, agent_key: null, conversation_source: null, compacted_summary: null, compacted_at_turn_index: null,
    compacted_at_timestamp: null, project_id: null, project_visibility: null, version: 0, nulls: [], created_at: new Date(1), updated_at: new Date(2), shared_with: [],
    ...over,
  })

  it('omits a NULL column, keeps a field stored as an explicit null, and serves times as numbers where they were numbers', () => {
    const plain = sessionDto(row(), O, { doc: false, version: false }) as unknown as Record<string, unknown>
    for (const k of ['title', 'shareLink', 'archivedBy', 'failReason', 'projectId', 'agentKey', '__v', 'sessionType', 'nextSeq']) expect(plain, k).to.not.have.property(k)
    expect(plain.lastActivityAt).to.equal(5)
    const unarchived = sessionDto(row({ nulls: ['archived_by'] }), O, { doc: false, version: false }) as unknown as Record<string, unknown>
    expect(unarchived).to.have.property('archivedBy', null)
    const archived = sessionDto(row({ is_archived: true, archived_by: U }), O, { doc: true, version: true }) as unknown as Record<string, unknown>
    expect(archived.archivedBy).to.equal(U)
    expect(archived.__v).to.equal(0)
  })

  it('a document read gives maps where the document store did, a lean read gives objects', () => {
    const r = row({ metadata: { a: 1 }, conversation_errors: [{ _id: P, message: 'm', timestamp: '2026-01-01T00:00:00.000Z', metadata: { code: 'x' } }] })
    const doc = sessionDto(r, O, { doc: true, version: true }) as unknown as { metadata: Map<string, number>; conversationErrors: Array<{ timestamp: Date; metadata: Map<string, string> }> }
    expect(doc.metadata).to.be.instanceOf(Map)
    expect(doc.conversationErrors[0]?.timestamp).to.be.instanceOf(Date)
    expect(doc.conversationErrors[0]?.metadata.get('code')).to.equal('x')
    const lean = sessionDto(r, O, { doc: false, version: false }) as unknown as { metadata: unknown }
    expect(lean.metadata).to.deep.equal({ a: 1 })
  })

  it('a message is stored with the defaults the document schema applied', () => {
    const s = storedMessage({ messageType: 'bot_response', content: 'x' })
    expect(s.modelInfo).to.deep.equal({ chatMode: 'quick' })
    expect(s.appliedFilters).to.deep.equal({ apps: [], kb: [], recordGroups: [], records: [], recordsExact: [] })
    expect(s.metadata).to.equal(null)
    expect(storedModelInfo({ modelKey: 'k', modelName: null as never })).to.deep.equal({ modelKey: 'k', chatMode: 'quick' })
    const f = storedFeedback({ isHelpful: true })
    expect(f.source).to.equal('user')
    expect(f.categories).to.deep.equal([])
    expect(f.timestamp).to.be.a('number')
    const meta = storedCitationMetadata({ content: 'c', chunkIndex: 0, citationType: 't', metadata: { orgId: O, recordId: 'r', bounding_box: [{ x: 1, y: 2 }] } as never })
    expect(meta.recordVersion).to.equal(0)
    expect(meta.previewRenderable).to.equal(true)
    expect((meta.bounding_box as Array<{ _id: string }>)[0]?._id).to.match(/^[0-9a-f]{24}$/)
  })

  const msg = (over: Record<string, unknown> = {}) => ({
    id: P, session_id: O, seq: 1, message_type: 'bot_response' as const, content: 'c', content_format: 'MARKDOWN', status: null, confidence: null,
    citations: [], follow_up_questions: [], feedback: [], metadata: null, model_info: {}, applied_filters: {}, attachments: [], reference_data: [], tools: [],
    reasoning: [], parts: [], created_at: new Date(), updated_at: new Date(), ...over,
  })

  it('a message row maps to the document shape, details included', () => {
    const own = messageDto(msg({ tools: [{ toolName: 't' }] }), O)
    expect(own.dto.tools).to.have.length(1)
    expect(own.dto.referenceData).to.deep.equal([])
    expect(own.dto).to.not.have.property('authorUserId')
    expect(own.dto).to.not.have.property('detailsHidden')
  })
})
