import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { MentionValidator } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.validator'
import { MENTION_ERROR_CODES } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.errors'
import { orgWideTeamId } from '../../../../../src/modules/enterprise_search/services/collaboration/principals/principal-resolver'

const ORG = new Types.ObjectId()
const OTHER_ORG = new Types.ObjectId()
const [OWNER, BOB, CAROL, DAN, EVE, ROBOT, OFF, VIA_TEAM] = Array.from({ length: 8 }, () => String(new Types.ObjectId()))

const people: Record<string, { org: Types.ObjectId; kind?: 'human' | 'service'; disabled?: boolean }> = {
  [OWNER!]: { org: ORG },
  [BOB!]: { org: ORG },
  [CAROL!]: { org: ORG },
  [DAN!]: { org: ORG },
  [EVE!]: { org: OTHER_ORG },
  [ROBOT!]: { org: ORG, kind: 'service' },
  [OFF!]: { org: ORG, disabled: true },
  [VIA_TEAM!]: { org: ORG },
}

const identity = { userId: OWNER!, orgId: String(ORG), authHeaders: {}, requestKey: {} }

function build(session: Record<string, unknown> = {}, over: { teamMembers?: Record<string, string[]>; agents?: Record<string, unknown>; guest?: boolean } = {}) {
  const agents = {
    canExecute: sinon.stub().resolves(true),
    isServiceAccount: sinon.stub().resolves(false),
    ...over.agents,
  }
  const memberUserIds = sinon.stub().callsFake(async (teamId: string) =>
    over.teamMembers?.[teamId] ? { status: 'ok', userIds: over.teamMembers[teamId] } : { status: 'unresolved' },
  )
  const validator = new MentionValidator({
    users: {
      displayNames: async () => new Map(),
      findByIds: async (org, ids) =>
        ids.flatMap((id) =>
          people[id] && String(people[id]!.org) === org
            ? [{ userId: id, displayName: id, kind: people[id]!.kind ?? 'human', isDisabled: people[id]!.disabled === true }]
            : [],
        ),
    },
    teams: { callerTeamIds: async () => ({ status: 'ok', teamIds: [] }), teamsVersion: async () => 0, exists: async () => true, memberUserIds },
    agents: agents as never,
    flags: { isEnabled: async () => over.guest === true },
  })
  const base = {
    _id: new Types.ObjectId(),
    orgId: ORG,
    userId: new Types.ObjectId(OWNER),
    sharedWith: [{ userId: new Types.ObjectId(BOB), accessLevel: 'read' }, { teamId: 'team-sales', accessLevel: 'write' }],
    ...session,
  }
  const full = (mentions: Array<{ type: string; id: string }>) => validator.validate(mentions as never, { session: base as never, identity })
  const run = async (mentions: Array<{ type: string; id: string }>) => (await full(mentions)).mentions
  return { run, full, agents, memberUserIds }
}

const reject = async (promise: Promise<unknown>) => {
  try {
    await promise
  } catch (error) {
    return error as { statusCode: number; code: string; publicDetails?: Record<string, unknown> }
  }
  throw new Error('expected the validator to reject')
}

