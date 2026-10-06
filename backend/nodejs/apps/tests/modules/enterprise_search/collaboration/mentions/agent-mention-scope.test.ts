import { expect } from 'chai'
import { Types } from 'mongoose'
import {
  AGENT_MENTIONS_MAX,
  allowedAgentMentions,
  inAgentMentionScope,
} from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/agent-mention-scope'
import { MentionValidator } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.validator'

describe('inAgentMentionScope (#16a, M2)', () => {
  it('without guest agents only the chat’s own agent is in scope', () => {
    expect(inAgentMentionScope({ agentKey: 'agent-1' }, false, 'agent-1')).to.equal(true)
    expect(inAgentMentionScope({ agentKey: 'agent-1' }, false, 'agent-2')).to.equal(false)
    expect(inAgentMentionScope({}, false, 'agent-1')).to.equal(false)
  })

  it('with guest agents every agent is in scope, in a default chat and in another agent’s chat', () => {
    expect(inAgentMentionScope({}, true, 'agent-2')).to.equal(true)
    expect(inAgentMentionScope({ agentKey: 'agent-1' }, true, 'agent-2')).to.equal(true)
  })

  it('one agent per message', () => {
    expect(AGENT_MENTIONS_MAX).to.equal(1)
  })
})

describe('picker and validator agree (#16a)', () => {
  const ORG = new Types.ObjectId()
  const KEYS = ['agent-1', 'agent-2']
  type Answer = boolean | 'unavailable'
  const answers: Answer[] = [true, false, 'unavailable']

  const validatorFor = (canExecute: Answer, serviceAccount: Answer, guest: boolean) =>
    new MentionValidator({
      flags: { isEnabled: async () => guest },
      users: { displayNames: async () => new Map(), findByIds: async () => [] },
      teams: { callerTeamIds: async () => ({ status: 'ok', teamIds: [] }), teamsVersion: async () => 0, exists: async () => true, memberUserIds: async () => ({ status: 'unresolved' }) },
      agents: { canExecute: async () => canExecute, isServiceAccount: async () => serviceAccount },
    } as never)

  it('every offered agent is accepted, and every candidate the validator accepts is offered, with and without guest agents', async () => {
    let offeredTotal = 0
    for (const guest of [false, true]) {
    for (const agentKey of [undefined, 'agent-1']) {
      for (const shared of [false, true]) {
        for (const canExecute of answers) {
          for (const sa of answers) {
            const session = {
              _id: new Types.ObjectId(),
              orgId: ORG,
              userId: new Types.ObjectId(),
              ...(agentKey && { agentKey }),
              sharedWith: shared ? [{ userId: new Types.ObjectId(), accessLevel: 'read' }] : [],
            }
            const identity = { userId: String(new Types.ObjectId()), orgId: String(ORG), authHeaders: {}, requestKey: {} }
            const agents = { canExecute: async () => canExecute, isServiceAccount: async () => sa }
            const offered = await allowedAgentMentions(session as never, identity, agents, KEYS, guest)
            const validator = validatorFor(canExecute, sa, guest)
            const accepted: string[] = []
            for (const key of KEYS) {
              const ok = await validator.validate([{ type: 'agent', id: key }] as never, { session: session as never, identity }).then(
                () => true,
                () => false,
              )
              if (ok) accepted.push(key)
            }
            expect(offered, JSON.stringify({ guest, agentKey, shared, canExecute, sa })).to.deep.equal(accepted)
            offeredTotal += offered.length
          }
        }
      }
    }
    }
    expect(offeredTotal).to.be.greaterThan(0)
  })
})
