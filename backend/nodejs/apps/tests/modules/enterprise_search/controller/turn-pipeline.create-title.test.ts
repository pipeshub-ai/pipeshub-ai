import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { turnWorld } from '../helpers/turn-world'
import { ACTORS } from '../helpers/conversation-world'

const B = String(ACTORS.B.userId)

describe('title of a first send with mentions', () => {
  afterEach(() => sinon.restore())

  const users = { displayNames: async () => new Map([[B, 'Bob']]), findByIds: async () => [] }

  it('names a mentioned colleague in the created chat title and in the conversation_created frame', async () => {
    const w = turnWorld({}, { mentions: {}, deps: { users } })
    const query = `<@user:${B}> can you look? <@assistant:self> thanks`
    const turn = await w.startCreate('A', 'chat', 'stream', { query, mentions: [{ type: 'user', id: B }] })
    expect(turn.error).to.equal(undefined)
    const [conversation] = w.created()
    expect(conversation!.title).to.equal('@Bob can you look? thanks')
    expect(turn.res.events()[0]!.data.value.title).to.equal('@Bob can you look? thanks')
    w.s.ai.finish()
  })

  it('names a mentioned agent from its profile', async () => {
    const profiles = { describe: sinon.stub().resolves({ name: 'Joke Buddy' }) }
    const w = turnWorld({}, { mentions: { guestAgents: true, profiles }, deps: { agents: profiles } })
    const query = '<@agent:agent-9> hi; <@assistant:self> tell jokes'
    const turn = await w.startCreate('A', 'chat', 'stream', {
      query,
      mentions: [{ type: 'agent', id: 'agent-9' }, { type: 'assistant', id: 'self' }],
    })
    expect(turn.error).to.equal(undefined)
    expect(w.created()[0]!.title).to.equal('@Joke Buddy hi; tell jokes')
    w.s.ai.finish()
  })

  it('drops the tokens when the label lookup fails, and the send still succeeds', async () => {
    const failing = { displayNames: sinon.stub().rejects(new Error('directory down')), findByIds: async () => [] }
    const w = turnWorld({}, { mentions: {}, deps: { users: failing } })
    const turn = await w.startCreate('A', 'chat', 'stream', {
      query: `<@user:${B}> can you look?`,
      mentions: [{ type: 'user', id: B }],
    })
    expect(turn.error).to.equal(undefined)
    expect(w.created()[0]!.title).to.equal('can you look?')
    w.s.ai.finish()
  })

  it('makes no lookup without mentions', async () => {
    const displayNames = sinon.stub().resolves(new Map())
    const w = turnWorld({}, { mentions: {}, deps: { users: { displayNames, findByIds: async () => [] } } })
    const turn = await w.startCreate('A', 'chat', 'stream', { query: 'plain question' })
    expect(turn.error).to.equal(undefined)
    expect(w.created()[0]!.title).to.equal('plain question')
    expect(displayNames.called).to.equal(false)
    w.s.ai.finish()
  })
})
