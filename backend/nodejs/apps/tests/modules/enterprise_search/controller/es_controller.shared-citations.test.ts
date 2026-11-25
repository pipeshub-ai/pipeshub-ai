import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { Logger } from '../../../../src/libs/services/logger.service'
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access'
import { HarnessLogger } from '../../../../src/modules/agent-harness/application/harness-log'
import { SharedCitationService } from '../../../../src/modules/agent-harness/application/shared-citations'
import { indexed, leaksOf, live, liveCitation, recordCitation, webCitation } from '../../agent-harness/support/citations'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

/**
 * C-6 on the conversation GET, the one shared view of a conversation's
 * history: legacy and durable sessions, chat and agent, go through it. Any
 * reader who is not the owner (a `sharedWith` recipient, a project member)
 * is checked. Since main #5698 an answer that used a record its reader cannot
 * read is hidden whole: a citation C-6 withheld leaves the answer unconfirmed,
 * so the reader sees the hidden marker, never the answer or the record.
 */

// The record-access check signs a scoped token for the query service.
const appConfig = {
  aiBackend: 'http://ai.test',
  iamBackend: 'http://iam.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never
const ORG = oid()
const OWNER = oid()
const RECIPIENT = oid()
const AGENT_KEY = 'agent-1'

type Line = [string, string, Record<string, unknown>]
type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
type Citations = Array<{ citationId: unknown; citationData: Record<string, unknown> }>

function recording(): { logger: HarnessLogger; lines: Line[] } {
  const lines: Line[] = []
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta])
  }
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } }
}

const inOrg = (citation: Record<string, unknown>) => ({
  ...citation,
  metadata: { ...(citation['metadata'] as object), orgId: String(ORG) },
})

function setup(
  harnessVersion?: 'durable-v1',
  cited: Array<Record<string, unknown>> = [inOrg(recordCitation('rec-open', 1)), inOrg(recordCitation('rec-closed', 2)), webCitation(3)],
) {
  const store = new InMemoryChatStore()
  store.install()
  const ai = new FakeAIBackend()
  ai.install()
  sinon.stub(Logger.getInstance(), 'warn')
  const stored = cited.map((citation) => store.addCitation(citation))
  const shared = { isShared: true, sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }] }
  // Agent conversations reach other readers through their project, not `sharedWith`.
  const projectId = oid()
  ;(ProjectService.getAccessibleProjectIds as sinon.SinonStub).resolves([projectId])
  const sessions = {
    chat: store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Chat', sessionType: 'chat', ...shared, ...(harnessVersion ? { harnessVersion } : {}) }),
    agent: store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Agent', sessionType: 'agent', agentKey: AGENT_KEY, conversationSource: 'agent_chat', projectId, projectVisibility: 'project', ...(harnessVersion ? { harnessVersion } : {}) }),
  }
  for (const session of Object.values(sessions)) {
    store.addMessage(session, { messageType: 'user_query', content: 'What were Q3 sales?' })
    store.addMessage(session, {
      messageType: 'bot_response',
      content: 'Q3 sales were 4M [1] [2] [3].',
      citations: stored.map((citation) => ({ citationId: citation._id })),
    })
  }
  return { store, ai, sessions, closedCitationId: String(stored[1]!._id) }
}

function request(userId: Types.ObjectId, params: Record<string, string>): never {
  return {
    headers: { authorization: `Bearer ${String(userId)}` },
    params,
    body: {},
    query: {},
    user: { userId: String(userId), orgId: String(ORG), email: "someone@example.com" },
    context: { requestId: 'req-shared-citations' },
  } as never
}

const UNCONFIRMED = { redacted: true, reason: 'access_check_unavailable' }

/** The owner can read every record of their own answers: the record-access check admits everything it is asked. */
const ownerReadsAll = (ai: FakeAIBackend): void => {
  ai.replyWith(/\/chat\/records\/access$/, (call) => ({
    status: 200,
    body: { accessible: call.body['recordIds'] ?? [], accessibleUrls: [], unmatchedUrls: call.body['webUrls'] ?? [] },
  }))
}

/**
 * The query service as it answers a reader who is not the owner: C-6 decided the records, so only the
 * cited web page's address is asked, and a public page matches no stored record.
 */
