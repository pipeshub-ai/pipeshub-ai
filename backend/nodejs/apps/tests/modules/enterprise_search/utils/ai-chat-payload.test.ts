import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { buildAiChatRequest, parseChatMode } from '../../../../src/modules/enterprise_search/utils/ai-chat-payload'

const context = (extra: Record<string, unknown> = {}) => ({
  conversationId: 'c1',
  previousConversations: [{ role: 'user_query', content: 'earlier' }],
  isNewConversation: false,
  ...extra,
})

describe('enterprise_search/utils/ai-chat-payload', () => {
  afterEach(() => sinon.restore())

  describe('parseChatMode', () => {
    it('maps agent and agent:<mode> to agent mode', () => {
      expect(parseChatMode('agent')).to.deep.equal({ chatMode: 'quick', agentMode: true })
      expect(parseChatMode('agent:deep')).to.deep.equal({ chatMode: 'deep', agentMode: true })
    })

    it('passes other modes through and defaults to quick', () => {
      expect(parseChatMode('web_search')).to.deep.equal({ chatMode: 'web_search', agentMode: false })
      expect(parseChatMode(undefined)).to.deep.equal({ chatMode: 'quick', agentMode: false })
    })
  })

  describe('buildAiChatRequest', () => {
    it('sends a search turn to /chat with nulls for absent optional fields', () => {
      const { path, payload } = buildAiChatRequest({ kind: 'assistant' }, { query: 'q', chatMode: 'internal_search' }, context())

      expect(path).to.equal('/api/v1/chat')
      expect(payload).to.deep.equal({
        query: 'q',
        previousConversations: [{ role: 'user_query', content: 'earlier' }],
        filters: {},
        attachments: [],
        modelKey: null,
        modelName: null,
        modelFriendlyName: null,
        reasoningEffort: null,
        timezone: null,
        currentTime: null,
        conversationId: 'c1',
        runId: null,
        chatMode: 'internal_search',
      })
    })

    it('adds recordIds only when the turn creates the conversation', () => {
      const body = { query: 'q', recordIds: ['r1'] }
      expect(buildAiChatRequest({ kind: 'assistant' }, body, context({ isNewConversation: true })).payload.recordIds).to.deep.equal(['r1'])
      expect(buildAiChatRequest({ kind: 'assistant' }, body, context()).payload).not.to.have.property('recordIds')
    })

    it('runs an assistant agent turn on the universal agent with tools and capabilities', () => {
      const { path, payload } = buildAiChatRequest(
        { kind: 'assistant' },
        { query: 'q', chatMode: 'agent:deep', tools: ['a.b'], agentCapabilities: { webSearch: false } },
        context(),
      )

      expect(path).to.equal('/api/v1/agent/agentIdPlaceholder/chat')
      expect(payload).to.include({ chatMode: 'deep' })
      expect(payload.tools).to.deep.equal(['a.b'])
      expect(payload.agentCapabilities).to.deep.equal({ webSearch: false })
    })

    it('never forwards tools for a non-agent assistant turn', () => {
      const { payload } = buildAiChatRequest({ kind: 'assistant' }, { query: 'q', tools: ['a.b'] }, context())
      expect(payload).not.to.have.property('tools')
      expect(payload).not.to.have.property('agentCapabilities')
    })

    it('sends an agent turn to its own route, escaping the key, with caller context', () => {
      const { path, payload } = buildAiChatRequest(
        { kind: 'agent', agentKey: 'a/b c' },
        { query: 'q', tools: [], callerDisplayName: ' Ada ', callerEmail: 'ada@example.com', quickMode: true },
        context({ isNewConversation: true }),
      )

      expect(path).to.equal('/api/v1/agent/a%2Fb%20c/chat')
      // Python treats `auto` (or anything unknown) as "run the tier classifier"; a scoped
      // agent is quick-only, so an omitted mode must not fall through to it.
      expect(payload).to.include({ chatMode: 'quick', quickMode: true, callerDisplayName: 'Ada', callerEmail: 'ada@example.com' })
      expect(payload.tools, 'an explicit empty list disables tools').to.deep.equal([])
    })

    it('omits quickMode on an agent follow-up and tools when none were sent', () => {
      const { payload } = buildAiChatRequest({ kind: 'agent', agentKey: 'a1' }, { query: 'q' }, context())
      expect(payload).not.to.have.property('quickMode')
      expect(payload).not.to.have.property('tools')
    })

    it('applies the project scope after the request tools, so the project narrows them', () => {
      sinon.stub(ProjectService, 'buildContext').returns({
        projectId: 'p1',
        instructions: 'Answer as the release team.',
        knowledgeScope: { apps: [], kb: [] },
        tools: ['jira.search'],
        linkedKnowledgeBaseId: null,
      } as never)

      const { payload } = buildAiChatRequest(
        { kind: 'agent', agentKey: 'a1' },
        { query: 'q', tools: ['jira.search', 'gmail.send'] },
        context({ project: {} as never }),
      )

      expect(payload.projectInstructions).to.equal('Answer as the release team.')
      expect(payload.tools).to.deep.equal(['jira.search'])
    })
  })
})
