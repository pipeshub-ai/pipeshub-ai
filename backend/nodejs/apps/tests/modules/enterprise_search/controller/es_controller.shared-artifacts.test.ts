import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { Logger } from '../../../../src/libs/services/logger.service'
import { InMemoryArtifactReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-artifact-access'
import { HarnessLogger } from '../../../../src/modules/agent-harness/application/harness-log'
import { SharedArtifactService } from '../../../../src/modules/agent-harness/application/shared-artifacts'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * SS0 (AN-5) on the conversation GET: a reader who does not own the
 * conversation sees an artifact only when every source it was derived from is
 * readable by them. Before SS0 every marker reached them, and with it the
 * record id the download route served.
 */

const appConfig = { aiBackend: 'http://ai.test', iamBackend: 'http://iam.test' } as never
const ORG = oid()
const OWNER = oid()
const RECIPIENT = oid()
const AGENT_KEY = 'agent-1'

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
type Message = { content: string; parts?: Array<Record<string, unknown>> }

const marker = (name: string, recordId: string) =>
  `::artifact[${name}](record:${recordId}){application/pdf|doc-${recordId}|${recordId}|DOCUMENT|1}`

const ANSWER = [
  'Here is the analysis.',
  marker('open-report.pdf', 'art-open'),
  marker('secret-report.pdf', 'art-secret'),
  '::download_conversation_task[legacy.csv](https://bucket.example/legacy.csv?X-Amz-Signature=abc)',
].join('\n\n')

function recording(): { logger: HarnessLogger; lines: Array<[string, string, Record<string, unknown>]> } {
  const lines: Array<[string, string, Record<string, unknown>]> = []
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta])
  }
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } }
}

function setup(harnessVersion?: 'durable-v1') {
  const store = new InMemoryChatStore()
  store.install()
  new FakeAIBackend().install()
  sinon.stub(Logger.getInstance(), 'warn')
  const projectId = oid()
  ;(ProjectService.getAccessibleProjectIds as sinon.SinonStub).resolves([projectId])
  const shared = { isShared: true, sharedWith: [{ userId: RECIPIENT, accessLevel: 'write' }] }
  const sessions = {
    chat: store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Chat', sessionType: 'chat', ...shared, ...(harnessVersion ? { harnessVersion } : {}) }),
    agent: store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Agent', sessionType: 'agent', agentKey: AGENT_KEY, conversationSource: 'agent_chat', projectId, projectVisibility: 'project', ...(harnessVersion ? { harnessVersion } : {}) }),
  }
  for (const session of Object.values(sessions)) {
    store.addMessage(session, { messageType: 'user_query', content: 'Analyse Q3' })
    store.addMessage(session, {
      messageType: 'bot_response',
      content: ANSWER,
      parts: [
        { type: 'tool_call', toolName: 'run_code', artifactId: 'art-secret-result' },
        { type: 'text', content: 'done', isFinal: true },
      ],
    })
  }
  return { sessions }
}

function request(userId: Types.ObjectId, params: Record<string, string>): never {
  return {
    headers: { authorization: `Bearer ${String(userId)}` },
    params,
    body: {},
    query: {},
    user: { userId: String(userId), orgId: String(ORG), email: 'someone@example.com' },
    context: { requestId: 'req-shared-artifacts' },
  } as never
}

async function read(handler: JsonHandler, req: never): Promise<{ body: string; answer: Message }> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await handler(req, res as never, next as never)
  await settle()
  expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
  const conversation = (res.jsonBody as { conversation: { messages: Message[] } }).conversation
  return { body: JSON.stringify(res.jsonBody), answer: conversation.messages[1]! }
}

const cases = [
  {
    name: 'chat',
    handler: (artifacts?: SharedArtifactService) => controller.getConversationById(appConfig, { artifacts }) as JsonHandler,
    params: (s: ReturnType<typeof setup>) => ({ conversationId: String(s.sessions.chat._id) }),
  },
  {
    name: 'agent',
    handler: (artifacts?: SharedArtifactService) => controller.getAgentConversationById(appConfig, { artifacts }) as JsonHandler,
    params: (s: ReturnType<typeof setup>) => ({ conversationId: String(s.sessions.agent._id), agentKey: AGENT_KEY }),
  },
]

const SECRET_LEAKS = ['art-secret', 'secret-report.pdf', 'doc-art-secret', 'legacy.csv', 'X-Amz-Signature']

describe('es_controller: a reader who does not own a conversation sees only the artifacts they may (SS0)', () => {
  afterEach(() => sinon.restore())

  for (const kind of cases) {
    for (const harness of [undefined, 'durable-v1'] as const) {
      const label = `${kind.name}, ${harness ?? 'legacy'} session`

      it(`${label}: readable artifacts stay, every other one becomes a placeholder`, async () => {
        const s = setup(harness)
        const artifacts = new InMemoryArtifactReadAccess().grant(`Bearer ${String(RECIPIENT)}`, 'art-open')
        const log = recording()
        const out = await read(kind.handler(new SharedArtifactService(artifacts, log.logger)), request(RECIPIENT, kind.params(s)))

        expect(artifacts.queries).to.deep.equal([
          { authorization: `Bearer ${String(RECIPIENT)}`, artifactIds: ['art-open', 'art-secret', 'art-secret-result'] },
        ])
        expect(out.answer.content).to.equal(
          ['Here is the analysis.', marker('open-report.pdf', 'art-open'), '::artifact_unavailable', '::artifact_unavailable'].join('\n\n'),
        )
        expect(out.answer.parts?.[0]).to.not.have.property('artifactId')
        for (const leak of SECRET_LEAKS) expect(out.body, leak).to.not.include(leak)
        expect(log.lines.map(([, event, meta]) => [event, meta['allowed'], meta['unverifiable'], meta['surface']])).to.deep.equal([
          ['artifacts.access_checked', 1, 2, 'conversation'],
        ])
        expect(JSON.stringify(log.lines)).to.not.include('art-')
      })

      it(`${label}: the owner is never checked and keeps every artifact`, async () => {
        const s = setup(harness)
        const artifacts = new InMemoryArtifactReadAccess()
        const out = await read(kind.handler(new SharedArtifactService(artifacts)), request(OWNER, kind.params(s)))
        expect(artifacts.queries).to.deep.equal([])
        expect(out.answer.content).to.equal(ANSWER)
        expect(out.answer.parts?.[0]).to.include({ artifactId: 'art-secret-result' })
      })

      it(`${label}: fails closed when the check fails`, async () => {
        const s = setup(harness)
        const artifacts = new InMemoryArtifactReadAccess().grant(`Bearer ${String(RECIPIENT)}`, 'art-open', 'art-secret')
        artifacts.failWith = 'refused'
        const log = recording()
        const out = await read(kind.handler(new SharedArtifactService(artifacts, log.logger)), request(RECIPIENT, kind.params(s)))
        for (const leak of [...SECRET_LEAKS, 'art-open', 'open-report.pdf']) expect(out.body, leak).to.not.include(leak)
        expect(log.lines[0]?.[1]).to.equal('artifacts.access_check_failed')
        expect(log.lines[0]?.[2]).to.include({ reason: 'refused', surface: 'conversation', choice: 'hide_artifacts' })
      })
    }

    it(`${kind.name}: with no check wired, a recipient sees no artifact`, async () => {
      const s = setup()
      const out = await read(kind.handler(), request(RECIPIENT, kind.params(s)))
      for (const leak of [...SECRET_LEAKS, 'art-open', 'open-report.pdf']) expect(out.body, leak).to.not.include(leak)
      expect(out.answer.content).to.include('::artifact_unavailable')
    })
  }
})
