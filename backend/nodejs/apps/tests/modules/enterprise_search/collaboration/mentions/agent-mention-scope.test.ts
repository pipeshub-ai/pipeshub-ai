import { expect } from 'chai'
import { Types } from 'mongoose'
import {
  agentMentionScope,
  allowedAgentMentions,
} from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/agent-mention-scope'
import { MentionValidator } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.validator'

describe('agentMentionScope (#16a, M1)', () => {
  it('is the chat’s own agent in an agent chat', () => {
    expect(agentMentionScope({ agentKey: 'agent-1' })).to.deep.equal(['agent-1'])
  })

  it('is empty in a chat that has no agent', () => {
    expect(agentMentionScope({})).to.deep.equal([])
  })
})

describe('picker and validator agree (#16a)', () => {
  const ORG = new Types.ObjectId()
  const KEYS = ['agent-1', 'agent-2']
  type Answer = boolean | 'unavailable'
  const answers: Answer[] = [true, false, 'unavailable']

  const validatorFor = (canExecute: Answer, serviceAccount: Answer) =>
    new MentionValidator({
      users: { displayNames: async () => new Map(), findByIds: async () => [] },
      teams: { callerTeamIds: async () => ({ status: 'ok', teamIds: [] }), teamsVersion: async () => 0, exists: async () => true, memberUserIds: async () => ({ status: 'unresolved' }) },
      agents: { canExecute: async () => canExecute, isServiceAccount: async () => serviceAccount },
    } as never)

  it('every offered agent is accepted, and every in-scope key the validator accepts is offered', async () => {
    let offeredTotal = 0
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
            const offered = await allowedAgentMentions(session as never, identity, agents)
            const validator = validatorFor(canExecute, sa)
            const accepted: string[] = []
            for (const key of KEYS) {
              const ok = await validator.validate([{ type: 'agent', id: key }] as never, { session: session as never, identity }).then(
                () => true,
                () => false,
              )
              if (ok) accepted.push(key)
            }
            expect(offered, JSON.stringify({ agentKey, shared, canExecute, sa })).to.deep.equal(accepted)
            offeredTotal += offered.length
          }
        }
      }
    }
    expect(offeredTotal).to.be.greaterThan(0)
  })
})
