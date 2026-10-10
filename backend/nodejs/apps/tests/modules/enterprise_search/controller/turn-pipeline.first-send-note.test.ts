import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Kind, turnWorld } from '../helpers/turn-world'
import { ACTORS } from '../helpers/conversation-world'
import { ChatAccessLoader } from '../../../../src/modules/authz/loaders/chat.loader'

const B = String(ACTORS.B.userId)
const C = String(ACTORS.C.userId)
const person = (id: string) => ({ type: 'user', id })
const draft = { collaborators: [{ principalType: 'user', principalId: B, accessLevel: 'write' }] }

const world = (over: { enabled?: boolean } = {}) => {
  const mentioned = sinon.stub().resolves()
  const sharing = { validate: sinon.stub().resolves(), apply: sinon.stub().resolves() }
  const w = turnWorld(
    {},
    {
      mentions: { enabled: over.enabled },
      sharing: sharing as never,
      deps: { producers: () => ({ mentioned }) as never, users: { displayNames: async () => new Map([[B, 'Bob']]), findByIds: async () => [] } },
    },
  )
  return { w, mentioned, sharing }
}

describe('first send that mentions only people', () => {
  beforeEach(() => {
    // The notification reads the chat back for its audience; the in-memory store stands in for Mongo.
    sinon.stub(ChatAccessLoader.prototype, 'loadScoped').callsFake(async () => ({ session: { _id: 's' } as never, project: null }))
  })
  afterEach(() => sinon.restore())

  for (const kind of ['chat', 'agent'] as const satisfies readonly Kind[]) {
    describe(kind, () => {
      it('stores a note, notifies the person and starts no AI run', async () => {
        const { w, mentioned } = world()
        const turn = await w.startCreate('A', kind, 'stream', {
          query: `<@user:${B}> can you look at this?`,
          mentions: [person(B)],
          clientMessageId: 'k1',
          share: draft,
        })
        expect(turn.error).to.equal(undefined)
        expect(w.s.ai.calls).to.have.length(0)
        const [conversation] = w.created()
        expect(conversation).to.include({ title: '@Bob can you look at this?', status: 'Complete' })
        expect(conversation!.activeRun ?? undefined).to.equal(undefined)
        const rows = w.rowsOf(conversation!._id)
        expect(rows).to.have.length(1)
        expect(rows[0]).to.include({ messageType: 'note', clientMessageId: 'k1' })
        expect(rows[0]!.mentions).to.deep.equal([person(B)])
        expect(mentioned.calledOnce).to.equal(true)
        expect(mentioned.firstCall.args[0]).to.deep.include({ actorUserId: String(ACTORS.A.userId), mentions: [person(B)] })
        const [first] = turn.res.events()
        expect(first!.data.value).to.deep.include({ note: true, title: '@Bob can you look at this?' })
        expect(first!.data.value.runId).to.equal(undefined)
        const finished = turn.res.eventsOf('RUN_FINISHED')[0]!
        expect(finished.data.result.conversation.messages.map((m: any) => m.messageType)).to.deep.equal(['note'])
        expect(turn.res.writableEnded).to.equal(true)
      })
    })
  }

  it('reports a colleague outside the chat', async () => {
    const { w } = world()
    const turn = await w.startCreate('A', 'chat', 'stream', { query: `<@user:${C}> hi`, mentions: [person(C)] })
    expect(turn.res.events()[0]!.data.value.nonParticipants).to.deep.equal([C])
    expect(w.created()).to.have.length(1)
    expect(w.s.ai.calls).to.have.length(0)
  })

  it('applies the draft share to the new chat before the answer to the sender', async () => {
    const { w, sharing } = world()
    const turn = await w.startCreate('A', 'chat', 'stream', { query: `<@user:${B}> hi`, mentions: [person(B)], share: draft })
    expect(turn.error).to.equal(undefined)
    expect(sharing.validate.calledBefore(sharing.apply)).to.equal(true)
    expect(sharing.apply.firstCall.args[2]).to.deep.include({ id: String(w.created()[0]!._id), kind: 'chat' })
  })

  it('a share that fails removes the chat and its note, and nobody is notified', async () => {
    const { w, mentioned, sharing } = world()
    sharing.apply.rejects(new Error('limit'))
    const turn = await w.startCreate('A', 'chat', 'stream', { query: `<@user:${B}> hi`, mentions: [person(B)], share: draft })
    expect(turn.error).to.be.instanceOf(Error)
    expect(w.created()).to.have.length(0)
    expect(mentioned.called).to.equal(false)
  })

  it('a retry of the same clientMessageId is a 409 and neither duplicates the note nor notifies twice', async () => {
    const { w, mentioned } = world()
    const body = { query: `<@user:${B}> hi`, mentions: [person(B)], clientMessageId: 'same', share: draft }
    await w.startCreate('A', 'chat', 'stream', body)
    const retry = await w.startCreate('A', 'chat', 'stream', body)
    expect(retry.error).to.include({ statusCode: 409 })
    expect(w.created()).to.have.length(1)
    expect(w.rowsOf(w.created()[0]!._id)).to.have.length(1)
    expect(mentioned.callCount).to.equal(1)
  })

  it('the non-streaming create answers 201 with the note and calls no AI backend', async () => {
    const { w } = world()
    const turn = await w.startCreate('A', 'chat', 'plain', { query: `<@user:${B}> hi`, mentions: [person(B)], share: draft })
    expect(turn.error).to.equal(undefined)
    expect(turn.res.statusCode).to.equal(201)
    const body = turn.res.jsonBody as any
    expect(body.note).to.equal(true)
    expect(body.conversation.messages.map((m: any) => m.messageType)).to.deep.equal(['note'])
    expect(w.s.ai.calls).to.have.length(0)
  })
})

describe('first send that still asks the AI', () => {
  afterEach(() => sinon.restore())

  it('a person plus @assistant is answered', async () => {
    const { w, mentioned } = world()
    const turn = await w.startCreate('A', 'chat', 'stream', {
      query: `<@user:${B}> <@assistant:self> summarize`,
      mentions: [person(B), { type: 'assistant', id: 'self' }],
      share: draft,
    })
    expect(turn.error).to.equal(undefined)
    expect(w.s.ai.streamCalls).to.have.length(1)
    expect(w.rowsOf(w.created()[0]!._id).map((r) => r.messageType)).to.deep.equal(['user_query'])
    expect(turn.res.events()[0]!.data.value.note).to.equal(undefined)
    expect(mentioned.called).to.equal(false)
    w.s.ai.finish()
  })

  it('a message with no mentions is answered', async () => {
    const { w } = world()
    const turn = await w.startCreate('A', 'chat', 'stream', { query: 'What is our PTO policy?' })
    expect(turn.error).to.equal(undefined)
    expect(w.s.ai.streamCalls).to.have.length(1)
    w.s.ai.finish()
  })

  it('with the mentions off a person tag is answered as before', async () => {
    const { w } = world({ enabled: false })
    const turn = await w.startCreate('A', 'chat', 'stream', { query: `<@user:${B}> hi`, mentions: [person(B)] })
    expect(turn.error).to.equal(undefined)
    expect(w.s.ai.streamCalls).to.have.length(1)
    expect(w.rowsOf(w.created()[0]!._id).map((r) => r.messageType)).to.deep.equal(['user_query'])
    w.s.ai.finish()
  })
})