const webPagesArePublic = (ai: FakeAIBackend): void => {
  ai.replyWith(/\/chat\/records\/access$/, (call) => ({
    status: 200,
    body: { accessible: [], accessibleUrls: [], unmatchedUrls: call.body['webUrls'] ?? [] },
  }))
}

async function read(
  handler: JsonHandler,
  req: never,
): Promise<{ body: string; citations: Citations; answerHidden: unknown }> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await handler(req, res as never, next as never)
  await settle()
  expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
  const conversation = (res.jsonBody as { conversation: { messages: Array<{ citations: Citations; answerHidden?: unknown }> } })
    .conversation
  const answer = conversation.messages[1]!
  return { body: JSON.stringify(res.jsonBody), citations: answer.citations, answerHidden: answer.answerHidden }
}

const cases = [
  {
    name: 'chat',
    handler: (citations?: SharedCitationService) => controller.getConversationById(appConfig, { citations }) as JsonHandler,
    params: (s: ReturnType<typeof setup>) => ({ conversationId: String(s.sessions.chat._id) }),
  },
  {
    name: 'agent',
    handler: (citations?: SharedCitationService) => controller.getAgentConversationById(appConfig, { citations }) as JsonHandler,
    params: (s: ReturnType<typeof setup>) => ({ conversationId: String(s.sessions.agent._id), agentKey: AGENT_KEY }),
  },
]

