import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { addComputedFields } from '../../../../src/modules/enterprise_search/utils/utils'
import {
  searchArchivedConversations,
  getAgentConversationById,
  getConversationById,
  getSearchById,
  listAllArchivesConversation,
  searchHistory,
} from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import EnterpriseSemanticSearch from '../../../../src/modules/enterprise_search/schema/search.schema'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'

/**
 * A shared conversation, archive or saved search shows each viewer only the
 * citations, quoted excerpts and attachments they may read. The query service's
 * access check is stubbed at the AI-service command; everything else runs for real.
 */

const OWNER = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const VIEWER = 'cccccccccccccccccccccccc'
const ORG = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const SESSION = 'dddddddddddddddddddddddd'
const CIT_OK = 'eeeeeeeeeeeeeeeeeeeeeeee'
const CIT_NO = 'ffffffffffffffffffffffff'
const SECRET = 'SECRET-quarterly-numbers'

const appConfig: any = {
  aiBackend: 'http://localhost:8000',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
}

const chain = (value: unknown): any => {
  const q: any = {}
  for (const m of ['select', 'sort', 'skip', 'limit', 'populate', 'lean', 'session']) q[m] = sinon.stub().returns(q)
  q.exec = sinon.stub().resolves(value)
  q.then = (ok: (v: unknown) => unknown, bad?: (e: unknown) => unknown) => Promise.resolve(value).then(ok, bad)
  return q
}

const citation = (id: string, recordId: string, content: string) => ({
  _id: id,
  content,
  chunkIndex: 1,
  metadata: { recordId, recordName: `${recordId}.pdf`, webUrl: `https://x/${recordId}`, orgId: ORG },
})

const answer = () => ({
  messageType: 'bot_response',
  content: 'The answer [1][2]',
  citations: [
    { citationId: citation(CIT_OK, 'rec-ok', 'readable text'), excerpt: 'readable excerpt' },
    { citationId: citation(CIT_NO, 'rec-no', SECRET), excerpt: SECRET, context: SECRET },
  ],
  attachments: [
    { recordId: 'att-ok', recordName: 'ok.pdf' },
    { recordId: 'att-no', recordName: 'hidden-name.pdf' },
  ],
})

const R_OK = '11111111-1111-4111-8111-111111111111'
const R_NO = '22222222-2222-4222-8222-222222222222'
const TOOL_TEXT = 'tool-output-as-written'
const OK_NAME = 'Readable page'
const SECRET_URL = `https://x/${SECRET}`

/** A stored answer with a readable and an unreadable record in every structure that names one. */
const richAnswer = () => ({
  ...answer(),
  referenceData: [
    { name: OK_NAME, recordId: R_OK, id: 'PA-1', webUrl: 'https://x/ok' },
    { name: SECRET, recordId: R_NO, id: 'PA-2', webUrl: SECRET_URL },
    { name: `${SECRET}-unidentified`, webUrl: SECRET_URL },
  ],
  reasoning: [{ content: 'I looked at the pages' }],
  tools: [{ toolName: 'read', toolResult: { id: R_NO, title: TOOL_TEXT } }],
  appliedFilters: {
    records: [
      { id: R_OK, name: OK_NAME },
      { id: R_NO, name: SECRET },
    ],
    apps: [{ id: 'app-ok-not-checked', name: 'no id match' }],
  },
  followUpQuestions: [{ question: 'And the budget?', confidence: 'High' }],
  parts: [
    { type: 'text', content: 'visible answer text' },
    { type: 'reasoning', content: 'thinking prose' },
    { type: 'tool_call', toolName: 'read', status: 'completed', args: `{"record_id":"${R_NO}"}`, resultPreview: `{"id":"${R_NO}","title":"${TOOL_TEXT}"}`, resultSummary: TOOL_TEXT, resultView: { rows: [{ id: R_NO, name: TOOL_TEXT }] }, artifactId: R_NO },
    { type: 'sub_agent', roleName: 'worker', parts: [{ type: 'tool_call', toolName: 'read', resultPreview: TOOL_TEXT }] },
  ],
})

