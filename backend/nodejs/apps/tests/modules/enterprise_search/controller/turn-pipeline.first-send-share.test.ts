import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Kind, turnWorld } from '../helpers/turn-world'
import { ACTORS } from '../helpers/conversation-world'
import { InvalidPrincipalError } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { MENTION_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.errors'

const share = { collaborators: [{ principalType: 'user', principalId: String(ACTORS.B.userId), accessLevel: 'write' }], note: 'See this' }

const world = (sharing: Record<string, unknown> = {}, over: { collab?: boolean; mentions?: boolean } = {}) => {
  const log: string[] = []
  const stub = {
    validate: sinon.stub().callsFake(async () => void log.push('validate')),
    apply: sinon.stub().callsFake(async () => void log.push('apply')),
    ...sharing,
  }
  const w = turnWorld({}, { collab: over.collab, sharing: stub as never, mentions: over.mentions ? { guestAgents: true } : undefined })
  return { w, stub, log }
}

describe('first send with a share block (draft collaborators)', () => {
  afterEach(() => sinon.restore())

  for (const kind of ['chat', 'agent'] as const satisfies readonly Kind[]) {
    describe(kind, () => {
      it('validates before the chat exists, applies after it and its first row exist, and before the stream opens', async () => {
        const { w, stub, log } = world()
        w.s.store.writes.length = 0
        const turn = await w.startCreate('A', kind, 'stream', { share, clientMessageId: 'k1' })
        expect(turn.error).to.equal(undefined)
        expect(log).to.deep.equal(['validate', 'apply'])
        const [conversation] = w.created()
        expect(w.rowsOf(conversation!._id).map((r) => r.messageType)).to.include('user_query')
        const [caller, , target, input] = stub.apply.firstCall.args
        expect(caller).to.deep.equal({ userId: String(ACTORS.A.userId), orgId: String(ACTORS.A.orgId), teamIds: 'unresolved' })
        expect(target).to.deep.include({ id: String(conversation!._id), kind })
        expect(input.collaborators).to.have.length(1)
        expect(input.note).to.equal('See this')
        // The share lands after the session write and before the stream's SSE head.
        const writes = w.s.store.writes
        expect(writes.indexOf('writeHead')).to.be.greaterThan(-1)
        expect(w.s.ai.streamCalls).to.have.length(1)
        w.s.ai.finish()
      })

      it('an invalid share creates nothing: no chat, no row, no AI call, no event', async () => {
        const { w, stub } = world({ validate: sinon.stub().rejects(new InvalidPrincipalError([{ key: 'user:x', reason: 'other_org' } as never])) })
        const turn = await w.startCreate('A', kind, 'stream', { share })
        expect(turn.error).to.include({ code: 'INVALID_PRINCIPAL' })
        expect(w.created()).to.have.length(0)
        expect(stub.apply.called).to.equal(false)
        expect(w.s.ai.streamCalls).to.have.length(0)
        expect(w.s.store.writes).to.not.include('writeHead')
      })

      it('a share that fails after the chat was created removes the chat and its rows, and the stream never opens', async () => {
        const { w } = world({ apply: sinon.stub().rejects(new Error('limit')) })
        const turn = await w.startCreate('A', kind, 'stream', { share })
        expect(turn.error).to.be.instanceOf(Error)
        expect(w.created()).to.have.length(0)
        expect(w.s.ai.streamCalls).to.have.length(0)
        expect(w.s.store.writes).to.not.include('writeHead')
      })

      it('non-streaming create shares the same way', async () => {
        const { w, log } = world()
        w.s.ai.reply(kind === 'chat' ? /\/api\/v1\/chat$/ : /\/agent\/agent-1\/chat$/, 200, { answer: 'ok', citations: [], confidence: 'High' })
        const turn = await w.startCreate('A', kind, 'plain', { share })
        expect(turn.error).to.equal(undefined)
        expect(log).to.deep.equal(['validate', 'apply'])
        expect(w.created()).to.have.length(1)
      })

      it('a first send without a share block never touches sharing', async () => {
        const { w, stub } = world()
        const turn = await w.startCreate('A', kind, 'stream', {})
        expect(turn.error).to.equal(undefined)
        expect(stub.validate.called || stub.apply.called).to.equal(false)
        w.s.ai.finish()
      })
    })
  }

  it('a retried first send (same clientMessageId) is refused before sharing, so nothing is shared twice', async () => {
    const { w, stub } = world()
    const first = await w.startCreate('A', 'chat', 'stream', { share, clientMessageId: 'same' })
    w.s.ai.finish()
    await first.res.ended
    const retry = await w.startCreate('A', 'chat', 'stream', { share, clientMessageId: 'same' })
    expect(retry.error).to.include({ statusCode: 409 })
    expect(stub.apply.callCount).to.equal(1)
    expect(w.created()).to.have.length(1)
  })

  it('with collaborative chats off a share is a 404 before any SSE head and nothing is created', async () => {
    const { w, stub } = world({}, { collab: false })
    const turn = await w.startCreate('A', 'chat', 'stream', { share })
    expect(turn.error).to.include({ statusCode: 404 })
    expect(stub.validate.called).to.equal(false)
    expect(w.created()).to.have.length(0)
    expect(w.s.store.writes).to.not.include('writeHead')
  })

  it('a first send without any share still streams with the flag off, as before', async () => {
    const { w } = world({}, { collab: false })
    const turn = await w.startCreate('A', 'chat', 'stream', {})
    expect(turn.error).to.equal(undefined)
    expect(w.created()).to.have.length(1)
    w.s.ai.finish()
  })

  describe('mentions in the first message', () => {
    const userMention = (who: 'B' | 'C') => ({ type: 'user', id: String(ACTORS[who].userId) })

    it('a draft collaborator is a participant, so no non-participant is reported; another colleague is', async () => {
      const { w } = world({}, { mentions: true })
      const turn = await w.startCreate('A', 'chat', 'stream', { share, mentions: [userMention('B'), userMention('C')] })
      expect(turn.error).to.equal(undefined)
      const first = turn.res.events()[0]!
      expect(first.data.value.nonParticipants).to.deep.equal([String(ACTORS.C.userId)])
      w.s.ai.finish()
    })

    it('without a draft the mentioned colleague is reported, and a draft colleague alone reports none', async () => {
      const { w } = world({}, { mentions: true })
      const t1 = await w.startCreate('A', 'chat', 'stream', { mentions: [userMention('B')] })
      expect(t1.res.events()[0]!.data.value.nonParticipants).to.deep.equal([String(ACTORS.B.userId)])
      w.s.ai.finish()
      const w2 = world({}, { mentions: true })
      const t2 = await w2.w.startCreate('A', 'chat', 'stream', { share, mentions: [userMention('B')] })
      expect(t2.res.events()[0]!.data.value.nonParticipants).to.equal(undefined)
      w2.w.s.ai.finish()
    })

    it('an invalid mention refuses the first send before anything is created', async () => {
      const { w } = world({}, { mentions: true })
      const turn = await w.startCreate('A', 'chat', 'stream', { mentions: [{ type: 'team', id: 'not-a-chat-team' }] })
      expect(turn.error).to.include({ code: MENTION_ERROR_CODES.NOT_ALLOWED })
      expect(w.created()).to.have.length(0)
    })
  })
})
