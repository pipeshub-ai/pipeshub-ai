import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { readableRecordIds, historyForModel, viewerOf, newReadable, redactMessagesFor, redactConversationFor } from '../../../../src/modules/enterprise_search/utils/viewer-redaction'
import { addComputedFields, formatPreviousConversations } from '../../../../src/modules/enterprise_search/utils/utils'
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
import { MongoConversationRepository } from '../../../../src/libs/repositories/conversation/mongo'
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
const stubAccess = (readable: string[] | null, unmatched: string[] = []) => {
  askedUrls.length = 0
  const asked: string[][] = []
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
    if (this.uri.endsWith('/chat/records/access')) {
      const body = this.body ? JSON.parse(this.body) : {}
      asked.push(body.recordIds ?? [])
      askedUrls.push(...(body.webUrls ?? []))
      if (readable === null) throw new Error('access service down')
      return { statusCode: 200, data: { accessible: readable.filter((r) => !r.startsWith('http')), accessibleUrls: readable.filter((r) => r.startsWith('http')), unmatchedUrls: unmatched }, msg: '' } as any
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
  sinon.stub(ChatSession, 'find').returns(chain([]))
  sinon.stub(ChatSessionMessage, 'countDocuments').resolves(messages.length)
  sinon.stub(ChatSessionMessage, 'find').returns(chain(messages))
}

/**
 * Per-record redaction applies to every message of another user. Answers that used a lost record
 * are hidden whole (tests below), so these use a question turn, where per-record redaction shows.
 */
const stubPeerConversation = (messages: unknown[]) =>
  stubConversation(messages.map((m) => ({ ...(m as object), messageType: 'user_query' })))

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
    stubPeerConversation([answer()])
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
    stubPeerConversation([richAnswer()])
    expectRecordsOnlyRedacted(await fetchConversation(VIEWER))
  })

  it('reference data with a vendor id is shown when the record at its address is readable', async () => {
    stubAccess(['https://jira/PA-1'])
    const rd = (id: string, webUrl?: string) => ({ name: `Ticket ${id}`, id, ...(webUrl && { webUrl }) })
    stubPeerConversation([{ ...answer(), referenceData: [rd('PA-1', 'https://jira/PA-1'), rd('PA-2', 'https://jira/PA-2'), rd('PA-3'), rd('PA-4', 'https://jira/unresolved')] }])
    const message = await fetchConversation(VIEWER)

    expect(message.referenceData[0].name).to.equal('Ticket PA-1')
    for (const i of [1, 2, 3]) expect(message.referenceData[i]).to.deep.equal({ redacted: true, reason: 'no_access', name: 'Restricted source' })
    expect(askedUrls.sort()).to.deep.equal(['https://jira/PA-1', 'https://jira/PA-2', 'https://jira/unresolved'])
  })

  it('a readable vendor id or recordId never admits an entry whose url is unreadable (Q3a)', async () => {
    stubAccess(['https://x/ok', R_OK])
    stubPeerConversation([{ ...answer(), referenceData: [{ name: SECRET, id: R_OK, webUrl: 'https://x/hidden' }] }])
    const message = await fetchConversation(VIEWER)
    expect(JSON.stringify(message.referenceData)).to.not.contain(SECRET)
  })

  it('a readable url is not a readable id, and a vendor id that equals one is ignored (Q3b)', async () => {
    stubAccess(['https://x/ok'])
    stubPeerConversation([{ ...answer(), referenceData: [
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
    stubPeerConversation([{ ...answer(), referenceData: [{ name: 'Ticket', id: 'PA-1', webUrl: 'https://jira/PA-1' }] }])
    const message = await fetchConversation(VIEWER)
    expect(message.referenceData[0].name).to.equal('Restricted source')
  })

  it('a failed access check hides every record reference but leaves the rest of the chat', async () => {
    stubAccess(null)
    stubPeerConversation([richAnswer()])
    const message = await fetchConversation(VIEWER)
    expectRecordsOnlyRedacted(message, false)
    expect(message.referenceData[0].redacted).to.equal(true)
    expect(message.reasoning).to.have.length(1)
  })

  it('the owner still gets every field and reference', async () => {
    stubAccess([])
    stubPeerConversation([richAnswer()])
    const message = await fetchConversation(OWNER)
    expect(message.parts[2].resultPreview).to.contain(TOOL_TEXT)
    expect(message.referenceData[1].name).to.equal(SECRET)
  })

  it('a non-200 reply from the access check redacts everything, even if it lists records', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
      return this.uri.endsWith('/chat/records/access')
        ? ({ statusCode: 503, data: { accessible: ['rec-no', 'att-no'], accessibleUrls: ['https://x/rec-no'] } } as any)
        : ({ statusCode: 200, data: {} } as any)
    })
    stubPeerConversation([{ ...answer(), referenceData: [{ name: SECRET, id: 'v-1', webUrl: 'https://x/rec-no' }] }])
    const message = await fetchConversation(VIEWER)
    expect(message.citations).to.deep.equal([{ redacted: true, reason: 'no_access' }, { redacted: true, reason: 'no_access' }])
    expect(message.attachments).to.deep.equal([])
    expect(JSON.stringify(message)).to.not.contain(SECRET)
  })

  it('a malformed access reply (accessible is a string) admits nothing', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { accessible: 'r', accessibleUrls: ['https://x/r'] } } as any)
    const readable = await readableRecordIds(appConfig, { userId: VIEWER, orgId: ORG }, ['r'], ['https://x/r'])
    expect(readable.ids.size).to.equal(0)
    expect(readable.urls.size).to.equal(0)
  })

  it('an access reply whose list holds a non-string entry admits nothing', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { accessible: [['rec-no'], 'rec-ok'], accessibleUrls: [] } } as any)
    const readable = await readableRecordIds(appConfig, { userId: VIEWER, orgId: ORG }, ['rec-no', 'rec-ok'])
    expect(readable.ids.size).to.equal(0)
  })

  it('access checks are batched at 500 ids per call and ids are checked once', async () => {
    const asked = stubAccess([])
    const ids = Array.from({ length: 1203 }, (_, i) => `r${i % 1100}`)
    await readableRecordIds(appConfig, { userId: VIEWER, orgId: ORG }, ids)
    expect(asked.map((a) => a.length)).to.deep.equal([500, 500, 100])
    expect(new Set(asked.flat()).size).to.equal(1100)
  })

  it('a citation whose document was deleted hides the answer as unconfirmed, never as record-free', async () => {
    stubAccess(['rec-ok'])
    stubConversation([{ messageType: 'bot_response', content: SECRET, citations: [{ citationId: null, excerpt: SECRET }] }])
    const message = await fetchConversation(VIEWER)
    expect(message.answerHidden).to.deep.equal({ redacted: true, reason: 'access_check_unavailable' })
    expect(JSON.stringify(message)).to.not.contain(SECRET)
  })

  it('the owner is recognised when the chat stores its initiator as an ObjectId', async () => {
    const asked = stubAccess([])
    stubPeerConversation([answer()])
    ;(ChatSession.findOne as sinon.SinonStub).returns(
      chain({ _id: SESSION, title: 't', initiator: new Types.ObjectId(OWNER), isShared: true, sharedWith: [{ userId: VIEWER }], status: 'complete' }),
    )
    const message = await fetchConversation(OWNER)
    expect(message.citations[1].citationData.content).to.equal(SECRET)
    expect(asked.length, 'the question\'s files and filters are checked for its answer').to.be.at.most(1)
  })

  it('an agent conversation opened by a project member or shared user is redacted too', async () => {
    stubAccess(['rec-ok', 'att-ok', R_OK])
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
    sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false)
    sinon.stub(Users, 'find').returns(chain([]))
    sinon.stub(ChatSession, 'findOne').returns(chain({ _id: SESSION, orgId: ORG, title: 't', initiator: OWNER, isShared: true, sharedWith: [], status: 'complete' }))
    sinon.stub(ChatSession, 'find').returns(chain([]))
    sinon.stub(ChatSessionMessage, 'countDocuments').resolves(1)
    sinon.stub(ChatSessionMessage, 'find').returns(chain([{ ...richAnswer(), messageType: 'user_query' }]))
    const r = res()
    const next = sinon.stub()
    await getAgentConversationById(appConfig)(req(VIEWER, { params: { conversationId: SESSION, agentKey: 'agent-1' } }), r, next)

    sinon.assert.notCalled(next)
    const message = r.json.firstCall.args[0].conversation.messages[0]
    expectRecordsOnlyRedacted(message)
    expect(message.citations[1]).to.deep.equal({ redacted: true, reason: 'no_access' })
    expect(message.attachments).to.deep.equal([{ recordId: 'att-ok', recordName: 'ok.pdf' }])
  })

  it('the owner\'s own question is passed through untouched', async () => {
    stubAccess([])
    stubPeerConversation([answer()])
    const message = await fetchConversation(OWNER)

    expect(message.citations[1].citationData.content).to.equal(SECRET)
    expect(message.attachments[1].recordId).to.equal('att-no')
    expect(message.answerHidden).to.equal(undefined)
  })

  it('redacts everything when the access check fails', async () => {
    stubAccess(null)
    stubPeerConversation([answer()])
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
    sinon.stub(ChatSessionMessage, 'find').returns(chain([{ sessionId: SESSION, ...richAnswer(), messageType: 'user_query', citations: [], attachments: [] }]))
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
          messageType: 'user_query',
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

  it('a search the viewer owns, whose records are all readable, is returned untouched', async () => {
    const asked = stubAccess(['rec-no'])
    const search = { _id: 's1', userId: VIEWER, citationIds: [citation(CIT_NO, 'rec-no', SECRET)], records: { 'rec-no': 'x' } }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    const r = res()
    await getSearchById(appConfig)(req(VIEWER, { params: { searchId: SESSION } }), r, sinon.stub())

    expect(r.json.firstCall.args[0][0]).to.deep.equal(search) // a copy: the repository hands out plain rows
    expect(asked).to.have.length(1)
  })

  it('a search the viewer owns hides hit text and record entries for a record they lost', async () => {
    stubAccess(['rec-ok'])
    const bare = new Types.ObjectId()
    const search = { _id: 's1', userId: VIEWER, citationIds: [citation(CIT_OK, 'rec-ok', 'readable text'), citation(CIT_NO, 'rec-no', SECRET), bare, { content: 'no record here' }], records: { 'rec-ok': 'a', 'rec-no': SECRET } }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    const r = res()
    await getSearchById(appConfig)(req(VIEWER, { params: { searchId: SESSION } }), r, sinon.stub())

    const out = r.json.firstCall.args[0][0]
    expect(out.citationIds[0].content).to.equal('readable text')
    expect(out.citationIds[1]).to.deep.equal({ redacted: true, reason: 'no_longer_accessible' })
    expect(String(out.citationIds[2])).to.equal(String(bare))
    expect(out.citationIds[3]).to.deep.equal({ content: 'no record here' })
    expect(out.records).to.deep.equal({ 'rec-ok': 'a' })
    expect(JSON.stringify(out)).to.not.contain(SECRET)
  })

  it('a search the viewer owns hides its hits as unconfirmed when the access check fails', async () => {
    stubAccess(null)
    const search = { _id: 's1', userId: VIEWER, citationIds: [citation(CIT_NO, 'rec-no', SECRET)], records: { 'rec-no': SECRET } }
    sinon.stub(EnterpriseSemanticSearch, 'find').returns(chain([search]))
    const r = res()
    await getSearchById(appConfig)(req(VIEWER, { params: { searchId: SESSION } }), r, sinon.stub())

    const out = r.json.firstCall.args[0][0]
    expect(out.citationIds).to.deep.equal([{ redacted: true, reason: 'access_check_unavailable' }])
    expect(JSON.stringify(out)).to.not.contain(SECRET)
  })
})