/** Everything except record references is as the owner sees it; references to R_NO are gone. */
const expectRecordsOnlyRedacted = (message: any, readableToo = true) => {
  const wire = JSON.stringify({ ...message, tools: undefined, parts: undefined })
  expect(wire).to.not.contain(SECRET)
  expect(wire).to.not.contain(R_NO)
  expect(message.content).to.equal('The answer [1][2]')
  expect(message.reasoning).to.deep.equal([{ content: 'I looked at the pages' }])
  expect(message.followUpQuestions).to.have.length(1)
  expect(message.parts[0]).to.deep.equal({ type: 'text', content: 'visible answer text' })
  expect(message.parts[1]).to.deep.equal({ type: 'reasoning', content: 'thinking prose' })
  const hidden = { redacted: true, reason: 'no_access', name: 'Restricted source' }
  expect(message.referenceData[1]).to.deep.equal(hidden)
  expect(message.referenceData[2], 'no id but a name and url: hidden').to.deep.equal(hidden)
  expect(message.appliedFilters.records[1]).to.deep.equal(hidden)
  // Tool steps and results are shown as written (per-record redaction of them is a follow-up).
  expect(message.tools).to.deep.equal(richAnswer().tools)
  expect(message.parts).to.deep.equal(richAnswer().parts)
  if (readableToo) {
    expect(message.referenceData[0].name).to.equal(OK_NAME)
    expect(message.appliedFilters.records[0].name).to.equal(OK_NAME)
  }
}

const req = (userId: string, extra: Record<string, unknown> = {}): any => ({
  headers: { authorization: 'Bearer t' },
  body: {},
  params: {},
  query: {},
  user: { userId, orgId: ORG },
  context: { requestId: 'r' },
  on: sinon.stub(),
  ...extra,
})

const res = (): any => {
  const r: any = { status: sinon.stub(), json: sinon.stub() }
  r.status.returns(r)
  return r
}

/** Stubs the access route: it names `readable` as readable; `null` makes it fail. Returns the calls seen. */
const askedUrls: string[] = []
const stubAccess = (readable: string[] | null) => {
  askedUrls.length = 0
  const asked: string[][] = []
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
    if (this.uri.endsWith('/chat/records/access')) {
      const body = this.body ? JSON.parse(this.body) : {}
      asked.push(body.recordIds ?? [])
      askedUrls.push(...(body.webUrls ?? []))
      if (readable === null) throw new Error('access service down')
      return { statusCode: 200, data: { accessible: readable.filter((r) => !r.startsWith('http')), accessibleUrls: readable.filter((r) => r.startsWith('http')) }, msg: '' } as any
    }
    return { statusCode: 200, data: {}, msg: '' } as any // the unrelated permission sync
  })
  return asked
}

const stubConversation = (messages: unknown[]) => {
  sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
  sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false)
  sinon.stub(Users, 'find').returns(chain([]))
  sinon.stub(ChatSession, 'findOne').returns(
    chain({ _id: SESSION, title: 't', initiator: OWNER, isShared: true, sharedWith: [{ userId: VIEWER }], status: 'complete' }),
  )
  sinon.stub(ChatSessionMessage, 'countDocuments').resolves(messages.length)
  sinon.stub(ChatSessionMessage, 'find').returns(chain(messages))
}

const fetchConversation = async (userId: string) => {
  const r = res()
  const next = sinon.stub()
  await getConversationById(appConfig)(req(userId, { params: { conversationId: SESSION } }), r, next)
  sinon.assert.notCalled(next)
  return r.json.firstCall.args[0].conversation.messages[0]
}