describe('es_controller: a shared reader of a conversation sees only the citations they can read (C-6)', () => {
  beforeEach(() => registerAppUpstreams(appConfig))
  afterEach(() => registerUpstreams({}))
  afterEach(() => sinon.restore())

  for (const kind of cases) {
    for (const harness of [undefined, 'durable-v1'] as const) {
      const label = `${kind.name}, ${harness ?? 'legacy'} session`

      it(`${label}: a recipient who cannot read a cited record sees the answer hidden, its records named nowhere`, async () => {
        const s = setup(harness)
        webPagesArePublic(s.ai)
        const records = new InMemoryRecordReadAccess().grant(`Bearer ${String(RECIPIENT)}`, 'rec-open')
        const log = recording()
        const out = await read(kind.handler(new SharedCitationService(records, log.logger)), request(RECIPIENT, kind.params(s)))
        expect(records.queries).to.deep.equal([{ authorization: `Bearer ${String(RECIPIENT)}`, sources: [indexed('rec-open'), indexed('rec-closed')] }])
        expect(out.answerHidden).to.deep.equal(UNCONFIRMED)
        expect(out.citations).to.deep.equal([])
        for (const leak of [...leaksOf('rec-closed'), s.closedCitationId]) expect(out.body, leak).to.not.include(leak)
        expect(out.body, 'the answer text is hidden with it').to.not.include('Q3 sales were 4M')
        expect(log.lines.map(([, event, meta]) => [event, meta['allowed'], meta['denied'], meta['surface']])).to.deep.equal([
          ['citations.access_checked', 1, 1, 'conversation'],
        ])
        expect(log.lines[0]![2]).to.include({ requestId: 'req-shared-citations', orgId: String(ORG) })
      })

      it(`${label}: a live citation is shown through its readable indexed copy and hidden without one, saved before C-18 or after`, async () => {
        const s = setup(harness, [
          inOrg(liveCitation('f-copy', 1)),
          inOrg(liveCitation('f-none', 2)),
          inOrg(liveCitation('f-legacy', 3, { sourceKind: false })),
        ])
        const records = new InMemoryRecordReadAccess()
          .grant(`Bearer ${String(RECIPIENT)}`, 'copy-open', 'copy-legacy')
          .indexCopy('DRIVE', 'f-copy', 'copy-open')
          .indexCopy('DRIVE', 'f-legacy', 'copy-legacy')
        const log = recording()
        const out = await read(kind.handler(new SharedCitationService(records, log.logger)), request(RECIPIENT, kind.params(s)))
        expect(records.queries).to.deep.equal([
          { authorization: `Bearer ${String(RECIPIENT)}`, sources: [live('f-copy'), live('f-none'), live('f-legacy')] },
        ])
        // f-none has no readable indexed copy, so the answer that cited it is hidden.
        expect(out.answerHidden).to.deep.equal(UNCONFIRMED)
        expect(out.citations).to.deep.equal([])
        for (const leak of [...leaksOf('f-none'), 'TEMP-f-none']) expect(out.body, leak).to.not.include(leak)
        expect(log.lines[0]![2]).to.include({ allowed: 2, denied: 0, unverifiable: 1 })
      })

      it(`${label}: the owner is never checked and sees every citation`, async () => {
        const s = setup(harness)
        ownerReadsAll(s.ai)
        const records = new InMemoryRecordReadAccess()
        const out = await read(kind.handler(new SharedCitationService(records)), request(OWNER, kind.params(s)))
        expect(records.queries).to.deep.equal([])
        expect(out.body).to.include('NAME-rec-closed')
        expect(out.citations.map((c) => c.citationData['content'])).to.deep.equal(['SNIPPET-rec-open', 'SNIPPET-rec-closed', 'public page text'])
      })

      it(`${label}: fails closed when the check fails`, async () => {
        const s = setup(harness)
        webPagesArePublic(s.ai)
        const records = new InMemoryRecordReadAccess().grant(`Bearer ${String(RECIPIENT)}`, 'rec-open', 'rec-closed')
        records.failWith = 'unavailable'
        const log = recording()
        const out = await read(kind.handler(new SharedCitationService(records, log.logger)), request(RECIPIENT, kind.params(s)))
        for (const leak of [...leaksOf('rec-open'), ...leaksOf('rec-closed')]) expect(out.body, leak).to.not.include(leak)
        expect(out.answerHidden).to.deep.equal(UNCONFIRMED)
        expect(out.citations).to.deep.equal([])
        expect(log.lines[0]?.[1]).to.equal('citations.access_check_failed')
        expect(log.lines[0]?.[2]).to.include({ reason: 'unavailable', surface: 'conversation' })
      })
    }

    it(`${kind.name}: AU-9 (SS2): the owner reads a collaborator's answer checked against the owner's own access`, async () => {
      const s = setup('durable-v1')
      const session = kind.name === 'chat' ? s.sessions.chat : s.sessions.agent
      const collaborator = oid()
      const cited = [inOrg(recordCitation('rec-bob-only', 4))].map((citation) => s.store.addCitation(citation))
      s.store.addMessage(session, { messageType: 'user_query', content: 'Bob asks', senderId: collaborator })
      s.store.addMessage(session, {
        messageType: 'bot_response',
        content: 'From his file [1].',
        citations: cited.map((citation) => ({ citationId: citation._id })),
        senderId: collaborator,
      })
      ownerReadsAll(s.ai)
      const records = new InMemoryRecordReadAccess()
      const res = new FakeSSEResponse()
      await kind.handler(new SharedCitationService(records))(request(OWNER, kind.params(s)), res as never, sinon.stub() as never)
      await settle()
      const messages = (res.jsonBody as { conversation: { messages: Array<{ citations?: Citations; answerHidden?: unknown }> } })
        .conversation.messages
      // Only the collaborator's answer is checked, with the owner's credential: Bob's record is hidden, and his answer with it.
      expect(records.queries).to.deep.equal([{ authorization: `Bearer ${String(OWNER)}`, sources: [indexed('rec-bob-only')] }])
      expect(messages[3]!.answerHidden).to.deep.equal(UNCONFIRMED)
      expect(messages[3]!.citations).to.deep.equal([])
      for (const leak of leaksOf('rec-bob-only')) expect(JSON.stringify(res.jsonBody), leak).to.not.include(leak)
      // The owner's own answer keeps every citation.
      expect(messages[1]!.citations!.map((c) => c.citationData['content'])).to.deep.equal(['SNIPPET-rec-open', 'SNIPPET-rec-closed', 'public page text'])
    })

    it(`${kind.name}: with no check wired, a recipient's record citations are hidden`, async () => {
      const s = setup()
      const out = await read(kind.handler(), request(RECIPIENT, kind.params(s)))
      expect(out.body).to.not.include('NAME-rec-open')
      // The record-access redaction asks for them itself; the backend admits none, so the answer is hidden as lost.
      expect(out.answerHidden).to.deep.equal({ redacted: true, reason: 'no_longer_accessible' })
      expect(out.citations).to.deep.equal([])
    })
  }
})
