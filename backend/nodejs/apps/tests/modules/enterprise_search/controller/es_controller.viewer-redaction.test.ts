import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
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

/** Every field of a stored answer that can carry a hidden record's name, link or text. */
const leakyAnswer = () => ({
  ...answer(),
  referenceData: [{ name: SECRET, id: 'rec-no', webUrl: `https://x/${SECRET}` }],
  reasoning: [{ content: SECRET }],
  tools: [{ toolName: 'search', toolResult: SECRET }],
  appliedFilters: { records: [{ id: 'rec-no', name: SECRET }] },
  followUpQuestions: [{ question: `What about ${SECRET}?`, confidence: 'High' }],
  feedback: [{ comments: { positive: SECRET } }],
  futureField: SECRET,
  parts: [
    { type: 'text', content: 'visible answer text' },
    { type: 'reasoning', content: SECRET },
    { type: 'mcp_sign_in', servers: [{ serverName: SECRET }] },
    { type: 'tool_call', toolName: 'search', displayName: 'Searched', status: 'completed', args: SECRET, argsSummary: SECRET, resultPreview: SECRET, resultSummary: SECRET, resultView: SECRET, artifactId: SECRET },
    { type: 'sub_agent', roleName: 'worker', parts: [{ type: 'tool_call', toolName: 'read', resultPreview: SECRET }, { type: 'unknown_kind', content: SECRET }] },
  ],
})

const expectNoLeak = (message: any) => {
  expect(JSON.stringify(message)).to.not.contain(SECRET)
  for (const f of ['referenceData', 'reasoning', 'tools', 'appliedFilters', 'followUpQuestions', 'feedback', 'futureField']) {
    expect(message, f).to.not.have.property(f)
  }
  expect(message.content).to.equal('The answer [1][2]')
  expect(message.parts).to.deep.equal([
    { type: 'text', content: 'visible answer text' },
    { type: 'tool_call', toolName: 'search', displayName: 'Searched', status: 'completed' },
    { type: 'sub_agent', roleName: 'worker', parts: [{ type: 'tool_call', toolName: 'read' }] },
  ])
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
const stubAccess = (readable: string[] | null) => {
  const asked: string[][] = []
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
    if (this.uri.endsWith('/chat/records/access')) {
      asked.push(this.body ? JSON.parse(this.body).recordIds : [])
      if (readable === null) throw new Error('access service down')
      return { statusCode: 200, data: { accessible: readable }, msg: '' } as any
    }
    return { statusCode: 200, data: {}, msg: '' } as any // the unrelated permission sync
  })
  return asked
}

const stubConversation = (messages: unknown[]) => {
  sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
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
  expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
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

  it('a viewer gets no tool results, reasoning, reference data, filters or follow-ups, only allow-listed fields', async () => {
    stubAccess(['rec-ok'])
    stubConversation([leakyAnswer()])
    expectNoLeak(await fetchConversation(VIEWER))
  })

  it('the owner still gets every field', async () => {
    stubAccess([])
    stubConversation([leakyAnswer()])
    const message = await fetchConversation(OWNER)
    expect(message.parts).to.have.length(5)
    expect(message.referenceData).to.have.length(1)
    expect(message.futureField).to.equal(SECRET)
  })

  it('an agent conversation opened by a project member or shared user is redacted too', async () => {
    stubAccess(['rec-ok', 'att-ok'])
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
    sinon.stub(Users, 'find').returns(chain([]))
    sinon.stub(ChatSession, 'findOne').returns(chain({ _id: SESSION, orgId: ORG, title: 't', initiator: OWNER, isShared: true, sharedWith: [], status: 'complete' }))
    sinon.stub(ChatSessionMessage, 'countDocuments').resolves(1)
    sinon.stub(ChatSessionMessage, 'find').returns(chain([leakyAnswer()]))
    const r = res()
    const next = sinon.stub()
    await getAgentConversationById(appConfig)(req(VIEWER, { params: { conversationId: SESSION, agentKey: 'agent-1' } }), r, next)

    expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
    const message = r.json.firstCall.args[0].conversation.messages[0]
    expectNoLeak(message)
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

  it('an archive strips the same fields from another user\'s messages', async () => {
    stubAccess(['rec-ok'])
    const archived = { _id: SESSION, title: 't', initiator: OWNER, isShared: true, sharedWith: [{ userId: VIEWER }], updatedAt: new Date(), archivedBy: OWNER }
    sinon.stub(ChatSession, 'find').returns(chain([archived]))
    sinon.stub(ChatSession, 'countDocuments').resolves(1)
    sinon.stub(ChatSessionMessage, 'find').returns(chain([{ sessionId: SESSION, ...leakyAnswer(), citations: [], attachments: [] }]))
    const r = res()
    await listAllArchivesConversation(appConfig)(req(VIEWER), r, sinon.stub())

    const message = r.json.firstCall.args[0].conversations[0].messages[0]
    expectNoLeak(message)
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

    expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
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
