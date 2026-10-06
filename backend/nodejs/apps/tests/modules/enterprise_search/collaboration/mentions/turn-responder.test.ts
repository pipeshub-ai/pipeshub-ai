import { expect } from 'chai'
import { guestAgentKeyOf, responderFor } from '../../../../../src/modules/enterprise_search/services/collaboration/turn/turn-responder'
import { buildAiChatRequest } from '../../../../../src/modules/enterprise_search/utils/ai-chat-payload'

describe('responder strategy (M2)', () => {
  it('a default chat is the assistant, an agent chat its own agent', () => {
    expect(responderFor({ kind: 'assistant' })).to.deep.equal({ kind: 'assistant', target: { kind: 'assistant' } })
    expect(responderFor({ kind: 'agent', agentKey: 'a1' })).to.deep.equal({ kind: 'own_agent', target: { kind: 'agent', agentKey: 'a1' } })
  })

  it('a guest agent overrides the turn’s target, in either kind of chat, and names itself for the rows', () => {
    for (const session of [{ kind: 'assistant' as const }, { kind: 'agent' as const, agentKey: 'a1' }]) {
      expect(responderFor(session, 'g9')).to.deep.equal({ kind: 'guest_agent', target: { kind: 'agent', agentKey: 'g9' }, respondingAgentKey: 'g9' })
    }
  })

  it('only an agent mention other than the chat’s own makes a guest', () => {
    const agent = (id: string) => ({ type: 'agent' as const, id })
    expect(guestAgentKeyOf([agent('a1')], 'a1')).to.equal(undefined)
    expect(guestAgentKeyOf([agent('a1')], undefined)).to.equal('a1')
    expect(guestAgentKeyOf([agent('g9')], 'a1')).to.equal('g9')
    expect(guestAgentKeyOf([{ type: 'assistant', id: 'self' }, { type: 'user', id: 'u1' }], undefined)).to.equal(undefined)
    expect(guestAgentKeyOf([], 'a1')).to.equal(undefined)
  })
})

describe('buildAiChatRequest for a guest agent', () => {
  const context = { previousConversations: [], isNewConversation: false, aclVersion: 1 }
  const body = { query: 'q', chatMode: 'agent:quick', tools: ['t.a'], agentCapabilities: { x: false }, callerEmail: 'a@b.c' }

  it('streams from the agent endpoint without the assistant’s tools, capabilities or caller context', () => {
    const { path, payload } = buildAiChatRequest({ kind: 'agent', agentKey: 'g9' }, body, { ...context, guestAgent: true })
    expect(path).to.equal('/api/v1/agent/g9/chat')
    expect(payload.chatMode).to.equal('quick')
    for (const key of ['tools', 'agentCapabilities', 'callerEmail']) expect(payload, key).to.not.have.property(key)
  })

  it('the chat’s own agent keeps forwarding them', () => {
    const { payload } = buildAiChatRequest({ kind: 'agent', agentKey: 'a1' }, { ...body, chatMode: 'quick' }, context)
    expect(payload.tools).to.deep.equal(['t.a'])
    expect(payload.agentCapabilities).to.deep.equal({ x: false })
  })
})