const QUESTION = 'What were the quarterly numbers?'
const asked = () => ({ messageType: 'user_query', content: QUESTION })
const fetchAll = async (userId: string) => {
  const r = res()
  const next = sinon.stub()
  await getConversationById(appConfig)(req(userId, { params: { conversationId: SESSION } }), r, next)
  sinon.assert.notCalled(next)
  const messages: any[] = r.json.firstCall.args[0].conversation.messages
  return { question: messages.find((m) => m.messageType === 'user_query'), answer: messages.find((m) => m.messageType === 'bot_response') }
}

describe('lost access hides the whole answer, for the owner too', () => {
  afterEach(() => sinon.restore())
  const lost = { redacted: true, reason: 'no_longer_accessible' }

  it('the owner\'s answer citing a record they lost is hidden whole; the question stays', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    stubConversation([asked(), richAnswer()])
    const { question, answer: a } = await fetchAll(OWNER)

    expect(question.content).to.equal(QUESTION)
    expect(a.answerHidden).to.deep.equal(lost)
    expect(a.content).to.equal('This answer used content you can no longer access.')
    for (const k of ['citations', 'attachments', 'referenceData', 'followUpQuestions', 'reasoning', 'tools', 'parts']) expect(a[k], k).to.deep.equal([])
    const wire = JSON.stringify(a)
    for (const leaked of [SECRET, 'The answer', 'readable text', 'I looked at', 'And the budget', 'visible answer text', TOOL_TEXT, R_NO]) expect(wire).to.not.contain(leaked)
  })

  it('a mixed answer, one record readable and one not, is hidden whole', async () => {
    stubAccess(['rec-ok'])
    stubConversation([asked(), answer()])
    const { answer: a } = await fetchAll(OWNER)
    expect(a.answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(a)).to.not.contain('readable text')
  })

  it('the owner\'s answer whose records are all readable is unchanged', async () => {
    stubAccess(['rec-ok', 'rec-no', 'att-ok', 'att-no'])
    stubConversation([asked(), answer()])
    const { answer: a } = await fetchAll(OWNER)

    expect(a.answerHidden).to.equal(undefined)
    expect(a.content).to.equal('The answer [1][2]')
    expect(a.citations[1].citationData.content).to.equal(SECRET)
    expect(a.attachments).to.have.length(2)
  })

  it('an answer naming no record is unchanged and costs no access check', async () => {
    const calls = stubAccess([])
    stubConversation([asked(), { messageType: 'bot_response', content: 'Plain answer', citations: [] }])
    const { answer: a } = await fetchAll(OWNER)
    expect(a.content).to.equal('Plain answer')
    expect(a.answerHidden).to.equal(undefined)
    expect(calls).to.have.length(0)
  })

  it('a shared viewer gets the whole answer hidden, not only its citations', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    stubConversation([asked(), richAnswer()])
    const { answer: a } = await fetchAll(VIEWER)

    expect(a.answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(a)).to.not.contain('The answer')
    expect(JSON.stringify(a)).to.not.contain('And the budget')
    expect(JSON.stringify(a)).to.not.contain('I looked at')
  })

  it('an access check that throws hides the answer as unconfirmed, never shows it', async () => {
    stubAccess(null)
    stubConversation([asked(), answer()])
    const { question, answer: a } = await fetchAll(OWNER)

    expect(question.content).to.equal(QUESTION)
    expect(a.answerHidden).to.deep.equal({ redacted: true, reason: 'access_check_unavailable' })
    expect(a.content).to.equal("We couldn't confirm your access. Try again.")
    expect(JSON.stringify(a)).to.not.contain(SECRET)
    expect(JSON.stringify(a)).to.not.contain('The answer')
  })

  it('an archive list hides the owner\'s lost-access answer too', async () => {
    stubAccess(['rec-ok'])
    const archived = { _id: SESSION, title: 't', initiator: OWNER, userId: OWNER, updatedAt: new Date(), archivedBy: OWNER }
    sinon.stub(ChatSession, 'find').returns(chain([archived]))
    sinon.stub(ChatSession, 'countDocuments').resolves(1)
    sinon.stub(ChatSessionMessage, 'find').returns(chain([{ sessionId: SESSION, ...answer(), citations: [{ citationId: citation(CIT_NO, 'rec-no', SECRET) }], attachments: [] }]))
    const r = res()
    await listAllArchivesConversation(appConfig)(req(OWNER), r, sinon.stub())
    const m = r.json.firstCall.args[0].conversations[0].messages[0]
    expect(m.answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(m)).to.not.contain(SECRET)
  })

  it('an agent conversation hides the owner\'s lost-access answer too', async () => {
    stubAccess(['rec-ok'])
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
    sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false)
    sinon.stub(Users, 'find').returns(chain([]))
    sinon.stub(ChatSession, 'findOne').returns(chain({ _id: SESSION, orgId: ORG, title: 't', userId: OWNER, initiator: OWNER, status: 'complete' }))
    sinon.stub(ChatSessionMessage, 'countDocuments').resolves(1)
    sinon.stub(ChatSessionMessage, 'find').returns(chain([answer()]))
    const r = res()
    const next = sinon.stub()
    await getAgentConversationById(appConfig)(req(OWNER, { params: { conversationId: SESSION, agentKey: 'agent-1' } }), r, next)
    sinon.assert.notCalled(next)
    expect(r.json.firstCall.args[0].conversation.messages[0].answerHidden).to.deep.equal(lost)
  })

  it('the history sent to the model for a follow-up leaves out a hidden answer', async () => {
    stubAccess(['rec-ok'])
    const history = await historyForModel(appConfig, viewerOf(OWNER, ORG), [asked(), richAnswer(), { messageType: 'user_query', content: 'Repeat that answer' }] as any)
    const sent = formatPreviousConversations(history as any)

    expect(sent.map((m) => m.role)).to.deep.equal(['user_query', 'bot_response', 'user_query'])
    expect(sent[0].content).to.equal(QUESTION)
    expect(sent[1].content).to.equal('')
    const wire = JSON.stringify(sent)
    for (const leaked of [SECRET, 'The answer', TOOL_TEXT, R_NO, 'rec-no', 'att-no']) expect(wire).to.not.contain(leaked)
  })

  it('the history keeps an answer whose records are all readable, as stored', async () => {
    stubAccess(['rec-ok', 'rec-no', 'att-ok', 'att-no'])
    const history = await historyForModel(appConfig, viewerOf(OWNER, ORG), [asked(), answer()] as any)
    expect(formatPreviousConversations(history as any)[1].content).to.equal('The answer [1][2]')
  })

  it('the history of a follow-up hides the answer when the access check fails', async () => {
    stubAccess(null)
    const history = await historyForModel(appConfig, viewerOf(OWNER, ORG), [asked(), answer()] as any)
    expect(formatPreviousConversations(history as any)[1].content).to.equal('')
  })
})