describe('mention validator (MN-05, MN-06, PH10-11)', () => {
  it('accepts the assistant, a participant, the owner and a chat team, de-duplicated', async () => {
    const { run } = build()
    const out = await run([{ type: 'assistant', id: 'self' }, { type: 'user', id: BOB! }, { type: 'user', id: BOB! }, { type: 'user', id: OWNER! }, { type: 'team', id: 'team-sales' }])
    expect(out).to.deep.equal([
      { type: 'assistant', id: 'self' },
      { type: 'user', id: BOB },
      { type: 'user', id: OWNER },
      { type: 'team', id: 'team-sales' },
    ])
  })

  it('rejects a user of another org as unknown, naming the index, with 400', async () => {
    const { run } = build({ sharedWith: [{ userId: new Types.ObjectId(EVE), accessLevel: 'read' }] })
    const e = await reject(run([{ type: 'assistant', id: 'self' }, { type: 'user', id: EVE! }]))
    expect(e).to.include({ statusCode: 400, code: MENTION_ERROR_CODES.NOT_ALLOWED })
    expect(e.publicDetails).to.deep.equal({ mentionIndex: 1, reason: 'unknown_user' })
  })

  it('rejects an id that is no user at all', async () => {
    const e = await reject(build().run([{ type: 'user', id: String(new Types.ObjectId()) }]))
    expect(e.publicDetails).to.deep.equal({ mentionIndex: 0, reason: 'unknown_user' })
  })

  it('MN-12: an active org colleague outside the chat is accepted, kept as a chip and reported as a non-participant', async () => {
    const out = await build({}, { teamMembers: { 'team-sales': [] } }).full([{ type: 'user', id: BOB! }, { type: 'user', id: DAN! }])
    expect(out.mentions).to.deep.equal([{ type: 'user', id: BOB }, { type: 'user', id: DAN }])
    expect(out.nonParticipants).to.deep.equal([DAN])
  })

  it('MN-12: participants (direct, owner, via team) are never reported', async () => {
    const out = await build({}, { teamMembers: { 'team-sales': [VIA_TEAM!] } }).full([{ type: 'user', id: BOB! }, { type: 'user', id: OWNER! }, { type: 'user', id: VIA_TEAM! }])
    expect(out.nonParticipants).to.deep.equal([])
  })

  it('rejects a disabled user and a service user even when they are in the chat', async () => {
    const { run } = build({ sharedWith: [{ userId: new Types.ObjectId(OFF), accessLevel: 'read' }, { userId: new Types.ObjectId(ROBOT), accessLevel: 'read' }] })
    expect((await reject(run([{ type: 'user', id: OFF! }]))).publicDetails).to.include({ reason: 'unknown_user' })
    expect((await reject(run([{ type: 'user', id: ROBOT! }]))).publicDetails).to.include({ reason: 'unknown_user' })
  })

  it('accepts a user who reaches the chat through a team, and does not look teams up for direct participants', async () => {
    const { run, memberUserIds } = build({}, { teamMembers: { 'team-sales': [VIA_TEAM!] } })
    await run([{ type: 'user', id: BOB! }])
    expect(memberUserIds.called).to.equal(false)
    expect(await run([{ type: 'user', id: VIA_TEAM! }])).to.have.length(1)
    expect((await build({}, { teamMembers: { 'team-sales': [VIA_TEAM!] } }).full([{ type: 'user', id: DAN! }])).nonParticipants).to.deep.equal([DAN])
  })

  it('fails closed with 503 when a chat team cannot be expanded and the user is not otherwise reached', async () => {
    const out = await reject(build().run([{ type: 'user', id: VIA_TEAM! }]))
    expect(out).to.include({ statusCode: 503, code: MENTION_ERROR_CODES.DIRECTORY_UNAVAILABLE })
  })

  it('in an org-wide chat every active org user is a participant, still not another org’s', async () => {
    const { run } = build({ sharedWith: [{ teamId: orgWideTeamId(String(ORG)), accessLevel: 'read' }] })
    expect(await run([{ type: 'user', id: DAN! }])).to.have.length(1)
    expect((await reject(run([{ type: 'user', id: EVE! }]))).publicDetails).to.include({ reason: 'unknown_user' })
    expect((await reject(run([{ type: 'team', id: orgWideTeamId(String(ORG)) }]))).publicDetails).to.include({ reason: 'not_a_chat_team' })
  })

  it('rejects a team that is not a principal of the chat, whoever the sender is', async () => {
    const e = await reject(build().run([{ type: 'team', id: 'team-ops' }]))
    expect(e).to.include({ statusCode: 400, code: MENTION_ERROR_CODES.NOT_ALLOWED })
    expect(e.publicDetails).to.deep.equal({ mentionIndex: 0, reason: 'not_a_chat_team' })
  })

  it('rejects an assistant token with an id other than self', async () => {
    const e = await reject(build().run([{ type: 'assistant', id: 'someone' }]))
    expect(e.publicDetails).to.include({ reason: 'invalid_assistant' })
  })

  describe('agent tokens', () => {
    const agentChat = { agentKey: 'agent-1' }

    it('MN-05: an agent the sender cannot execute is 403 MENTION_NOT_ALLOWED', async () => {
      const { run, agents } = build(agentChat, { agents: { canExecute: sinon.stub().resolves(false) } })
      const e = await reject(run([{ type: 'agent', id: 'agent-1' }]))
      expect(e).to.include({ statusCode: 403, code: MENTION_ERROR_CODES.NOT_ALLOWED })
      expect(e.publicDetails).to.deep.equal({ mentionIndex: 0, reason: 'agent_not_allowed' })
      expect(agents.canExecute.firstCall.args[1]).to.equal('agent-1')
    })

    it('rejects any agent other than the chat’s own, and any agent in a default chat', async () => {
      const own = build(agentChat)
      expect((await reject(own.run([{ type: 'agent', id: 'agent-2' }]))).publicDetails).to.include({ reason: 'agent_not_in_chat' })
      expect(own.agents.canExecute.called).to.equal(false)
      expect((await reject(build().run([{ type: 'agent', id: 'agent-1' }]))).publicDetails).to.include({ reason: 'agent_not_in_chat' })
    })

    it('#16a: a participant who did not create the agent but may run it is accepted; one who may not is refused', async () => {
      const byCaller = (who: { userId: string }) => who.userId === OWNER
      const ok = build(agentChat, { agents: { canExecute: sinon.stub().callsFake(async (who) => byCaller(who)) } })
      expect(await ok.run([{ type: 'agent', id: 'agent-1' }])).to.have.length(1)
      const no = build(agentChat, { agents: { canExecute: sinon.stub().callsFake(async (who) => !byCaller(who)) } })
      expect((await reject(no.run([{ type: 'agent', id: 'agent-1' }]))).publicDetails).to.include({ reason: 'agent_not_allowed' })
    })

    it('accepts the own agent when the sender may run it', async () => {
      expect(await build(agentChat).run([{ type: 'agent', id: 'agent-1' }])).to.deep.equal([{ type: 'agent', id: 'agent-1' }])
    })

    it('MN-06: a service-account agent is refused in a shared chat and fine in a private one', async () => {
      const sa = { isServiceAccount: sinon.stub().resolves(true) }
      const shared = await reject(build(agentChat, { agents: sa }).run([{ type: 'agent', id: 'agent-1' }]))
      expect(shared).to.include({ statusCode: 403, code: MENTION_ERROR_CODES.SA_AGENT_SHARED })
      const solo = build({ ...agentChat, sharedWith: [] }, { agents: sa })
      expect(await solo.run([{ type: 'agent', id: 'agent-1' }])).to.have.length(1)
    })

    it('fails closed with 503 when the agent directory cannot answer', async () => {
      const e = await reject(build(agentChat, { agents: { canExecute: sinon.stub().resolves('unavailable') } }).run([{ type: 'agent', id: 'agent-1' }]))
      expect(e).to.include({ statusCode: 503, code: MENTION_ERROR_CODES.DIRECTORY_UNAVAILABLE })
      const sa = await reject(build(agentChat, { agents: { isServiceAccount: sinon.stub().resolves('unavailable') } }).run([{ type: 'agent', id: 'agent-1' }]))
      expect(sa.statusCode).to.equal(503)
    })

    describe('guest agents (agent builder flag on)', () => {
      it('accepts any agent the sender may run, in a default chat and in another agent’s chat, and still the own agent', async () => {
        for (const session of [{}, agentChat]) {
          const w = build(session, { guest: true })
          expect(await w.run([{ type: 'agent', id: 'agent-2' }])).to.deep.equal([{ type: 'agent', id: 'agent-2' }])
        }
        expect(await build(agentChat, { guest: true }).run([{ type: 'agent', id: 'agent-1' }])).to.have.length(1)
      })

      it('an agent the sender cannot run is still 403 agent_not_allowed', async () => {
        const w = build({}, { guest: true, agents: { canExecute: sinon.stub().resolves(false) } })
        expect((await reject(w.run([{ type: 'agent', id: 'agent-2' }]))).publicDetails).to.include({ reason: 'agent_not_allowed' })
      })

      it('a service-account agent is refused in a shared chat and allowed in a solo one', async () => {
        const sa = { isServiceAccount: sinon.stub().resolves(true) }
        const shared = await reject(build({}, { guest: true, agents: sa }).run([{ type: 'agent', id: 'agent-2' }]))
        expect(shared).to.include({ statusCode: 403, code: MENTION_ERROR_CODES.SA_AGENT_SHARED })
        expect(await build({ sharedWith: [] }, { guest: true, agents: sa }).run([{ type: 'agent', id: 'agent-2' }])).to.have.length(1)
      })

      it('two agents in one message are 422 TOO_MANY_AGENT_MENTIONS before any agent is looked up', async () => {
        const w = build({}, { guest: true })
        const e = await reject(w.run([{ type: 'agent', id: 'agent-2' }, { type: 'agent', id: 'agent-3' }]))
        expect(e).to.include({ statusCode: 422, code: MENTION_ERROR_CODES.TOO_MANY_AGENT_MENTIONS })
        expect(e.publicDetails).to.deep.equal({ max: 1 })
        expect(w.agents.canExecute.called).to.equal(false)
      })

      it('the same agent twice is one mention; an agent with the assistant is fine', async () => {
        const w = build({}, { guest: true })
        expect(await w.run([{ type: 'agent', id: 'agent-2' }, { type: 'agent', id: 'agent-2' }, { type: 'assistant', id: 'self' }])).to.have.length(2)
      })
    })

    it('an alias-only message never consults the agent directory', async () => {
      const { run, agents } = build(agentChat)
      await run([{ type: 'assistant', id: 'self' }])
      expect(agents.canExecute.called).to.equal(false)
    })
  })
})
