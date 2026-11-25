import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../src/modules/enterprise_search/controller/es_controller'
import { formatPreviousConversations } from '../../src/modules/enterprise_search/utils/utils'
import { IMessage } from '../../src/modules/enterprise_search/types/conversation.interfaces'
import { ProjectService } from '../../src/modules/projects/services/project.service'
import { FakeSSEResponse, settle } from '../modules/enterprise_search/controller/chat-test-harness'
import { appConfig } from '../modules/enterprise_search/controller/streaming-flows'
import { AIServiceCommand } from '../../src/libs/commands/ai_service/ai.service.command'
import { CHAT_SCENARIOS, DURABLE_SCENARIOS, STEERED_SCENARIOS } from './support/durable'
import { PersistedRun, persistedRun } from './support/surfaces'

/**
 * P1b T3 (MG-3, MG-3a): with the harness flag off, legacy reads a durable
 * session from what the run left in the conversation (the transcript
 * projection written in its commit). Every durable scenario, steered ones
 * included, goes through the legacy conversation GET, which never consults
 * the rollout, and continues with the history legacy builds from it.
 */

type Message = Record<string, unknown>

const FIELDS = ['messageType', 'content', 'contentFormat', 'confidence', 'parts', 'tools', 'status']

const pick = (message: Message): Message =>
  Object.fromEntries(FIELDS.filter((k) => message[k] !== undefined).map((k) => [k, message[k]]))

const citationIds = (message: Message): string[] =>
  ((message['citations'] as Array<{ citationId: unknown }> | undefined) ?? []).map((c) => {
    const id = c.citationId as { _id?: unknown } | string
    return String(typeof id === 'object' && id !== null && '_id' in id ? id._id : id)
  })

async function legacyGet(persisted: PersistedRun): Promise<{ status: unknown; messages: Message[] }> {
  const { run } = persisted
  ;(ProjectService.getAccessibleProjectIds as sinon.SinonStub).resolves([])
  // The owner can still read every record the run cited, so the read hides no answer (main #5698).
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: { uri: string; body?: string }) {
    const asked = this.uri.endsWith('/chat/records/access')
      ? (JSON.parse(this.body ?? '{}') as { recordIds?: string[]; webUrls?: string[] })
      : undefined
    return Promise.resolve({
      statusCode: 200,
      data: asked ? { accessible: asked.recordIds ?? [], accessibleUrls: [], unmatchedUrls: asked.webUrls ?? [] } : {},
      msg: '',
    } as never)
  })
  const agent = run.agentKey !== undefined
  const handler = agent ? controller.getAgentConversationById(appConfig) : controller.getConversationById(appConfig)
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await handler(
    {
      headers: { authorization: `Bearer ${run.actorId}` },
      params: { conversationId: run.sessionId, ...(agent ? { agentKey: run.agentKey } : {}) },
      body: {},
      query: {},
      user: { userId: run.actorId, orgId: run.orgId, email: 'owner@example.com' },
      context: { requestId: 'req-rollback' },
    } as never,
    res as never,
    next as never,
  )
  await settle()
  expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
  const { conversation } = res.jsonBody as { conversation: { status: unknown; messages: Message[] } }
  return { status: conversation.status, messages: conversation.messages }
}

const SCENARIOS = [...DURABLE_SCENARIOS, ...Object.keys(STEERED_SCENARIOS)]

describe('contract: flag off, legacy renders and continues every durable session (P1b T3, MG-3)', () => {
  afterEach(() => sinon.restore())

  it('covers completed, failed, stopped, needs-input and steered runs, chat and agent', () => {
    expect(SCENARIOS).to.include.members([
      'agent_quick_tool_call',
      'agent_artifact_marker_answer',
      'agent_quick_reasoning',
      'agent_cancel_mid_stream',
      'agent_ask_user_question',
      'chat_answer_with_citations',
      'chat_llm_unavailable',
      'agent_steered_during_tool',
      'agent_steered_after_answer',
    ])
    expect(CHAT_SCENARIOS.length).to.be.greaterThan(0)
  })

  for (const scenario of SCENARIOS) {
    it(`MG-3a ${scenario}: the legacy GET returns what the run saved: parts, citations, tools, status`, async () => {
      const persisted = await persistedRun(scenario)
      const saved = persisted.store.messagesOf(persisted.run.sessionId)
      const got = await legacyGet(persisted)
      expect(got.messages.map(pick)).to.deep.equal(saved.map(pick))
      expect(got.messages.map(citationIds)).to.deep.equal(saved.map(citationIds))
      expect(got.status).to.equal(persisted.store.session(persisted.run.sessionId)?.status)
      for (const message of got.messages) {
        expect(message, 'no internal addressing').to.not.have.any.keys('sessionId', 'orgId', 'seq')
      }
    })

    it(`MG-3b/MG-3e ${scenario}: legacy continues with the run's turns in its history (web, Slack and MCP build it the same way)`, async () => {
      const persisted = await persistedRun(scenario)
      const got = await legacyGet(persisted)
      const history = formatPreviousConversations(got.messages as unknown as IMessage[])
      const answers = got.messages.filter((m) => m['messageType'] === 'bot_response')
      expect(history[0]).to.deep.include({ role: 'user_query', content: persisted.journal.query })
      for (const answer of answers) {
        expect(history.map((turn) => turn.content)).to.include(answer['content'])
      }
      const steered = STEERED_SCENARIOS[scenario]
      if (steered !== undefined) {
        const at = history.findIndex((turn) => turn.role === 'user_query' && turn.content === steered)
        expect(at, 'the steering message is a user turn').to.be.greaterThan(0)
        expect(history.at(-1)!.role).to.equal('bot_response')
      } else {
        expect(history.filter((turn) => turn.role === 'user_query')).to.have.length(1)
      }
    })
  }

  it('MG-3a: the steered answer keeps its user_input part where the run took it, through the GET', async () => {
    const persisted = await persistedRun('agent_steered_during_tool')
    const got = await legacyGet(persisted)
    const answer = got.messages.find((m) => m['messageType'] === 'bot_response')!
    const parts = answer['parts'] as Array<Record<string, unknown>>
    expect(parts.map((p) => p['type'])).to.deep.equal(['text', 'tool_call', 'user_input', 'text'])
    expect(parts[2]).to.include({ content: STEERED_SCENARIOS['agent_steered_during_tool'], messageSeq: 1 })
  })
})