describe('a hidden answer takes its turn with it; web links are not records', () => {
  afterEach(() => sinon.restore())
  const lost = { redacted: true, reason: 'no_longer_accessible' }
  const at = (i: number) => ({ createdAt: new Date(1000 + i) })
  const card = (i: number) => ({ messageType: 'tool_call', content: SECRET, tools: [{ toolName: 'ask_user_question', toolResult: { questions: [{ question: SECRET }] } }], ...at(i) })
  const fetchRows = async (userId: string) => {
    const r = res()
    await getConversationById(appConfig)(req(userId, { params: { conversationId: SESSION } }), r, sinon.stub())
    return r.json.firstCall.args[0].conversation.messages as any[]
  }

  it('tool_call rows after a hidden answer are hidden; rows of the next turn are not', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    stubConversation([
      { messageType: 'user_query', content: QUESTION, ...at(0) },
      { ...answer(), ...at(1) },
      card(2),
      { messageType: 'user_query', content: 'User selections: A', ...at(3) },
      card(4),
      { messageType: 'user_query', content: 'Next question', ...at(5) },
      { messageType: 'bot_response', content: 'Plain answer', citations: [], ...at(6) },
      { messageType: 'tool_call', content: 'kept card', tools: [{ toolName: 'ask_user_question', toolResult: 'kept' }], ...at(7) },
    ])
    const rows = await fetchRows(OWNER)

    expect(rows[2].answerHidden).to.deep.equal(lost)
    expect(rows[4].answerHidden).to.deep.equal(lost)
    expect(rows[3].content).to.equal('User selections: A')
    expect(JSON.stringify([rows[2], rows[4]])).to.not.contain(SECRET)
    expect(rows[7].content).to.equal('kept card')
    expect(rows[7].answerHidden).to.equal(undefined)
  })

  it('a page that starts with the tool_call rows of an answer on the page before hides them too', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    const rows = [
      { messageType: 'user_query', content: QUESTION, ...at(0) },
      { ...answer(), ...at(1) },
      card(2),
      card(3),
    ]
    stubConversation(rows)
    // Two rows a page: the second page holds only the card rows, the answer is on the first.
    sinon.stub(MongoConversationRepository.prototype, 'listMessages').callsFake(async (_id: string, q: { skip?: number; limit?: number }) => {
      const skip = q.skip ?? 0
      return rows.slice(skip, skip + (q.limit ?? rows.length)).map((dto) => ({ dto: { ...dto } })) as any
    })
    const r = res()
    await getConversationById(appConfig)(req(OWNER, { params: { conversationId: SESSION }, query: { page: '1', limit: '2' } }), r, sinon.stub())
    const page = r.json.firstCall.args[0].conversation.messages as any[]

    expect(page.map((m) => m.messageType)).to.deep.equal(['tool_call', 'tool_call'])
    for (const m of page) expect(m.answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(page)).to.not.contain(SECRET)
  })

  const pageOf = async (userId: string, rows: any[], query: Record<string, string>) => {
    stubConversation(rows)
    sinon.stub(MongoConversationRepository.prototype, 'listMessages').callsFake(async (_id: string, q: { skip?: number; limit?: number }) => {
      const skip = q.skip ?? 0
      return rows.slice(skip, skip + (q.limit ?? rows.length)).map((dto) => ({ dto: { ...dto } })) as any
    })
    const r = res()
    await getConversationById(appConfig)(req(userId, { params: { conversationId: SESSION }, query }), r, sinon.stub())
    return r.json.firstCall.args[0].conversation.messages as any[]
  }

  it('a card saved before its answer (the live order) is hidden with it, in the fetch and in the model history', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    const rows = [{ messageType: 'user_query', content: QUESTION, ...at(0) }, card(1), { ...answer(), ...at(2) }]
    stubConversation(rows)
    const shown = await fetchRows(OWNER)
    expect(shown[1].answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(shown)).to.not.contain(SECRET)

    const history = await historyForModel(appConfig, viewerOf(OWNER, ORG), rows.map((m) => ({ ...m })) as any)
    expect(JSON.stringify(formatPreviousConversations(history as any))).to.not.contain(SECRET)
  })

  it('an answer stored as a classified failure (type error) is hidden like any answer', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    stubConversation([{ messageType: 'user_query', content: QUESTION, ...at(0) }, { ...answer(), messageType: 'error', ...at(1) }])
    const rows = await fetchRows(OWNER)
    expect(rows[1].answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(rows[1])).to.not.contain(SECRET)
  })

  it('an approval answer continues its turn: the question\'s file counts for the continuation', async () => {
    stubAccess(['att-ok'])
    stubConversation([
      { messageType: 'user_query', content: QUESTION, attachments: [{ recordId: 'att-no', recordName: 'x.pdf' }], ...at(0) },
      { messageType: 'bot_response', content: 'first', citations: [], ...at(1) },
      { messageType: 'user_query', content: 'Tool approval: allow', ...at(2) },
      { messageType: 'bot_response', content: SECRET, status: 'stopped', citations: [], ...at(3) },
    ])
    const rows = await fetchRows(OWNER)
    expect(rows[1].answerHidden).to.deep.equal(lost)
    expect(rows[3].answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(rows[3])).to.not.contain(SECRET)
  })

  it('a card reply\'s own file is checked and counts for the turn\'s answer', async () => {
    stubAccess(['att-ok'])
    stubConversation([
      { messageType: 'user_query', content: QUESTION, ...at(0) },
      { messageType: 'user_query', content: 'User selections: A', attachments: [{ recordId: 'att-no', recordName: 'y.pdf' }], ...at(1) },
      { messageType: 'bot_response', content: SECRET, status: 'stopped', citations: [], ...at(2) },
    ])
    const rows = await fetchRows(OWNER)
    expect(rows[2].answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(rows[2])).to.not.contain(SECRET)
  })

  it('a stopped answer is hidden when the question\'s own file or filter is lost', async () => {
    stubAccess(['att-ok'])
    stubConversation([
      { messageType: 'user_query', content: QUESTION, attachments: [{ recordId: 'att-no', recordName: 'x.pdf' }], ...at(0) },
      { messageType: 'bot_response', content: SECRET, status: 'stopped', citations: [], ...at(1) },
    ])
    const rows = await fetchRows(OWNER)
    expect(rows[1].answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(rows[1])).to.not.contain(SECRET)
  })

  it('a page that starts with a User selections row and a card reads back to the question', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    const page = await pageOf(OWNER, [
      { messageType: 'user_query', content: QUESTION, ...at(0) },
      { ...answer(), ...at(1) },
      { messageType: 'user_query', content: 'User selections: A', ...at(2) },
      card(3),
    ], { page: '1', limit: '2' })
    expect(page[1].answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(page)).to.not.contain(SECRET)
  })

  it('a page that ends on a card whose answer is on the next page hides the card', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    const page = await pageOf(OWNER, [
      { messageType: 'user_query', content: QUESTION, ...at(0) },
      card(1),
      { ...answer(), ...at(2) },
      { messageType: 'user_query', content: 'Next', ...at(3) },
    ], { page: '2', limit: '2' })
    expect(page.map((m) => m.messageType)).to.deep.equal(['user_query', 'tool_call'])
    expect(page[1].answerHidden).to.deep.equal(lost)
    expect(JSON.stringify(page)).to.not.contain(SECRET)
  })

  it('a turn longer than one read step is still found; one longer than the cap is hidden as unconfirmed', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    // The live order: the question, its cards, then the answer.
    const turn = (cards: number, answerRow: any) => [
      { messageType: 'user_query', content: QUESTION, ...at(0) },
      ...Array.from({ length: cards }, (_, i) => card(1 + i)),
      { ...answerRow, ...at(1 + cards) },
    ]
    const lostAnswer = answer()
    const long = await pageOf(OWNER, turn(60, lostAnswer), { page: '1', limit: '2' })
    expect(long.map((m) => m.answerHidden)).to.deep.equal([lost, lost])
    sinon.restore()

    // A stopped answer names no record, so only the unknown turn can hide it.
    stubAccess(['rec-ok', 'att-ok'])
    const stopped = { messageType: 'bot_response', content: SECRET, status: 'stopped', citations: [] }
    const huge = await pageOf(OWNER, turn(700, stopped), { page: '1', limit: '2' })
    const unconfirmed = { redacted: true, reason: 'access_check_unavailable' }
    expect(huge.map((m) => m.answerHidden)).to.deep.equal([unconfirmed, unconfirmed])
    expect(JSON.stringify(huge)).to.not.contain(SECRET)
  })

  it('an answer at the end of a page whose turn runs on past the cap is hidden as unconfirmed', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    const stopped = { messageType: 'bot_response', content: SECRET, status: 'stopped', citations: [] }
    const rows = [
      { messageType: 'user_query', content: QUESTION, ...at(0) },
      { ...stopped, ...at(1) },
      ...Array.from({ length: 700 }, (_, i) => card(2 + i)),
    ]
    const oldest = await pageOf(OWNER, rows, { page: '351', limit: '2' })
    expect(oldest.map((m) => m.messageType)).to.deep.equal(['user_query', 'bot_response'])
    expect(oldest[1].answerHidden).to.deep.equal({ redacted: true, reason: 'access_check_unavailable' })
    expect(JSON.stringify(oldest)).to.not.contain(SECRET)
  })

  it('a record already known to be denied hides a fresh answer even when a new record could not be checked', async () => {
    stubAccess(null)
    const memo = newReadable()
    memo.asked.add('i:att-no')
    const fresh = { _id: 'f', messageType: 'bot_response', content: 'x', attachments: [{ recordId: 'att-no' }, { recordId: 'att-new' }] }
    const out: any[] = await redactMessagesFor(appConfig, viewerOf(OWNER, ORG), [fresh] as any, () => OWNER, { known: memo, fresh: () => true })
    expect(out[0].answerHidden).to.deep.equal(lost)
  })

  it('a web citation whose address matches no record keeps the answer; a citation with no row hides it', async () => {
    stubAccess([], ['https://example.com/page'])
    stubConversation([{ messageType: 'bot_response', content: 'Web answer', citations: [{ citationId: { _id: 'c', content: 'x', metadata: { webUrl: 'https://example.com/page' } } }] }])
    const [m] = await fetchRows(OWNER)
    expect(m.content).to.equal('Web answer')
  })

  it('hides a turn\'s tool rows whatever order the client sorts the page in', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    stubConversation([
      { messageType: 'user_query', content: QUESTION, ...at(0) },
      { ...answer(), ...at(1) },
      card(2),
      { messageType: 'user_query', content: 'Next question', ...at(3) },
      { messageType: 'bot_response', content: 'Plain answer', citations: [], ...at(4) },
      { messageType: 'tool_call', content: 'kept card', tools: [{ toolName: 'ask_user_question', toolResult: 'kept' }], ...at(5) },
    ])
    const r = res()
    await getConversationById(appConfig)(req(OWNER, { params: { conversationId: SESSION }, query: { sortBy: 'messageType' } }), r, sinon.stub())
    const rows = r.json.firstCall.args[0].conversation.messages as any[]

    // Sorted by type the plain answer comes between the lost answer and its card.
    expect(rows.map((m) => m.messageType)).to.deep.equal(['bot_response', 'bot_response', 'tool_call', 'tool_call', 'user_query', 'user_query'])
    const secretCard = rows.find((m) => m._id === undefined && m.answerHidden !== undefined && m.messageType === 'tool_call')
    expect(secretCard, 'the card of the lost answer is hidden').to.not.equal(undefined)
    expect(JSON.stringify(rows)).to.not.contain(SECRET)
    expect(rows.find((m) => m.content === 'kept card')).to.not.equal(undefined)
  })

  it('tool_call rows are hidden as unconfirmed when the check cannot answer', async () => {
    stubAccess(null)
    stubConversation([{ messageType: 'user_query', content: QUESTION, ...at(0) }, { ...answer(), ...at(1) }, card(2)])
    const rows = await fetchRows(OWNER)
    expect(rows[2].answerHidden).to.deep.equal({ redacted: true, reason: 'access_check_unavailable' })
    expect(JSON.stringify(rows[2])).to.not.contain(SECRET)
  })

  it('a web-search answer whose link matches no record stays visible', async () => {
    stubAccess([], ['https://example.com/page'])
    stubConversation([{ messageType: 'bot_response', content: 'From the web', citations: [], referenceData: [{ name: 'Example', webUrl: 'https://example.com/page' }] }])
    const [m] = await fetchRows(OWNER)
    expect(m.content).to.equal('From the web')
    expect(m.answerHidden).to.equal(undefined)
  })

  it('a link that resolves to a record the viewer cannot read hides the answer', async () => {
    stubAccess([], [])
    stubConversation([{ messageType: 'bot_response', content: 'From a page', citations: [], referenceData: [{ name: 'Wiki', webUrl: 'https://wiki/page' }] }])
    const [m] = await fetchRows(OWNER)
    expect(m.answerHidden).to.deep.equal(lost)
  })

  it('a just-streamed answer is never replaced by the unconfirmed notice, an earlier one is', async () => {
    stubAccess(null)
    const fresh = { _id: 'fresh-id', messageType: 'bot_response', content: 'Fresh answer', attachments: [{ recordId: 'att-new' }] }
    const old = { _id: 'old-id', messageType: 'bot_response', content: 'Old answer', attachments: [{ recordId: 'att-old' }] }
    const out: any = await redactConversationFor(appConfig, viewerOf(OWNER, ORG), { messages: [old, fresh] }, { freshIds: ['fresh-id'] })

    expect(out.messages[0].answerHidden).to.deep.equal({ redacted: true, reason: 'access_check_unavailable' })
    expect(out.messages[1].content).to.equal('Fresh answer')
    expect(out.messages[1].answerHidden).to.equal(undefined)
  })

  it('a shared viewer sees a web reference as it is, and an unreadable record\'s link as hidden', async () => {
    stubAccess([], ['https://example.com/page'])
    stubConversation([{ messageType: 'user_query', content: QUESTION, referenceData: [{ name: 'Example', webUrl: 'https://example.com/page' }, { name: SECRET, webUrl: 'https://wiki/page' }] }])
    const m = await fetchConversation(VIEWER)
    expect(m.referenceData[0].name).to.equal('Example')
    expect(m.referenceData[1]).to.deep.equal({ redacted: true, reason: 'no_access', name: 'Restricted source' })
  })

  it('records the history check settled are not asked about again for the final conversation', async () => {
    const asked = stubAccess(['rec-ok', 'att-ok'])
    const memo = newReadable()
    const viewer = viewerOf(OWNER, ORG)
    const stored: any[] = [asked_(), answer()]
    await historyForModel(appConfig, viewer, stored, memo)
    const fresh = { messageType: 'bot_response', content: 'new', citations: [], attachments: [{ recordId: 'att-new' }] }
    await redactMessagesFor(appConfig, viewer, [...stored, fresh] as any, () => OWNER, { known: memo })

    expect(asked.flat().sort()).to.deep.equal(['att-new', 'att-no', 'att-ok', 'rec-no', 'rec-ok'])
  })
})
const asked_ = () => ({ messageType: 'user_query', content: QUESTION })