describe('shared chats: per-viewer citations', () => {
  afterEach(() => sinon.restore())

  it('a viewer gets a placeholder for each citation, excerpt and attachment they cannot read', async () => {
    const asked = stubAccess(['rec-ok', 'att-ok'])
    stubConversation([answer()])
    const message = await fetchConversation(VIEWER)

    expect(message.citations).to.have.length(2)
    expect(message.citations[0].citationData.content).to.equal('readable text')
    expect(message.citations[1]).to.deep.equal({ redacted: true, reason: 'no_access' })
    expect(message.attachments).to.deep.equal([{ recordId: 'att-ok', recordName: 'ok.pdf' }])
    const wire = JSON.stringify(message)
    for (const leaked of [SECRET, 'rec-no', 'att-no', 'hidden-name', CIT_NO]) expect(wire).to.not.contain(leaked)
    expect(asked.flat().sort()).to.deep.equal(['att-no', 'att-ok', 'rec-no', 'rec-ok'])
  })

  it('keeps every field and hides only the references to records the viewer cannot read', async () => {
    stubAccess(['rec-ok', R_OK])
    stubConversation([richAnswer()])
    expectRecordsOnlyRedacted(await fetchConversation(VIEWER))
  })

  it('reference data with a vendor id is shown when the record at its address is readable', async () => {
    stubAccess(['https://jira/PA-1'])
    const rd = (id: string, webUrl?: string) => ({ name: `Ticket ${id}`, id, ...(webUrl && { webUrl }) })
    stubConversation([{ ...answer(), referenceData: [rd('PA-1', 'https://jira/PA-1'), rd('PA-2', 'https://jira/PA-2'), rd('PA-3'), rd('PA-4', 'https://jira/unresolved')] }])
    const message = await fetchConversation(VIEWER)

    expect(message.referenceData[0].name).to.equal('Ticket PA-1')
    for (const i of [1, 2, 3]) expect(message.referenceData[i]).to.deep.equal({ redacted: true, reason: 'no_access', name: 'Restricted source' })
    expect(askedUrls.sort()).to.deep.equal(['https://jira/PA-1', 'https://jira/PA-2', 'https://jira/unresolved'])
  })

  it('a readable vendor id or recordId never admits an entry whose url is unreadable (Q3a)', async () => {
    stubAccess(['https://x/ok', R_OK])
    stubConversation([{ ...answer(), referenceData: [{ name: SECRET, id: R_OK, webUrl: 'https://x/hidden' }] }])
    const message = await fetchConversation(VIEWER)
    expect(JSON.stringify(message.referenceData)).to.not.contain(SECRET)
  })

  it('a readable url is not a readable id, and a vendor id that equals one is ignored (Q3b)', async () => {
    stubAccess(['https://x/ok'])
    stubConversation([{ ...answer(), referenceData: [
      { name: 'ok', id: 'PA-1', webUrl: 'https://x/ok' },
      { name: SECRET, id: 'https://x/ok', webUrl: 'https://x/hidden' },
      { name: `${SECRET}-by-record`, recordId: 'https://x/ok' },
    ] }])
    const message = await fetchConversation(VIEWER)
    expect(message.referenceData[0].name).to.equal('ok')
    expect(JSON.stringify(message.referenceData)).to.not.contain(SECRET)
  })

  it('vendor-id reference data stays hidden when the access check fails', async () => {
    stubAccess(null)
    stubConversation([{ ...answer(), referenceData: [{ name: 'Ticket', id: 'PA-1', webUrl: 'https://jira/PA-1' }] }])
    const message = await fetchConversation(VIEWER)
    expect(message.referenceData[0].name).to.equal('Restricted source')
  })

  it('a failed access check hides every record reference but leaves the rest of the chat', async () => {
    stubAccess(null)
    stubConversation([richAnswer()])
    const message = await fetchConversation(VIEWER)
    expectRecordsOnlyRedacted(message, false)
    expect(message.referenceData[0].redacted).to.equal(true)
    expect(message.reasoning).to.have.length(1)
  })

  it('the owner still gets every field and reference', async () => {
    stubAccess([])
    stubConversation([richAnswer()])
    const message = await fetchConversation(OWNER)
    expect(message.parts[2].resultPreview).to.contain(TOOL_TEXT)
    expect(message.referenceData[1].name).to.equal(SECRET)
  })

  it('an agent conversation opened by a project member or shared user is redacted too', async () => {
    stubAccess(['rec-ok', 'att-ok', R_OK])
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
    sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false)
    sinon.stub(Users, 'find').returns(chain([]))
    sinon.stub(ChatSession, 'findOne').returns(chain({ _id: SESSION, orgId: ORG, title: 't', initiator: OWNER, isShared: true, sharedWith: [], status: 'complete' }))
    sinon.stub(ChatSessionMessage, 'countDocuments').resolves(1)
    sinon.stub(ChatSessionMessage, 'find').returns(chain([richAnswer()]))
    const r = res()
    const next = sinon.stub()
    await getAgentConversationById(appConfig)(req(VIEWER, { params: { conversationId: SESSION, agentKey: 'agent-1' } }), r, next)

    sinon.assert.notCalled(next)
    const message = r.json.firstCall.args[0].conversation.messages[0]
    expectRecordsOnlyRedacted(message)
    expect(message.citations[1]).to.deep.equal({ redacted: true, reason: 'no_access' })
    expect(message.attachments).to.deep.equal([{ recordId: 'att-ok', recordName: 'ok.pdf' }])
  })

  it('the owner sees everything and no access check is made', async () => {
    const asked = stubAccess([])
    stubConversation([answer()])
    const message = await fetchConversation(OWNER)

    expect(message.citations[1].citationData.content).to.equal(SECRET)
    expect(message.attachments[1].recordId).to.equal('att-no')
    expect(asked).to.have.length(0)
  })

  it('redacts everything when the access check fails', async () => {
    stubAccess(null)
    stubConversation([answer()])
    const message = await fetchConversation(VIEWER)

    expect(message.citations).to.deep.equal([{ redacted: true, reason: 'no_access' }, { redacted: true, reason: 'no_access' }])
    expect(message.attachments).to.deep.equal([])
    expect(JSON.stringify(message)).to.not.contain(SECRET)
  })

  it('an archive redacts record references in another user\'s messages', async () => {
    stubAccess(['rec-ok', R_OK])
    const archived = { _id: SESSION, title: 't', initiator: OWNER, isShared: true, sharedWith: [{ userId: VIEWER }], updatedAt: new Date(), archivedBy: OWNER }
    sinon.stub(ChatSession, 'find').returns(chain([archived]))
    sinon.stub(ChatSession, 'countDocuments').resolves(1)
    sinon.stub(ChatSessionMessage, 'find').returns(chain([{ sessionId: SESSION, ...richAnswer(), citations: [], attachments: [] }]))
    const r = res()
    await listAllArchivesConversation(appConfig)(req(VIEWER), r, sinon.stub())

    const message = r.json.firstCall.args[0].conversations[0].messages[0]
    expectRecordsOnlyRedacted(message)
  })

  it('an archive of another user\'s chat resolves stored citation ids and redacts the same way', async () => {
    stubAccess(['rec-ok'])
    const archived = { _id: SESSION, title: 't', initiator: OWNER, isShared: true, sharedWith: [{ userId: VIEWER }], updatedAt: new Date(), archivedBy: OWNER }
    sinon.stub(ChatSession, 'find').returns(chain([archived]))
    sinon.stub(ChatSession, 'countDocuments').resolves(1)
    // Archives read messages unpopulated: citations are bare ids with the quoted excerpt beside them.
    sinon.stub(ChatSessionMessage, 'find').returns(
      chain([
        {
          sessionId: SESSION,
          messageType: 'bot_response',
          citations: [{ citationId: CIT_OK, excerpt: 'readable excerpt' }, { citationId: CIT_NO, excerpt: SECRET, context: SECRET }],
          attachments: [],
        },
      ]),
    )
    sinon.stub(Citation, 'find').returns(
      chain([
        { _id: CIT_OK, metadata: { recordId: 'rec-ok' } },
        { _id: CIT_NO, metadata: { recordId: 'rec-no' } },
      ]),
    )
    const r = res()
    const next = sinon.stub()
    await listAllArchivesConversation(appConfig)(req(VIEWER), r, next)

    sinon.assert.notCalled(next)
    const [citations] = [r.json.firstCall.args[0].conversations[0].messages[0].citations]
    expect(citations[0].excerpt).to.equal('readable excerpt')
    expect(citations[1]).to.deep.equal({ redacted: true, reason: 'no_access' })
    expect(JSON.stringify(citations)).to.not.contain(SECRET)
  })

  it('a saved search shared with a viewer hides hits and records they cannot read', async () => {
    stubAccess(['rec-ok'])
    const search = {
      _id: 's1',
      userId: OWNER,
      query: 'q',
      citationIds: [citation(CIT_OK, 'rec-ok', 'readable text'), citation(CIT_NO, 'rec-no', SECRET)],
      records: { 'rec-ok': '{"name":"ok"}', 'rec-no': `{"name":"${SECRET}"}` },
    }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    const r = res()
    await getSearchById(appConfig)(req(VIEWER, { params: { searchId: SESSION } }), r, sinon.stub())

    const [out] = r.json.firstCall.args[0]
    expect(out.citationIds[0].content).to.equal('readable text')
    expect(out.citationIds[1]).to.deep.equal({ redacted: true, reason: 'no_access' })
    expect(Object.keys(out.records)).to.deep.equal(['rec-ok'])
    expect(JSON.stringify(out)).to.not.contain(SECRET)
  })

  it('a saved-search hit without metadata is redacted, not passed through', async () => {
    stubAccess(['rec-ok'])
    const search = { _id: 's1', userId: OWNER, query: 'q', citationIds: [{ _id: CIT_NO, content: SECRET }, null, citation(CIT_OK, 'rec-ok', 'readable text')], records: {} }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    const r = res()
    await getSearchById(appConfig)(req(VIEWER, { params: { searchId: SESSION } }), r, sinon.stub())

    const [out] = r.json.firstCall.args[0]
    expect(out.citationIds[0]).to.deep.equal({ redacted: true, reason: 'no_access' })
    expect(out.citationIds[1]).to.deep.equal({ redacted: true, reason: 'no_access' })
    expect(out.citationIds[2].content).to.equal('readable text')
  })

  it('an unpopulated citation id of a shared search is redacted, not passed through', async () => {
    stubAccess(['rec-ok'])
    const bare = new Types.ObjectId()
    const search = { _id: 's1', userId: OWNER, query: 'q', citationIds: [bare, citation(CIT_OK, 'rec-ok', 'readable text')], records: {} }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    sinon.stub(EnterpriseSemanticSearch, 'countDocuments').resolves(1)
    const r = res()
    await searchHistory(appConfig)(req(VIEWER), r, sinon.stub())

    const [out] = r.json.firstCall.args[0].searchHistory
    expect(out.citationIds).to.have.length(1)
    expect(out.citationIds[0].content).to.equal('readable text')
  })

  it('archive search leaves owner-only fields out for a user the chat is shared with', async () => {
    const owned = { _id: SESSION, userId: OWNER, initiator: OWNER, title: 't', isShared: true, sharedWith: [{ userId: VIEWER, accessLevel: 'read' }], failReason: SECRET, compactedSummary: SECRET, conversationErrors: [{ message: SECRET }], source: 'assistant' }
    sinon.stub(ChatSessionMessage, 'aggregate').resolves([])
    sinon.stub(ChatSession, 'aggregate').resolves([owned] as never)
    sinon.stub(ChatSession, 'countDocuments').resolves(1)
    sinon.stub(Users, 'find').returns(chain([]))
    const run = async (userId: string) => {
      const r = res()
      const next = sinon.stub()
      await searchArchivedConversations(appConfig)(req(userId, { query: { search: 't' } }), r, next)
      sinon.assert.notCalled(next)
      return r.json.firstCall.args[0].conversations[0]
    }
    expect(JSON.stringify(await run(VIEWER))).to.not.contain(SECRET)
    expect((await run(OWNER)).failReason).to.equal(SECRET)
  })

  it('archived list: a user the chat is shared with gets no owner-only fields, the owner does', async () => {
    stubAccess([])
    const archived = { _id: SESSION, userId: OWNER, initiator: OWNER, title: 't', isShared: true, sharedWith: [{ userId: VIEWER }], updatedAt: new Date(), archivedBy: OWNER, failReason: SECRET, compactedSummary: SECRET, conversationErrors: [{ message: SECRET }] }
    sinon.stub(ChatSession, 'find').returns(chain([archived]))
    sinon.stub(ChatSession, 'countDocuments').resolves(1)
    sinon.stub(ChatSessionMessage, 'find').returns(chain([]))
    const run = async (userId: string) => {
      const r = res()
      await listAllArchivesConversation(appConfig)(req(userId), r, sinon.stub())
      return r.json.firstCall.args[0].conversations[0]
    }
    expect(JSON.stringify(await run(VIEWER))).to.not.contain(SECRET)
    expect((await run(OWNER)).failReason).to.equal(SECRET)
  })

  it('every list that adds computed fields keeps owner-only fields for the owner only', () => {
    const c = { initiator: OWNER, sharedWith: [], failReason: SECRET, compactedSummary: SECRET, conversationErrors: [{ message: SECRET }] }
    expect(JSON.stringify(addComputedFields(c as any, VIEWER))).to.not.contain(SECRET)
    expect(addComputedFields(c as any, OWNER).failReason).to.equal(SECRET)
  })

  it('the history of a shared search leaves out bare hit ids, keeping the spec\'s id-string array valid', async () => {
    stubAccess(['rec-ok'])
    const search = { _id: 's1', userId: OWNER, query: 'q', citationIds: [new Types.ObjectId(), citation(CIT_NO, 'rec-no', SECRET)], records: {} }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    sinon.stub(EnterpriseSemanticSearch, 'countDocuments').resolves(1)
    const r = res()
    await searchHistory(appConfig)(req(VIEWER), r, sinon.stub())

    const [out] = r.json.firstCall.args[0].searchHistory
    expect(out.citationIds).to.deep.equal([{ redacted: true, reason: 'no_access' }])
  })

  it('the search history lists hide unreadable records of a search shared with the viewer', async () => {
    stubAccess([])
    const search = { _id: 's1', userId: OWNER, query: 'q', citationIds: [CIT_NO], records: { 'rec-no': `{"name":"${SECRET}"}` } }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    sinon.stub(EnterpriseSemanticSearch, 'countDocuments').resolves(1)
    const r = res()
    await searchHistory(appConfig)(req(VIEWER), r, sinon.stub())

    expect(JSON.stringify(r.json.firstCall.args[0].searchHistory)).to.not.contain(SECRET)
  })

  it('a search the viewer owns is returned untouched', async () => {
    const asked = stubAccess([])
    const search = { _id: 's1', userId: VIEWER, citationIds: [citation(CIT_NO, 'rec-no', SECRET)], records: { 'rec-no': 'x' } }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    const r = res()
    await getSearchById(appConfig)(req(VIEWER, { params: { searchId: SESSION } }), r, sinon.stub())

    expect(r.json.firstCall.args[0][0]).to.equal(search)
    expect(asked).to.have.length(0)
  })
})
